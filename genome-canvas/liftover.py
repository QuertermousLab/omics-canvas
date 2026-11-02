"""Genome builds of track files and liftover between builds.

`detect_build()` names the assembly a file was made for from what it says about itself: the chromosome sizes in a
bigWig/bigBed, BAM, VCF or .hic header (chr1 differs between every build), a .hic genome id, or a build in its file or
folder name. It reads a few kilobytes, so it runs on every track load.

`Liftover` converts files (UCSC liftOver and CrossMap, external programs) and pasted regions between the builds it has
chain files for. Converted files are written under the Genome Canvas data folder (never next to the source).
Standard library only, like server.py.
"""

import gzip
import hashlib
import json
import os
import re
import secrets
import shutil
import struct
import subprocess
import tempfile
import threading
import time
import zlib
from datetime import datetime, timezone
from pathlib import Path

# chr1 length -> build (UCSC names); chrX for files without chr1
CHR1_LENGTHS = {
    249250621: "hg19", 248956422: "hg38", 247249719: "hg18", 248387328: "hs1",
    195471971: "mm10", 195154279: "mm39", 197195432: "mm9",
}
CHRX_LENGTHS = {
    155270560: "hg19", 156040895: "hg38", 154913754: "hg18", 154259566: "hs1",
    171031299: "mm10", 169476592: "mm39", 166650296: "mm9",
}
ALIASES = {
    "hg19": "hg19", "grch37": "hg19", "b37": "hg19", "hs37d5": "hg19",
    "hg38": "hg38", "grch38": "hg38",
    "hg18": "hg18", "ncbi36": "hg18",
    "hs1": "hs1", "chm13": "hs1", "t2t": "hs1",
    "mm10": "mm10", "grcm38": "mm10",
    "mm39": "mm39", "grcm39": "mm39",
    "mm9": "mm9", "ncbim37": "mm9",
}
NAME_PATTERN = re.compile(r"(?<![a-z0-9])(hg19|hg38|hg18|grch37|grch38|b37|hs37d5|hs1|chm13|mm10|mm39|mm9|grcm38|grcm39)(?![a-z0-9])", re.I)
HEADER_BYTES = 1 << 20  # the most a header read decompresses


def canonical_build(name):
    """UCSC name of a build ("GRCh38" -> "hg38"); None when unknown."""
    key = re.sub(r"[^a-z0-9]", "", str(name or "").lower().split(".")[0])
    return ALIASES.get(key)


CHROM_NAME = re.compile(r"(?:^|[_|.])(?:chr)?([0-9]+|X|Y)$", re.I)


def _from_lengths(lengths, where):
    # "1", "chr1", and prefixed names such as Cell Ranger's "GRCh38_chr1"
    by_chrom = {}
    for name, size in lengths.items():
        match = CHROM_NAME.search(name)
        if match:
            by_chrom.setdefault(match.group(1).upper(), size)
    for chrom, table in (("1", CHR1_LENGTHS), ("X", CHRX_LENGTHS)):
        build = table.get(by_chrom.get(chrom))
        if build:
            return {"build": build, "evidence": "chr{} is {:,} bp ({})".format(chrom, by_chrom[chrom], where)}
    return None


def _bbi_lengths(handle):
    """Chromosome sizes of a bigWig/bigBed: its chromosome B+ tree, read node by node."""
    head = handle.read(64)
    for order in ("<", ">"):
        magic = struct.unpack(order + "I", head[:4])[0]
        if magic in (0x888FFC26, 0x8789F2EB):
            break
    else:
        return {}
    tree_offset = struct.unpack(order + "Q", head[8:16])[0]
    handle.seek(tree_offset)
    tree = handle.read(32)
    if struct.unpack(order + "I", tree[:4])[0] != 0x78CA8C91:
        return {}
    key_size = struct.unpack(order + "I", tree[8:12])[0]
    lengths, pending, visited = {}, [tree_offset + 32], 0
    while pending and visited < 200:
        handle.seek(pending.pop())
        visited += 1
        leaf, _, count = struct.unpack(order + "BBH", handle.read(4))
        for _ in range(count):
            if leaf:
                key = handle.read(key_size)
                _, size = struct.unpack(order + "II", handle.read(8))
                lengths[key.rstrip(b"\0").decode("latin-1")] = size
            else:
                handle.read(key_size)
                pending.append(struct.unpack(order + "Q", handle.read(8))[0])
        if "chr1" in lengths or "1" in lengths:
            break
    return lengths


def _bgzf_prefix(handle, limit=HEADER_BYTES, enough=lambda data: False):
    """The first decompressed bytes of a (BGZF) gzip file, member after member, read 64 KB at a time until
    `enough(data)` or `limit` bytes."""
    out, raw = bytearray(), b""
    decompressor = zlib.decompressobj(31)
    while len(out) < limit:
        chunk = handle.read(64 << 10)
        if not chunk and not raw:
            break
        raw += chunk
        while raw and len(out) < limit:
            try:
                out += decompressor.decompress(raw)
            except zlib.error:
                return bytes(out)
            raw = decompressor.unused_data
            if decompressor.eof:
                decompressor = zlib.decompressobj(31)
            else:
                break
        if not chunk or enough(out):
            break
    return bytes(out[:limit])


def _bam_header_read(data):
    if len(data) < 12:
        return False
    position = 8 + struct.unpack("<i", data[4:8])[0]
    return len(data) > position + 4 + 64 * 30  # the text and the first references


def _bam_lengths(handle):
    data = _bgzf_prefix(handle, enough=_bam_header_read)
    if data[:4] != b"BAM\1":
        return {}
    text_length = struct.unpack("<i", data[4:8])[0]
    position = 8 + text_length
    if position + 4 > len(data):
        return {}
    count = struct.unpack("<i", data[position:position + 4])[0]
    position += 4
    lengths = {}
    for _ in range(min(count, 5000)):
        if position + 4 > len(data):
            break
        name_length = struct.unpack("<i", data[position:position + 4])[0]
        name = data[position + 4:position + 4 + name_length].rstrip(b"\0").decode("latin-1")
        position += 4 + name_length
        if position + 4 > len(data):
            break
        lengths[name] = struct.unpack("<i", data[position:position + 4])[0]
        position += 4
        if name in ("chr1", "1"):
            break
    return lengths


def _vcf_header(handle, compressed):
    data = _bgzf_prefix(handle, enough=lambda data: b"\n#CHROM" in data) if compressed else handle.read(HEADER_BYTES)
    lengths, reference = {}, ""
    for line in data.decode("latin-1").splitlines():
        if not line.startswith("##"):
            break
        match = re.match(r"##contig=<ID=([^,>]+).*?length=(\d+)", line)
        if match:
            lengths[match.group(1)] = int(match.group(2))
        elif line.startswith(("##reference=", "##assembly=")):
            reference = line.split("=", 1)[1]
    return lengths, reference


def _hic_header(handle):
    data = handle.read(64 << 10)
    if data[:4] != b"HIC\0":
        return "", {}
    version = struct.unpack("<i", data[4:8])[0]
    position = 16
    end = data.index(b"\0", position)
    genome = data[position:end].decode("latin-1")
    position = end + 1
    if version >= 9:
        position += 16
    try:
        attributes = struct.unpack("<i", data[position:position + 4])[0]
        position += 4
        for _ in range(attributes * 2):
            position = data.index(b"\0", position) + 1
        chromosomes = struct.unpack("<i", data[position:position + 4])[0]
        position += 4
        lengths = {}
        for _ in range(min(chromosomes, 500)):
            end = data.index(b"\0", position)
            name = data[position:end].decode("latin-1")
            position = end + 1
            size_format = "<q" if version >= 9 else "<i"
            step = 8 if version >= 9 else 4
            lengths[name] = struct.unpack(size_format, data[position:position + step])[0]
            position += step
        return genome, lengths
    except (ValueError, struct.error):
        return genome, {}


def detect_build(path, file_format):
    """{"build", "evidence"} for a track file, or None. Header evidence wins over the name."""
    path = Path(path)
    found = None
    try:
        with path.open("rb") as handle:
            if file_format in ("bigwig", "bigbed"):
                found = _from_lengths(_bbi_lengths(handle), "file header")
            elif file_format == "bam":
                found = _from_lengths(_bam_lengths(handle), "BAM header")
            elif file_format == "vcf":
                lengths, reference = _vcf_header(handle, path.name.lower().endswith((".gz", ".bgz")))
                found = _from_lengths(lengths, "VCF header")
                match = None if found else NAME_PATTERN.search(reference)
                if match:
                    found = {"build": canonical_build(match.group(1)), "evidence": "VCF header names {}".format(match.group(1))}
            elif file_format == "hic":
                genome, lengths = _hic_header(handle)
                found = _from_lengths(lengths, ".hic header")
                if not found and canonical_build(Path(genome).name):
                    found = {"build": canonical_build(Path(genome).name), "evidence": ".hic genome {}".format(genome)}
    except (OSError, ValueError, struct.error, EOFError):
        found = None
    if found:
        return found
    for part in reversed(path.parts[-3:]):
        match = NAME_PATTERN.search(part)
        if match:
            return {"build": canonical_build(match.group(1)), "evidence": "“{}” in its {}".format(
                match.group(1), "file name" if part == path.name else "folder name")}
    return None


# ---------------------------------------------------------------- liftover

TOOL_DIRS = ("/usr/local/bin", "/usr/bin", str(Path.home() / ".local" / "bin"))
# formats -> how they are converted (CrossMap sub-command, or our own BEDPE handling)
CONVERTIBLE = {
    "bed": "bed", "narrowpeak": "bed", "broadpeak": "bed", "bedmethyl": "bed",
    "bedgraph": "wig", "wig": "wig", "bigwig": "bigwig",
    "bam": "bam", "cram": "bam",
    "gff": "gff", "gff3": "gff", "gtf": "gff",
    "vcf": "vcf", "bedpe": "bedpe",
}
NOT_CONVERTIBLE = {
    "hic": "Hi-C matrices are binned on one build: convert the pairs and rebuild the .hic",
    "bigbed": "convert the source BED instead (bigBed holds no editable text)",
    "gwas": "re-run the GWAS export with positions on the target build",
}


def tool(name):
    found = shutil.which(name)
    if found:
        return found
    for directory in TOOL_DIRS:
        candidate = Path(directory) / name
        if candidate.is_file() and os.access(str(candidate), os.X_OK):
            return str(candidate)
    return None


def _now():
    return datetime.now(timezone.utc).isoformat()


class Liftover:
    """Chains and reference FASTAs from the config; file conversions run in threads (two at a time)."""

    def __init__(self, chains_dir, output_dir, chains=None, fasta=None):
        self.chains_dir = Path(chains_dir)
        self.output_dir = Path(output_dir)
        self.extra_chains = {tuple(key.split(":", 1)): Path(value) for key, value in (chains or {}).items() if ":" in key}
        self.fasta = {canonical_build(key) or key: Path(value) for key, value in (fasta or {}).items()}
        self.jobs = {}
        self.lock = threading.Lock()
        self.slots = threading.Semaphore(2)

    def chain(self, source, target):
        if (source, target) in self.extra_chains:
            return self.extra_chains[(source, target)]
        candidate = self.chains_dir / "{}To{}.over.chain.gz".format(source, target[:1].upper() + target[1:])
        return candidate if candidate.is_file() else None

    def pairs(self):
        builds = sorted(set(ALIASES.values()))
        return [[a, b] for a in builds for b in builds if a != b and self.chain(a, b)]

    def status(self):
        return {
            "available": bool(tool("liftOver") or tool("CrossMap")) and bool(self.pairs()),
            "pairs": self.pairs(),
            "convertible": sorted(CONVERTIBLE),
            "tools": {name: bool(tool(name)) for name in ("liftOver", "CrossMap", "bgzip", "tabix", "samtools", "bcftools")},
        }

    def why_not(self, file_format, source, target):
        """None when a file of this format can be converted from source to target, else the reason."""
        if source == target:
            return "It is already on {}".format(target)
        if not self.chain(source, target):
            return "No chain file for {} → {}".format(source, target)
        if file_format in NOT_CONVERTIBLE:
            return "{} files cannot be lifted over: {}".format(file_format, NOT_CONVERTIBLE[file_format])
        method = CONVERTIBLE.get(file_format)
        if not method:
            return "{} files cannot be lifted over yet".format(file_format)
        if method == "bedpe" and not tool("liftOver"):
            return "liftOver is not installed on the server"
        if method != "bedpe" and not tool("CrossMap"):
            return "CrossMap is not installed on the server"
        if method == "vcf" and not (self.fasta.get(target) and self.fasta[target].is_file()):
            return "VCF liftover needs the {} reference FASTA (liftoverFasta in the config)".format(target)
        return None

    # ------------------------------------------------------------ regions

    def regions(self, text, source, target):
        """Lift pasted regions ("chr1:100-200", "chr1 100 200 name", one per line)."""
        chain = self.chain(source, target)
        if not chain or not tool("liftOver"):
            raise ValueError("No liftover from {} to {} on this server".format(source, target))
        rows = []
        for line in str(text or "").splitlines()[:5000]:
            line = line.strip()
            if not line or line.startswith(("#", "track", "browser")):
                continue
            match = re.match(r"^(\S+?)[:\s]+([\d,]+)\s*[-\s]\s*([\d,]+)(?:\s+(.*))?$", line)
            if not match:
                rows.append({"input": line, "error": "Not a region (chr:start-end or chr start end)"})
                continue
            chrom, start, end = match.group(1), int(match.group(2).replace(",", "")), int(match.group(3).replace(",", ""))
            # chr:start-end is 1-based like a locus; BED columns are 0-based
            zero_based = start - 1 if ":" in line.split()[0] else start
            if end <= zero_based:
                rows.append({"input": line, "error": "End before start"})
                continue
            rows.append({"input": line, "chrom": chrom, "start": max(0, zero_based), "end": end, "name": (match.group(4) or "").strip()})
        regions = [row for row in rows if "chrom" in row]
        if regions:
            with tempfile.TemporaryDirectory() as directory:
                given, mapped, unmapped = (Path(directory) / name for name in ("in.bed", "out.bed", "unmapped.bed"))
                given.write_text("".join("{}\t{}\t{}\tr{}\n".format(r["chrom"], r["start"], r["end"], i) for i, r in enumerate(regions)))
                subprocess.run([tool("liftOver"), "-minMatch=0.1", "-multiple", str(given), str(chain), str(mapped), str(unmapped)],
                               check=True, capture_output=True, timeout=120)
                hits = {}
                for line in mapped.read_text().splitlines():
                    fields = line.split("\t")
                    hits.setdefault(int(fields[3][1:]), []).append((fields[0], int(fields[1]), int(fields[2])))
                reasons, last = {}, ""
                for line in unmapped.read_text().splitlines():
                    if line.startswith("#"):
                        last = line[1:].strip()
                    elif line.strip():
                        reasons[int(line.split("\t")[3][1:])] = last
                for i, row in enumerate(regions):
                    found = hits.get(i, [])
                    row["mapped"] = [{"chrom": c, "start": s, "end": e, "locus": "{}:{:,}-{:,}".format(c, s + 1, e)} for c, s, e in found]
                    if not found:
                        row["error"] = reasons.get(i) or "Not mapped"
        return rows

    # ------------------------------------------------------------ files

    def job(self, job_id):
        with self.lock:
            job = self.jobs.get(job_id)
            return dict(job) if job else None

    def start(self, source_path, file_format, source, target, out_key, private, done_hook=None, label=None):
        """Convert a file in the background; returns its job (already done when converted before)."""
        reason = self.why_not(file_format, source, target)
        if reason:
            raise ValueError(reason)
        source_path = Path(source_path)
        stat = source_path.stat()
        key = hashlib.sha256("{}|{}|{}|{}|{}".format(out_key, stat.st_size, int(stat.st_mtime), source, target).encode()).hexdigest()[:16]
        # a Home folder's file: a folder of its own that nobody can guess, never reused for others
        folder = self.output_dir / "private" / secrets.token_hex(12) if private else self.output_dir / target / key
        with self.lock:
            for job in self.jobs.values():
                if job["key"] == key and job["status"] in ("queued", "running"):
                    return dict(job)
        done = self._finished(self.output_dir / target / key) if not private else None
        job_id = secrets.token_hex(6)
        job = {"id": job_id, "key": key, "status": "queued", "message": "Waiting for a free slot", "source": source,
               "target": target, "format": file_format, "name": label or source_path.name, "output": None, "outputPath": None,
               "createdAt": _now(), "finishedAt": None}
        if done:
            job.update(status="done", message="Converted before", output=done,
                       outputPath=str(self.output_dir / target / key / done), finishedAt=_now())
        with self.lock:
            self.jobs[job_id] = job
            for old in sorted(self.jobs.values(), key=lambda item: item["createdAt"])[:-200]:
                self.jobs.pop(old["id"], None)
        if not done:
            threading.Thread(target=self._run, args=(job_id, source_path, Path(folder), done_hook), daemon=True).start()
        return dict(job)

    @staticmethod
    def _finished(folder):
        try:
            record = json.loads((folder / "liftover.json").read_text())
        except (OSError, ValueError):
            return None
        return record.get("output") if (folder / record.get("output", "")).is_file() else None

    def _update(self, job_id, **values):
        with self.lock:
            self.jobs[job_id].update(values)

    def _run(self, job_id, source_path, folder, done_hook):
        with self.slots:
            job = self.job(job_id)
            self._update(job_id, status="running", message="Converting {} → {}".format(job["source"], job["target"]))
            work = folder.parent / (folder.name + ".part-" + secrets.token_hex(4))
            try:
                work.mkdir(parents=True, exist_ok=True)
                output = self._convert(source_path, job["format"], job["source"], job["target"], work)
                for leftover in work.iterdir():  # CrossMap's .unmap lists and intermediates
                    if not leftover.name.startswith(output):
                        leftover.unlink()
                (work / "liftover.json").write_text(json.dumps({
                    "source": str(source_path), "from": job["source"], "to": job["target"], "format": job["format"],
                    "output": output, "createdAt": _now(),
                }, indent=1))
                if folder.exists():
                    shutil.rmtree(str(folder))
                work.rename(folder)
                self._update(job_id, status="done", message="Converted to {}".format(job["target"]), output=output,
                             outputPath=str(folder / output), finishedAt=_now())
                if done_hook:
                    done_hook(folder / output)
            except Exception as exc:  # noqa: BLE001 - the message goes to the page
                shutil.rmtree(str(work), ignore_errors=True)
                detail = getattr(exc, "stderr", b"") or b""
                detail = detail.decode("utf-8", "replace").strip().splitlines()[-1:] if isinstance(detail, bytes) else []
                self._update(job_id, status="failed", message="{}{}".format(exc, ": " + detail[0] if detail else ""), finishedAt=_now())

    def _convert(self, source_path, file_format, source, target, work):
        """Run the tools; returns the converted file's name inside `work`."""
        chain = str(self.chain(source, target))
        method = CONVERTIBLE[file_format]
        name = source_path.name
        stem = re.sub(r"(\.(bed|narrowpeak|broadpeak|bedmethyl|bedgraph|wig|bigwig|bw|bam|cram|gff3?|gtf|vcf|bedpe|interact))?(\.b?gz)?$", "",
                      name, flags=re.I)
        # "peaks.hg19.bed" -> "peaks.hg38.bed"
        stem = re.sub(r"[._-]({})(?=[._-]|$)".format("|".join(k for k, v in ALIASES.items() if v == source)), "", stem, flags=re.I) or stem
        run = lambda *args: subprocess.run([str(arg) for arg in args], check=True, capture_output=True, timeout=6 * 3600)
        if method == "bigwig" or method == "wig":
            run(tool("CrossMap"), method, chain, source_path, work / "out")
            produced = next((p for p in (work / "out.bw", work / "out.bigWig") if p.is_file()), None)
            if not produced:
                raise RuntimeError("CrossMap wrote no bigWig")
            final = "{}.{}.bw".format(stem, target)
            produced.rename(work / final)
            for leftover in work.glob("out*"):
                leftover.unlink()
            return final
        if method == "bam":
            run(tool("CrossMap"), "bam", chain, source_path, work / "out")
            produced = next((p for p in (work / "out.sorted.bam", work / "out.bam") if p.is_file()), None)
            if not produced:
                raise RuntimeError("CrossMap wrote no BAM")
            final = "{}.{}.bam".format(stem, target)
            produced.rename(work / final)
            if tool("samtools"):
                run(tool("samtools"), "index", work / final)
            for leftover in work.glob("out*"):
                leftover.unlink()
            return final
        if method == "vcf":
            plain = work / "out.vcf"
            run(tool("CrossMap"), "vcf", chain, source_path, self.fasta[target], plain)
            final = "{}.{}.vcf.gz".format(stem, target)
            if tool("bcftools"):
                run(tool("bcftools"), "sort", "-Oz", "-o", work / final, plain)
            else:
                self._sorted_text(plain, work / final[:-3], header="#")
                run(tool("bgzip"), "-f", work / final[:-3])
            run(tool("tabix"), "-f", "-p", "vcf", work / final)
            for leftover in work.glob("out*"):
                leftover.unlink()
            return final
        if method == "gff":
            extension = "gtf" if file_format == "gtf" else "gff3" if file_format == "gff3" else "gff"
            plain = work / "out.{}".format(extension)
            run(tool("CrossMap"), "gff", chain, source_path, plain)
            final = "{}.{}.{}".format(stem, target, extension)
            self._sorted_text(plain, work / final, header="#", start_index=3)
            plain.unlink()
            return self._compress(work, final, "gff")
        if method == "bedpe":
            final = "{}.{}.bedpe".format(stem, target)
            self._bedpe(source_path, chain, work / final)
            return final
        # BED-like: CrossMap keeps every column after the first three
        plain = work / "out.bed"
        run(tool("CrossMap"), "bed", chain, source_path, plain)
        extension = {"narrowpeak": "narrowPeak", "broadpeak": "broadPeak", "bedmethyl": "bedmethyl"}.get(file_format, "bed")
        final = "{}.{}.{}".format(stem, target, extension)
        self._sorted_text(plain, work / final, header="#")
        plain.unlink()
        for leftover in work.glob("out*"):
            leftover.unlink()
        return self._compress(work, final, "bed") if name.lower().endswith((".gz", ".bgz")) else final

    def _compress(self, work, final, preset):
        """bgzip + tabix a large text output (IGV then reads only the region in view)."""
        if (work / final).stat().st_size < (20 << 20) and preset == "gff":
            return final
        if not (tool("bgzip") and tool("tabix")):
            return final
        subprocess.run([tool("bgzip"), "-f", str(work / final)], check=True, capture_output=True, timeout=3600)
        subprocess.run([tool("tabix"), "-f", "-p", preset, str(work / (final + ".gz"))], check=True, capture_output=True, timeout=3600)
        return final + ".gz"

    @staticmethod
    def _sorted_text(source, target, header="#", start_index=1):
        """Sort a coordinate text file by chromosome and start (its 0-based column `start_index`), headers first."""
        heads, rows = [], []
        with open(str(source), encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if line.startswith(header) or line.startswith(("track", "browser")):
                    heads.append(line)
                elif line.strip():
                    fields = line.split("\t", start_index + 1)
                    try:
                        rows.append((fields[0], int(fields[start_index]), line))
                    except (IndexError, ValueError):
                        continue
        rows.sort(key=lambda row: (row[0], row[1]))
        with open(str(target), "w", encoding="utf-8") as handle:
            handle.writelines(heads)
            handle.writelines(row[2] if row[2].endswith("\n") else row[2] + "\n" for row in rows)

    @staticmethod
    def _bedpe(source_path, chain, target):
        """Lift both ends of each pair with liftOver; pairs with an end that does not map are dropped."""
        opener = gzip.open if source_path.name.lower().endswith((".gz", ".bgz")) else open
        lines = []
        with opener(str(source_path), "rt", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                lines.append(line.rstrip("\n"))
        with tempfile.TemporaryDirectory() as directory:
            given = Path(directory) / "ends.bed"
            with given.open("w") as handle:
                for i, line in enumerate(lines):
                    fields = line.split("\t")
                    if line.startswith(("#", "track")) or len(fields) < 6:
                        continue
                    handle.write("{}\t{}\t{}\ta{}\n{}\t{}\t{}\tb{}\n".format(fields[0], fields[1], fields[2], i, fields[3], fields[4], fields[5], i))
            mapped, unmapped = Path(directory) / "out.bed", Path(directory) / "unmapped.bed"
            subprocess.run([tool("liftOver"), str(given), chain, str(mapped), str(unmapped)], check=True, capture_output=True, timeout=3600)
            ends = {}
            for line in mapped.read_text().splitlines():
                fields = line.split("\t")
                ends[fields[3]] = fields[:3]
        with open(str(target), "w", encoding="utf-8") as handle:
            for i, line in enumerate(lines):
                fields = line.split("\t")
                if line.startswith(("#", "track")):
                    handle.write(line + "\n")
                elif "a{}".format(i) in ends and "b{}".format(i) in ends:
                    handle.write("\t".join(ends["a{}".format(i)] + ends["b{}".format(i)] + fields[6:]) + "\n")
