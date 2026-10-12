#!/usr/bin/env python3
"""Discover genome-wide LD-clumped leads and annotate a complete GWAS table."""

import argparse
import bisect
import concurrent.futures
import gzip
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_DIR))
from tools.precompute_gwas_ld import LD_COLUMNS, original_locus
import server

EXTRA_COLUMNS = list(LD_COLUMNS) + ["ld_locus", "ld_reference_hg38", "ld_reference_variant"]
PAIR_COLUMNS = ["chromosome", "position", "ref", "alt", "ld_reference", "ld_r2",
                "ld_reference_locus", "ld_reference_hg38", "ld_population", "ld_locus"]


def variant_key(chromosome, position, first, second):
    if chromosome == "X":
        chromosome = "PAR1" if position <= 2699520 else "PAR2" if position >= 154931044 else "X"
    return "{}:{}:{}:{}".format(chromosome, position, *sorted((first.upper(), second.upper())))


def complement_key(chromosome, position, first, second):
    if len(first) == len(second) == 1 and set(first.upper() + second.upper()).issubset(set("ACGT")):
        table = str.maketrans("ACGT", "TGCA")
        return variant_key(chromosome, position, first.upper().translate(table), second.upper().translate(table))
    return None


def scan_significant(source, threshold):
    candidates = defaultdict(list)
    counts = Counter()
    significant_counts = Counter()
    digest = hashlib.sha256()
    with gzip.open(source, "rb") as handle:
        header = handle.readline()
        digest.update(header)
        columns = header.decode("utf-8").rstrip("\r\n").split("\t")
        required = {"chromosome", "position", "pvalue", "ref", "alt"}
        if not required.issubset(columns) or not {"hg19_locus", "grch37_locus"}.intersection(columns):
            raise ValueError("Input needs chromosome, position, pvalue, alleles, and original GRCh37 coordinates")
        if set(EXTRA_COLUMNS).intersection(columns):
            raise ValueError("Use the original input without LD annotations")
        indexes = {column: index for index, column in enumerate(columns)}
        score_index = indexes.get("neg_log_pvalue")
        minimum = -math.log10(threshold)
        for number, raw in enumerate(handle, 1):
            digest.update(raw)
            fields = raw.decode("utf-8").rstrip("\r\n").split("\t")
            if len(fields) != len(columns):
                raise ValueError("Input row {} does not match the header".format(number + 1))
            counts[fields[indexes["chromosome"]]] += 1
            if number % 2000000 == 0:
                print("Scanned {:,} variants".format(number), flush=True)
            try:
                score = float(fields[score_index]) if score_index is not None else -math.inf
                if not math.isfinite(score):
                    pvalue = float(fields[indexes["pvalue"]])
                    score = -math.log10(pvalue) if 0 < pvalue <= 1 else -math.inf
            except ValueError:
                continue
            if score < minimum:
                continue
            record = dict(zip(columns, fields))
            significant_counts[record["chromosome"]] += 1
            try:
                locus = original_locus(record)
            except ValueError:
                locus = None
            if not locus or record["ref"] in ("", ".") or record["alt"] in ("", ".") or record["ref"] == record["alt"]:
                continue
            # Alternative hg38 mappings are retained in the output, but cannot
            # create a second lead for the same GRCh37 genotype variant.
            if record["chromosome"] != "chr" + locus[0]:
                continue
            record["score"] = score
            record["original_position"] = locus[1]
            record["chrom"] = locus[0]
            candidates[locus[0]].append(record)
    return columns, candidates, dict(counts), dict(significant_counts), digest.hexdigest()


def match_panel_candidates(records, bfile, chromosome):
    by_position = defaultdict(list)
    for record in records:
        by_position[record["original_position"]].append(record)
    hits = defaultdict(dict)
    duplicate_ids = set()
    seen = set()
    with Path(str(bfile) + ".bim").open() as handle:
        for line in handle:
            fields = line.split()
            if len(fields) != 6:
                raise ValueError("Invalid reference BIM row")
            position = int(fields[3])
            if position not in by_position:
                continue
            alt, ref = fields[4].upper(), fields[5].upper()
            if ref == alt or max(len(ref), len(alt)) > 1000:
                continue
            key = variant_key(chromosome, position, ref, alt)
            if key in seen:
                duplicate_ids.add(key)
            seen.add(key)
            for index, record in enumerate(by_position[position]):
                exact = variant_key(chromosome, position, record["ref"], record["alt"]) == key
                complementary = complement_key(chromosome, position, record["ref"], record["alt"]) == key
                if exact or complementary:
                    hits[(position, index)][key] = (exact, fields[1], ref, alt)
    matched = {}
    ambiguous = 0
    for (position, index), variants in hits.items():
        choices = {key: value for key, value in variants.items() if key not in duplicate_ids}
        exact = {key: value for key, value in choices.items() if value[0]}
        choices = exact or choices
        if len(choices) != 1:
            ambiguous += 1
            continue
        key, (_, panel_id, ref, alt) = next(iter(choices.items()))
        record = dict(by_position[position][index])
        record.update({"variant": key, "panel_id": panel_id, "panel_ref": ref, "panel_alt": alt})
        label = record.get("rsid", ".")
        record["label"] = label if label not in ("", ".") else panel_id if panel_id != "." else key
        if key not in matched or record["score"] > matched[key]["score"]:
            matched[key] = record
    return matched, ambiguous


def plink_options(bfile, chromosome, threads, sex_file=None):
    options = ["plink2", "--bfile", str(bfile), "--set-all-var-ids", "@:#:$1:$2",
               "--new-id-max-allele-len", "1000", "missing", "--rm-dup", "exclude-all",
               "--memory", "8192", "--threads", str(threads)]
    if chromosome == "X":
        if not sex_file:
            raise ValueError("chrX requires a sex panel to repair the reference FAM's unknown sexes")
        options += ["--update-sex", str(sex_file), "--split-par", "b37"]
    return options


def run_plink(command, prefix):
    with Path(str(prefix) + ".console.log").open("w") as handle:
        result = subprocess.run(command + ["--out", str(prefix)], stdout=handle, stderr=subprocess.STDOUT, timeout=3600)
    if result.returncode:
        tail = Path(str(prefix) + ".console.log").read_text()[-2500:]
        raise ValueError("PLINK failed for {}:\n{}".format(prefix.name, tail))


def discover_chromosome(chromosome, records, bfile, work, threshold, clump_r2, radius_kb, threads, sex_file):
    matched, ambiguous = match_panel_candidates(records, bfile, chromosome)
    directory = work / ("chr" + chromosome)
    directory.mkdir()
    if not matched:
        return {"chromosome": chromosome, "matchedSignificant": 0, "ambiguous": ambiguous, "leads": [], "bfile": str(bfile)}
    association = directory / "association.tsv"
    extract = directory / "significant.txt"
    with association.open("w") as handle, extract.open("w") as identifiers:
        handle.write("ID\tNEG_LOG10_P\tA1\n")
        for key, record in sorted(matched.items()):
            handle.write("{}\t{:.15g}\t{}\n".format(key, record["score"], record["panel_alt"]))
            identifiers.write(key + "\n")
    prefix = directory / "clump"
    options = plink_options(bfile, chromosome, threads, sex_file)
    run_plink(options + ["--extract", str(extract), "--clump", "cols=chrom,pos,ref,alt1,total,sp2", str(association),
                        "--clump-log10", "--clump-log10-p1", str(-math.log10(threshold)),
                        "--clump-log10-p2", str(-math.log10(threshold)), "--clump-force-a1",
                        "--clump-unphased", "--clump-r2", str(clump_r2), "--clump-kb", str(radius_kb)], prefix)
    leads = []
    report = Path(str(prefix) + ".clumps")
    if not report.is_file():
        raise ValueError("PLINK did not create a clumping report")
    with report.open() as handle:
        columns = handle.readline().lstrip("#").split()
        for line in handle:
            row = dict(zip(columns, line.split()))
            record = dict(matched[row["ID"]])
            record["clumped_significant"] = int(row.get("TOTAL", 0))
            leads.append(record)
    summary = {"chromosome": chromosome, "matchedSignificant": len(matched), "ambiguous": ambiguous,
               "leads": leads, "bfile": str(bfile)}
    print("chr{}: {:,} significant panel variants -> {} leads".format(chromosome, len(matched), len(leads)), flush=True)
    return summary


def group_loci(leads, radius):
    loci = []
    for chromosome in sorted({lead["chrom"] for lead in leads}, key=lambda item: int(item) if item.isdigit() else 23):
        chromosome_leads = sorted((lead for lead in leads if lead["chrom"] == chromosome), key=lambda item: item["original_position"])
        for lead in chromosome_leads:
            start, end = max(1, lead["original_position"] - radius), lead["original_position"] + radius
            if loci and loci[-1]["chrom"] == chromosome and start <= loci[-1]["end"]:
                locus = loci[-1]
                locus["end"] = max(locus["end"], end)
            else:
                locus = {"id": "locus{:04d}".format(len(loci) + 1), "chrom": chromosome,
                         "start": start, "end": end, "leads": [], "variants": 0, "matched": 0,
                         "hg38_extents": {}}
                loci.append(locus)
            locus["leads"].append(lead)
            lead["locus"] = locus["id"]
    return loci


def range_rows(chromosome, start, end):
    if chromosome != "X":
        return [(chromosome, start, end)]
    rows = []
    for code, lower, upper in [("PAR1", 1, 2699520), ("X", 2699521, 154931043), ("PAR2", 154931044, 1000000000)]:
        if max(lower, start) <= min(upper, end):
            rows.append((code, max(lower, start), min(upper, end)))
    return rows


def select_pair(first, second):
    if first is None:
        return second
    def priority(pair):
        return (pair["self"], pair["r2"], pair["lead"]["score"], -pair["distance"], pair["lead"]["variant"])
    return max((first, second), key=priority)


def calculate_chromosome(summary, loci, work, radius_kb, threads, sex_file, label):
    chromosome = summary["chromosome"]
    leads = summary["leads"]
    if not leads:
        return {"chromosome": chromosome, "best": {}, "pairPath": None, "pairs": 0}
    by_id = {lead["variant"]: lead for lead in leads}
    directory = work / ("chr" + chromosome)
    identifiers, intervals = directory / "leads.txt", directory / "intervals.bed"
    identifiers.write_text("\n".join(by_id) + "\n")
    with intervals.open("w") as handle:
        for locus in loci:
            if locus["chrom"] == chromosome:
                for code, start, end in range_rows(chromosome, locus["start"], locus["end"]):
                    handle.write("{}\t{}\t{}\n".format(code, start, end))
    prefix = directory / "ld"
    run_plink(plink_options(summary["bfile"], chromosome, threads, sex_file) + [
        "--extract", "bed1", str(intervals), "--ld-snp-list", str(identifiers),
        "--r2-unphased", "cols=chrom,pos,id,ref,alt1", "--ld-window-kb", str(radius_kb),
        "--ld-window", "99999999", "--ld-window-r2", "0",
    ], prefix)
    report = Path(str(prefix) + ".vcor")
    if not report.is_file():
        raise ValueError("PLINK did not create an LD report")
    best = {}
    pair_path = directory / "pairs.tsv"
    pair_count = 0
    with report.open() as handle, pair_path.open("w") as pairs:
        columns = handle.readline().lstrip("#").rstrip().split("\t")
        indexes = {column: index for index, column in enumerate(columns)}
        for line in handle:
            values = line.rstrip().split("\t")
            r2 = float(values[indexes["UNPHASED_R2"]])
            if not math.isfinite(r2) or not 0 <= r2 <= 1:
                continue
            # PLINK suppresses duplicate lead-lead pairs; record both directions
            # when each member is itself a lead.
            for side, other in [("A", "B"), ("B", "A")]:
                lead = by_id.get(values[indexes["ID_" + side]])
                if lead is None:
                    continue
                position = int(values[indexes["POS_" + other]])
                ref, alt = values[indexes["REF_" + other]], values[indexes["ALT1_" + other]]
                key = variant_key(chromosome, position, ref, alt)
                pair = {"lead": lead, "r2": r2, "self": False, "distance": abs(position - lead["original_position"])}
                best[key] = select_pair(best.get(key), pair)
                pairs.write("\t".join([chromosome, str(position), ref, alt, lead["label"], "{:.8g}".format(r2),
                                        "{}:{}".format(chromosome, lead["original_position"]),
                                        "{}:{}".format(lead["chromosome"], lead["position"]), label, lead["locus"]]) + "\n")
                pair_count += 1
        for lead in leads:
            best[lead["variant"]] = {"lead": lead, "r2": 1.0, "self": True, "distance": 0}
            pairs.write("\t".join([chromosome, str(lead["original_position"]), lead["panel_ref"], lead["panel_alt"],
                                    lead["label"], "1", "{}:{}".format(chromosome, lead["original_position"]),
                                    "{}:{}".format(lead["chromosome"], lead["position"]), label, lead["locus"]]) + "\n")
            pair_count += 1
    print("chr{}: {:,} lead–panel pairs, {:,} annotated panel variants".format(chromosome, pair_count, len(best)), flush=True)
    return {"chromosome": chromosome, "best": best, "pairPath": pair_path, "pairs": pair_count}


def write_sex_file(panel, destination):
    with Path(panel).open() as source, destination.open("w") as output:
        columns = source.readline().split()
        if "sample" not in columns or "gender" not in columns:
            raise ValueError("The sex panel must have sample and gender columns")
        indexes = {name: columns.index(name) for name in ("sample", "gender")}
        output.write("#FID\tIID\tSEX\n")
        for line in source:
            values = line.split()
            sample, gender = (values[indexes[name]] for name in ("sample", "gender"))
            sex = {"male": "1", "female": "2"}.get(gender.lower())
            if not sex:
                raise ValueError("Unknown sex for " + sample)
            output.write("{}\t{}\t{}\n".format(sample, sample, sex))


def bgzip_command(threads):
    binary = shutil.which("bgzip")
    help_result = subprocess.run([binary, "--help"], capture_output=True, text=True)
    # Some servers have an older local binary before a newer system binary.
    if "--threads" not in help_result.stdout + help_result.stderr and Path("/usr/bin/bgzip").is_file():
        binary = "/usr/bin/bgzip"
        help_result = subprocess.run([binary, "--help"], capture_output=True, text=True)
    return [binary, "-c"] + (["-@", str(threads)] if "--threads" in help_result.stdout + help_result.stderr else [])


def write_annotation(source, destination, columns, results, loci, label, radius, threads):
    by_chromosome = {result["chromosome"]: result["best"] for result in results}
    locus_groups = defaultdict(list)
    for locus in loci:
        locus_groups[locus["chrom"]].append(locus)
    starts = {chrom: [locus["start"] for locus in group] for chrom, group in locus_groups.items()}
    stats = Counter()
    digest = hashlib.sha256()
    with gzip.open(source, "rb") as handle, destination.open("wb") as output:
        compressor = subprocess.Popen(bgzip_command(threads), stdin=subprocess.PIPE, stdout=output)
        try:
            header = handle.readline()
            digest.update(header)
            compressor.stdin.write(("\t".join(columns + EXTRA_COLUMNS) + "\n").encode())
            indexes = {column: index for index, column in enumerate(columns)}
            locus_index = indexes.get("hg19_locus", indexes.get("grch37_locus"))
            for number, raw in enumerate(handle, 1):
                digest.update(raw)
                line = raw.rstrip(b"\r\n").decode()
                values = line.split("\t")
                original = values[locus_index]
                if original.startswith("chr"):
                    original = original[3:]
                chromosome, separator, text_position = original.partition(":")
                chromosome = {"23": "X", "24": "Y"}.get(chromosome, chromosome)
                group = locus_groups.get(chromosome, [])
                position = int(text_position) if separator and text_position.isdigit() else 0
                index = bisect.bisect_right(starts.get(chromosome, []), position) - 1
                locus = group[index] if index >= 0 and position <= group[index]["end"] else None
                ref, alt = values[indexes["ref"]], values[indexes["alt"]]
                pair = None
                if locus and ref not in ("", ".") and alt not in ("", ".") and ref != alt:
                    best = by_chromosome.get(chromosome, {})
                    key = variant_key(chromosome, position, ref, alt)
                    pair = best.get(key)
                    if pair is None:
                        alternative = complement_key(chromosome, position, ref, alt)
                        pair = best.get(alternative) if alternative else None
                annotation = ["."] * len(EXTRA_COLUMNS)
                annotation[5] = "0"
                if locus:
                    locus["variants"] += 1
                    stats["inLoci"] += 1
                    hg38_chromosome, hg38_position = values[indexes["chromosome"]], int(values[indexes["position"]])
                    extent = locus["hg38_extents"].setdefault(hg38_chromosome, [hg38_position, hg38_position])
                    extent[0], extent[1] = min(extent[0], hg38_position), max(extent[1], hg38_position)
                    lead = pair["lead"] if pair else min(
                        (lead for lead in locus["leads"] if abs(position - lead["original_position"]) <= radius),
                        key=lambda lead: (abs(position - lead["original_position"]), -lead["score"]), default=None)
                    if lead:
                        is_reference = bool(pair and pair["self"] and hg38_chromosome == lead["chromosome"]
                                            and hg38_position == int(lead["position"]))
                        annotation = ["{:.8g}".format(pair["r2"]) if pair else ".", lead["label"],
                                      "{}:{}".format(chromosome, lead["original_position"]), label,
                                      "unphased dosage r2", "1" if is_reference else "0", locus["id"],
                                      "{}:{}".format(lead["chromosome"], lead["position"]), lead["variant"]]
                        stats["referenceRows"] += is_reference
                    if pair:
                        locus["matched"] += 1
                        stats["matched"] += 1
                compressor.stdin.write((line + "\t" + "\t".join(annotation) + "\n").encode())
                if number % 2000000 == 0:
                    print("Wrote {:,} variants; {:,} with LD".format(number, stats["matched"]), flush=True)
            stats["variants"] = number if 'number' in locals() else 0
            compressor.stdin.close()
            if compressor.wait() != 0:
                raise ValueError("GWAS compression failed")
        finally:
            if compressor.poll() is None:
                compressor.terminate()
                compressor.wait()
    return dict(stats), digest.hexdigest()


def index_table(path, skip=1, chromosome=1, position=2):
    subprocess.run(["tabix", "-S", str(skip), "-s", str(chromosome), "-b", str(position),
                    "-e", str(position), str(path)], check=True, capture_output=True, text=True)


def write_pair_table(results, destination, work, threads):
    sources = [str(result["pairPath"]) for result in results if result["pairPath"]]
    sorted_path = work / "pairs.sorted.tsv"
    with sorted_path.open("wb") as sorted_output:
        subprocess.run(["sort", "--parallel=" + str(threads), "-S", "2G", "-T", str(work),
                        "-t", "\t", "-k1,1", "-k2,2n", *sources], stdout=sorted_output,
                       check=True, env={**os.environ, "LC_ALL": "C"})
    with destination.open("wb") as output:
        compressor = subprocess.Popen(bgzip_command(threads), stdin=subprocess.PIPE, stdout=output)
        try:
            compressor.stdin.write(("\t".join(PAIR_COLUMNS) + "\n").encode())
            with sorted_path.open("rb") as handle:
                shutil.copyfileobj(handle, compressor.stdin, length=1024 * 1024)
            compressor.stdin.close()
            if compressor.wait() != 0:
                raise ValueError("LD pair compression failed")
        finally:
            if compressor.poll() is None:
                compressor.terminate()
                compressor.wait()
    index_table(destination)


def write_catalogs(leads, loci, lead_path, locus_path):
    with lead_path.open("w") as handle:
        handle.write("chromosome\tposition\tlead\tneg_log_pvalue\tref\talt\thg19_locus\tlocus\tpanel_variant\tclumped_significant\n")
        for lead in sorted(leads, key=lambda item: (item["chromosome"], int(item["position"]))):
            handle.write("\t".join([lead["chromosome"], lead["position"], lead["label"], str(lead["score"]),
                                    lead["ref"], lead["alt"], "{}:{}".format(lead["chrom"], lead["original_position"]),
                                    lead["locus"], lead["variant"], str(lead["clumped_significant"])]) + "\n")
    with locus_path.open("w") as handle:
        handle.write("locus\thg19_interval\thg38_intervals\tleads\tprimary_lead\tvariants\tmatched\n")
        for locus in loci:
            primary = max(locus["leads"], key=lambda lead: lead["score"])
            extents = ";".join("{}:{}-{}".format(chrom, *bounds) for chrom, bounds in sorted(locus["hg38_extents"].items()))
            handle.write("\t".join([locus["id"], "{}:{}-{}".format(locus["chrom"], locus["start"], locus["end"]),
                                    extents, str(len(locus["leads"])), primary["label"],
                                    str(locus["variants"]), str(locus["matched"])]) + "\n")


def prepare_genomewide(source, output, threshold=5e-8, clump_r2=0.1, radius_kb=1000,
                       workers=4, threads=2, bfile_x=None, sex_panel=None):
    source, output = Path(source).resolve(), Path(output).absolute()
    suffixes = ["", ".tbi", ".ld.json", ".leads.tsv", ".loci.tsv", ".ld-pairs.hg19.tsv.gz", ".ld-pairs.hg19.tsv.gz.tbi"]
    targets = [Path(str(output) + suffix) for suffix in suffixes]
    if source == output.resolve() or not str(output).endswith(".gwas.gz"):
        raise ValueError("Use a new .gwas.gz output, never the original input")
    if any(target.exists() or target.is_symlink() for target in targets):
        raise FileExistsError("An output or companion file already exists")
    if not source.is_file() or not output.parent.is_dir():
        raise ValueError("The source and output directory must exist")
    for program in ("plink2", "tabix", "bgzip", "sort"):
        if not shutil.which(program):
            raise ValueError(program + " was not found")
    settings = server.ld_reference_settings()
    if not settings:
        raise ValueError("Configure ldReferenceHg19 before computing LD")
    label = settings.get("label", "1000 Genomes Phase 3 ALL")
    started = time.perf_counter()
    source_stat = source.stat()
    print("[1/5] Scanning the complete GWAS input for significant variants...", flush=True)
    columns, candidates, counts, significant, source_digest = scan_significant(source, threshold)
    print("Scanned {:,} variants; {:,} significant; {:,} compatible candidates".format(
        sum(counts.values()), sum(significant.values()), sum(map(len, candidates.values()))), flush=True)
    unavailable = []
    jobs = []
    for chromosome, records in candidates.items():
        bfile = Path(bfile_x) if chromosome == "X" and bfile_x else server.formatted_ld_path(settings.get("bfileTemplate"), chromosome)
        if not bfile or not all(Path(str(bfile) + suffix).is_file() for suffix in (".bed", ".bim", ".fam")):
            unavailable.append({"chromosome": chromosome, "significantCandidates": len(records), "reason": "No local panel"})
        else:
            jobs.append((chromosome, records, bfile))
    if not jobs:
        raise ValueError("No compatible chromosomes have a usable local panel")
    with tempfile.TemporaryDirectory(prefix="genome-canvas-genomewide-ld-") as temporary:
        work = Path(temporary)
        sex_file = None
        if any(chromosome == "X" for chromosome, _, _ in jobs):
            if not sex_panel:
                raise ValueError("--sex-panel is required with the chrX reference")
            sex_file = work / "sex.tsv"
            write_sex_file(sex_panel, sex_file)
        print("[2/5] LD clumping on {} chromosomes...".format(len(jobs)), flush=True)
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(discover_chromosome, chrom, records, bfile, work, threshold,
                                       clump_r2, radius_kb, threads, sex_file) for chrom, records, bfile in jobs]
            summaries = [future.result() for future in concurrent.futures.as_completed(futures)]
        leads = [lead for summary in summaries for lead in summary["leads"]]
        if not leads:
            raise ValueError("No LD-clumped leads were identified")
        loci = group_loci(leads, radius_kb * 1000)
        print("Identified {} leads in {} merged loci".format(len(leads), len(loci)), flush=True)
        print("[3/5] Computing lead–variant LD across all discovered windows...", flush=True)
        # Parsing large LD reports is CPU-bound Python work; separate processes
        # avoid serializing chromosome jobs behind the interpreter's GIL.
        with concurrent.futures.ProcessPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(calculate_chromosome, summary, loci, work, radius_kb, threads, sex_file, label)
                       for summary in summaries]
            results = [future.result() for future in concurrent.futures.as_completed(futures)]
        with tempfile.TemporaryDirectory(prefix=".genome-canvas-genomewide-", dir=output.parent) as staged_directory:
            staged = Path(staged_directory) / output.name
            print("[4/5] Writing the complete annotated table and preserving every original row...", flush=True)
            stats, verified_digest = write_annotation(source, staged, columns, results, loci, label, radius_kb * 1000, threads * workers)
            if verified_digest != source_digest or source.stat().st_mtime_ns != source_stat.st_mtime_ns:
                raise ValueError("The original file changed during processing")
            if stats["variants"] != sum(counts.values()) or stats["referenceRows"] != len(leads):
                raise ValueError("Original row count or lead-reference count did not validate")
            index_table(staged, chromosome=columns.index("chromosome") + 1, position=columns.index("position") + 1)
            print("[5/5] Saving complete lead-pair data, lead/locus catalogs, and provenance...", flush=True)
            write_pair_table(results, Path(str(staged) + ".ld-pairs.hg19.tsv.gz"), work, threads * workers)
            write_catalogs(leads, loci, Path(str(staged) + ".leads.tsv"), Path(str(staged) + ".loci.tsv"))
            metadata = {
                "version": 1, "createdAt": datetime.now(timezone.utc).isoformat(), "scope": "complete GWAS input",
                "source": str(source), "sourceBytes": source_stat.st_size, "sourceModifiedNs": source_stat.st_mtime_ns,
                "sourceUncompressedSHA256": source_digest, "allOriginalRowsPreserved": True,
                "parameters": {"pThreshold": threshold, "clumpR2": clump_r2, "radiusKb": radius_kb,
                               "clumping": "PLINK2 --clump-unphased", "ldMethod": "PLINK2 --r2-unphased",
                               "annotationRule": "self lead first; otherwise highest r2; ties by P, distance, variant ID",
                               "locusRule": "merge overlapping lead windows in GRCh37", "population": label,
                               "referenceBuild": "GRCh37", "summaryBuild": "GRCh38", "pairsBuild": "GRCh37",
                               "workers": workers, "threadsPerWorker": threads, "memoryMiBPerWorker": 8192,
                               "chrXSexPanel": str(sex_panel) if sex_panel else None, "chrXPARBuild": "b37" if bfile_x else None},
                "variantsByChromosome": counts, "significantByChromosome": significant,
                "leads": leads, "loci": loci, "unavailableChromosomes": unavailable,
                "matchedSignificant": sum(summary["matchedSignificant"] for summary in summaries),
                "ambiguousSignificant": sum(summary["ambiguous"] for summary in summaries),
                "referencePanels": {summary["chromosome"]: summary["bfile"] for summary in summaries},
                "pairCount": sum(result["pairs"] for result in results), "statistics": stats,
                "totalSeconds": round(time.perf_counter() - started, 3),
            }
            Path(str(staged) + ".ld.json").write_text(json.dumps(metadata, indent=2) + "\n")
            published = []
            try:
                for suffix, target in list(zip(suffixes, targets))[1:] + [("", targets[0])]:
                    os.link(Path(str(staged) + suffix), target)
                    published.append(target)
            except OSError:
                for target in published:
                    target.unlink()
                raise
    print("Ready: {} — {} leads, {} loci, {:,} LD-annotated / {:,} total variants".format(
        output, len(leads), len(loci), stats["matched"], stats["variants"]), flush=True)
    return metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--config", type=Path, default=APP_DIR / "genomecanvas.config.json")
    parser.add_argument("--p-threshold", type=float, default=5e-8)
    parser.add_argument("--clump-r2", type=float, default=0.1)
    parser.add_argument("--radius-kb", type=int, default=1000)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument("--bfile-x", type=Path)
    parser.add_argument("--sex-panel", type=Path, help="1000G sample/pop/super_pop/gender panel")
    args = parser.parse_args()
    if not 0 < args.p_threshold <= 1 or not 0 <= args.clump_r2 <= 1 or not 1 <= args.radius_kb <= 5000:
        parser.error("Invalid significance, clumping, or window parameters")
    if not 1 <= args.workers <= 16 or not 1 <= args.threads <= 16:
        parser.error("--workers and --threads must be between 1 and 16")
    try:
        server.SETTINGS = json.loads(args.config.read_text())
        prepare_genomewide(args.input, args.output, args.p_threshold, args.clump_r2, args.radius_kb,
                           args.workers, args.threads, args.bfile_x, args.sex_panel)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        parser.exit(1, "Error: {}\n".format(exc))


if __name__ == "__main__":
    main()
