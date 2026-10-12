#!/usr/bin/env python3
"""Write indexed regional GWAS tracks with fixed-lead, local-panel LD annotations."""

import argparse
import gzip
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_DIR))
import server

LD_COLUMNS = (
    "ld_r2", "ld_reference", "ld_reference_locus", "ld_population",
    "ld_method", "ld_is_reference",
)


def parse_region(value):
    match = re.fullmatch(r"((?:chr)?(?:[0-9]+|X|Y)):([0-9,]+)-([0-9,]+)", value, re.I)
    if not match:
        raise ValueError("Expected a region such as chr9:21737208-22275000")
    chrom = "chr" + server.normalized_ld_chromosome(match[1])
    start, end = (int(part.replace(",", "")) for part in match.groups()[1:])
    if start < 1 or end < start:
        raise ValueError("Invalid region coordinates")
    return chrom, start, end


def original_locus(record):
    value = record.get("hg19_locus") or record.get("grch37_locus") or ""
    match = re.fullmatch(r"(?:chr)?([0-9]+|X|Y):([0-9]+)", value, re.I)
    if not match:
        return None
    chromosome = {"23": "X", "24": "Y"}.get(match[1], match[1])
    return server.normalized_ld_chromosome(chromosome), int(match[2])


def association_score(record):
    try:
        value = float(record.get("neg_log_pvalue", "nan"))
        if math.isfinite(value):
            return value
        pvalue = float(record.get("pvalue", "nan"))
        return -math.log10(pvalue) if 0 < pvalue <= 1 else -math.inf
    except ValueError:
        return -math.inf


def choose_lead(records, identifier=None):
    candidates = [record for record in records if original_locus(record) and record.get("ref") and record.get("alt")]
    if identifier:
        candidates = [record for record in candidates if record.get("rsid") == identifier]
    if not candidates:
        raise ValueError("No compatible lead SNP with GRCh37 coordinates and alleles was found")
    lead = max(candidates, key=association_score)
    if not math.isfinite(association_score(lead)):
        raise ValueError("The lead SNP has no valid association P value")
    return lead


def annotate_records(records, payload):
    reference = payload["reference"]
    by_position = {}
    for variant in payload["variants"]:
        by_position.setdefault(variant["position"], []).append(variant)
    matched = 0
    for record in records:
        locus = original_locus(record)
        ref, alt = record.get("ref", ""), record.get("alt", "")
        compatible = locus and locus[0] == payload["chromosome"] and ref and alt and ref != alt
        match = next((variant for variant in by_position.get(locus[1], [])
                      if server.alleles_match(ref, alt, variant["ref"], variant["alt"])), None) if compatible else None
        is_reference = bool(compatible and locus[1] == reference["position"]
                            and server.alleles_match(ref, alt, reference["ref"], reference["alt"]))
        r2 = 1.0 if is_reference else match["r2"] if match else None
        if r2 is not None and not math.isfinite(r2):
            r2 = None
        matched += r2 is not None
        record.update({
            "ld_r2": "{:.8g}".format(r2) if r2 is not None else ".",
            "ld_reference": reference["id"],
            "ld_reference_locus": "{}:{}".format(payload["chromosome"], reference["position"]),
            "ld_population": payload["label"],
            "ld_method": "unphased dosage r2",
            "ld_is_reference": "1" if is_reference else "0",
        })
    return matched


def load_region(source, columns, region):
    result = subprocess.run(["tabix", str(source), region], check=True, capture_output=True, text=True, timeout=60)
    records = []
    for line in result.stdout.splitlines():
        values = line.split("\t")
        if len(values) != len(columns):
            raise ValueError("A GWAS row does not match its header")
        records.append(dict(zip(columns, values)))
    if not records:
        raise ValueError("No GWAS variants were found in " + region)
    return records


def prepare(source, output, regions, identifier=None, threads=4):
    source, output = Path(source).resolve(), Path(output).absolute()
    targets = [output, Path(str(output) + ".tbi"), Path(str(output) + ".ld.json")]
    if not str(output).endswith(".gwas.gz") or source == output.resolve():
        raise ValueError("Use a new .gwas.gz output; the source file cannot be overwritten")
    if any(target.exists() or target.is_symlink() for target in targets):
        raise FileExistsError("An output or companion file already exists")
    if not source.is_file() or not any(Path(str(source) + suffix).is_file() for suffix in (".tbi", ".csi")):
        raise ValueError("The input must be a readable, Tabix-indexed GWAS file")
    if not output.parent.is_dir():
        raise ValueError("The output directory does not exist")
    if identifier and len(regions) != 1:
        raise ValueError("--lead can only be used with a single region")
    parsed = sorted(set(parse_region(region) for region in regions))
    for previous, current in zip(parsed, parsed[1:]):
        if previous[0] == current[0] and current[1] <= previous[2]:
            raise ValueError("Precomputed regions must not overlap")
    with gzip.open(source, "rt", encoding="utf-8") as handle:
        columns = handle.readline().rstrip("\r\n").split("\t")
    if not {"chromosome", "position", "pvalue", "ref", "alt"}.issubset(columns):
        raise ValueError("Required columns: chromosome, position, pvalue, ref, alt")
    if not {"hg19_locus", "grch37_locus"}.intersection(columns):
        raise ValueError("The input must retain hg19_locus or grch37_locus")
    if set(LD_COLUMNS).intersection(columns):
        raise ValueError("The input already contains LD annotations; use the original file")
    output_columns = columns + list(LD_COLUMNS)
    started = time.perf_counter()
    annotated = []
    summaries = []
    for chrom, start, end in parsed:
        region = "{}:{}-{}".format(chrom, start, end)
        records = load_region(source, columns, region)
        lead = choose_lead(records, identifier)
        chromosome, position = original_locus(lead)
        positions = [original_locus(record)[1] for record in records
                     if original_locus(record) and original_locus(record)[0] == chromosome]
        calculation_start = time.perf_counter()
        payload = server.calculate_local_ld(chromosome, min(positions), max(positions), position, lead["ref"], lead["alt"])
        calculation_seconds = time.perf_counter() - calculation_start
        if not payload["available"]:
            raise ValueError(region + ": " + payload.get("reason", "LD calculation failed"))
        matched = annotate_records(records, payload)
        annotated.extend(records)
        summary = {
            "region": region, "reference": payload["reference"], "population": payload["label"],
            "referenceBuild": payload["build"], "referenceChromosome": chromosome,
            "method": "PLINK2 --r2-unphased", "variants": len(records), "matched": matched,
            "calculationSeconds": round(calculation_seconds, 4),
        }
        summaries.append(summary)
        print(json.dumps(summary), flush=True)
    metadata = {
        "version": 1, "createdAt": datetime.now(timezone.utc).isoformat(), "source": str(source),
        "sourceBytes": source.stat().st_size, "sourceModifiedNs": source.stat().st_mtime_ns,
        "scope": "selected regions only", "fixedReference": True, "regions": summaries,
    }
    bgzip = shutil.which("bgzip")
    if not bgzip:
        raise ValueError("bgzip was not found")
    command = [bgzip, "-c"]
    help_text = subprocess.run([bgzip, "--help"], capture_output=True, text=True)
    if "--threads" in help_text.stdout + help_text.stderr:
        command.extend(["-@", str(threads)])
    with tempfile.TemporaryDirectory(prefix=".genome-canvas-ld-", dir=output.parent) as temporary:
        staged = Path(temporary) / output.name
        with staged.open("wb") as destination:
            compressor = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=destination, text=True, encoding="utf-8")
            try:
                compressor.stdin.write("\t".join(output_columns) + "\n")
                for record in annotated:
                    compressor.stdin.write("\t".join(record[column] for column in output_columns) + "\n")
                compressor.stdin.close()
                if compressor.wait() != 0:
                    raise ValueError("BGZF compression failed")
            finally:
                if compressor.poll() is None:
                    compressor.terminate()
                    compressor.wait()
        subprocess.run(["tabix", "-S", "1", "-s", str(columns.index("chromosome") + 1),
                        "-b", str(columns.index("position") + 1), "-e", str(columns.index("position") + 1),
                        str(staged)], check=True, capture_output=True, text=True)
        staged_metadata = Path(str(staged) + ".ld.json")
        staged_metadata.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        # Publish the index first, then data, with no-clobber hard links on the
        # same filesystem. Only links created by this invocation are removed on failure.
        published = []
        try:
            for staged_path, target in [(Path(str(staged) + ".tbi"), targets[1]),
                                        (staged_metadata, targets[2]), (staged, targets[0])]:
                os.link(staged_path, target)
                published.append(target)
        except OSError:
            for target in published:
                target.unlink()
            raise
    metadata["totalSeconds"] = round(time.perf_counter() - started, 4)
    print("Ready: {} ({} variants, {} matched; {:.3f} seconds)".format(
        output, len(annotated), sum(item["matched"] for item in summaries), metadata["totalSeconds"]), flush=True)
    return metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--region", required=True, action="append", help="hg38 region; may be repeated without overlap")
    parser.add_argument("--lead", help="Fixed lead rsID; default: strongest association in the region")
    parser.add_argument("--config", type=Path, default=APP_DIR / "genomecanvas.config.json")
    parser.add_argument("--threads", type=int, default=4)
    args = parser.parse_args()
    if not 1 <= args.threads <= 32:
        parser.error("--threads must be between 1 and 32")
    try:
        server.SETTINGS = json.loads(args.config.read_text(encoding="utf-8"))
        prepare(args.input, args.output, args.region, args.lead, args.threads)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        parser.exit(1, "Error: {}\n".format(exc))


if __name__ == "__main__":
    main()
