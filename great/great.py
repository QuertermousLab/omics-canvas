"""GREAT for Omics Canvas: functional enrichment of genomic regions (McLean et al. 2010, Nat Biotechnol; GREAT 4.0.4).

great.stanford.edu is no longer served; this is a reimplementation that follows GREAT's published core code
(greatTools: createRegulatoryDomains.c, calculateBinomialP.c) and documentation:

- Genes: GREAT 4.0.4's gene sets (one canonical TSS per gene) and curated regulatory domains, per assembly
  (tools/fetch_great_data.py builds data/<assembly>/).
- Regulatory domains: basal plus extension (5 kb up, 1 kb down, extended up to 1 Mb to the nearest basal domains),
  two nearest genes, or single nearest gene; curated domains replace the computed ones of their genes.
- A region is associated with every gene whose regulatory domain holds its midpoint (or its summit, an option).
- Binomial test over regions: p = the non-gap fraction of the genome in the term's genes' domains; P(X >= k) for
  X ~ Bin(n regions, p), with GREAT's incomplete-beta code. Hypergeometric test over genes: the genes hit by one or
  more regions against all genes. With a background region set: a hypergeometric test over regions.
- Multiple testing: Bonferroni (m * p) and GREAT's FDR q (m * p / rank); m = the terms tested in the ontology.

Input is read flexibly (read_regions): BED3 to BED12, narrowPeak/broadPeak, bedGraph, CSV/TSV tables with a header
(chrom/seqnames, start, end ... in any column), MACS .xls, region strings (chr1:100-200, chr1-100-200), compressed
files; Ensembl chromosome names and contigs outside the assembly are reported, not fatal.

Runs as a worker process (python3 great.py --worker <data dir>), driven by great_service.py; standard library only.
"""

import bz2
import csv
import gzip
import io
import json
import lzma
import math
import re
import sys
import zipfile
from array import array
from bisect import bisect_right
from collections import OrderedDict, defaultdict
from pathlib import Path

GREAT_VERSION = "4.0.4"
ASSEMBLIES = ("hg38", "hg19", "mm10", "mm9")
SPECIES = {"hg38": "human", "hg19": "human", "mm10": "mouse", "mm9": "mouse"}
RULES = ("basalPlusExt", "twoClosest", "oneClosest")
RULE_NAMES = {"basalPlusExt": "Basal plus extension", "twoClosest": "Two nearest genes", "oneClosest": "Single nearest gene"}
DEFAULT_PARAMS = {"rule": "basalPlusExt", "upstream": 5000, "downstream": 1000, "extension": 1000000,
                  "twoDistance": 1000000, "oneDistance": 1000000, "curated": True, "anchor": "midpoint",
                  "coordinates": "auto"}
# Region-gene distance bins of GREAT's association graphs (kb): [0, 5], (5, 50], (50, 500], > 500
DISTANCE_BINS = (5000, 50000, 500000)


class InputError(ValueError):
    """A problem with the user's input, reported to them as is."""


# ---------------------------------------------------------------- statistics (calculateBinomialP.c)

MAXIT = 10000
EPS = 3.0e-7
FPMIN = 1.0e-30


def betacf(a, b, x):
    """Continued fraction of the incomplete beta function (modified Lentz), as in GREAT's calculateBinomialP.c."""
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < FPMIN:
        d = FPMIN
    d = 1.0 / d
    h = d
    for m in range(1, MAXIT + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < FPMIN:
            d = FPMIN
        c = 1.0 + aa / c
        if abs(c) < FPMIN:
            c = FPMIN
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < FPMIN:
            d = FPMIN
        c = 1.0 + aa / c
        if abs(c) < FPMIN:
            c = FPMIN
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < EPS:
            break
    return h


def betai(a, b, x):
    """Regularized incomplete beta function I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    bt = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log(1.0 - x))
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * betacf(a, b, x) / a
    return 1.0 - bt * betacf(b, a, 1.0 - x) / b


def binomial_upper(n, k, p):
    """P(X >= k) for X ~ Binomial(n, p): GREAT's getBinomPval."""
    if k <= 0:
        return 1.0
    return min(1.0, max(0.0, betai(k, n - k + 1, p)))


def _log_choose(n, k):
    return math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)


def hypergeometric_upper(total, successes, draws, k):
    """P(X >= k) for X ~ Hypergeometric(population total, successes in it, draws)."""
    if k <= 0:
        return 1.0
    high = min(successes, draws)
    low = max(0, draws - (total - successes))
    if k > high:
        return 0.0
    if k <= low:
        return 1.0

    def log_pmf(i):
        return _log_choose(successes, i) + _log_choose(total - successes, draws - i) - _log_choose(total, draws)

    mode = (draws + 1) * (successes + 1) // (total + 2)
    if k > mode:  # the upper tail decreases from k on
        term, tail, i = 1.0, 1.0, k
        while i < high:
            term *= (successes - i) * (draws - i) / ((i + 1.0) * (total - successes - draws + i + 1.0))
            tail += term
            i += 1
            if term < tail * 1e-17:
                break
        return min(1.0, math.exp(log_pmf(k)) * tail)
    term, tail, i = 1.0, 1.0, k - 1  # 1 - the lower tail, summed down from k - 1
    while i > low:
        term *= i * (total - successes - draws + i) / ((successes - i + 1.0) * (draws - i + 1.0))
        tail += term
        i -= 1
        if term < tail * 1e-17:
            break
    return min(1.0, max(0.0, 1.0 - math.exp(log_pmf(k - 1)) * tail))


def corrections(pvalues):
    """GREAT's ranks, Bonferroni p (m * p) and FDR q (m * p / rank), each capped at 1."""
    m = len(pvalues)
    order = sorted(range(m), key=lambda i: pvalues[i])
    ranks, bonferroni, fdr = [0] * m, [1.0] * m, [1.0] * m
    for rank, i in enumerate(order, 1):
        ranks[i] = rank
        bonferroni[i] = min(1.0, pvalues[i] * m)
        fdr[i] = min(1.0, pvalues[i] * m / rank)
    return ranks, bonferroni, fdr


# ---------------------------------------------------------------- reading regions

CHROM_COLUMNS = ("chrom", "chr", "chromosome", "seqnames", "seqname", "seqid", "contig", "chromosome_name", "chr_name",
                 "chrom_name", "chromosome name", "space")
START_COLUMNS = ("start", "chromstart", "chrom_start", "begin", "from", "start_position", "txstart", "peak_start",
                 "region_start")
END_COLUMNS = ("end", "chromend", "chrom_end", "stop", "to", "end_position", "txend", "peak_end", "region_end")
POSITION_COLUMNS = ("pos", "position", "bp", "base_pair_location", "snp_pos", "summit_position")
NAME_COLUMNS = ("name", "id", "peak", "peak_id", "peakid", "peak_name", "region_id", "region_name", "feature", "interval")
REGION_COLUMNS = ("region", "regions", "locus", "loci", "coordinates", "coordinate", "interval", "peak", "peak_id",
                  "location", "range")
SUMMIT_COLUMNS = ("abs_summit", "summit")
REGION_STRING = re.compile(r"^\s*([A-Za-z0-9_.]+?)\s*[:\-_\s]\s*([0-9][0-9,]*)\s*[-_\s]\s*([0-9][0-9,]*)\s*(\([+-]\))?\s*$")
MAX_ISSUE_EXAMPLES = 5


def decompress(data, maximum_bytes):
    """Bytes of an upload or file: gzip/bgzip, bzip2, xz or the first file of a zip are opened."""
    if data[:2] == b"\x1f\x8b":
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as handle:
            data = handle.read(maximum_bytes + 1)
    elif data[:3] == b"BZh":
        data = bz2.decompress(data)
    elif data[:6] == b"\xfd7zXZ\x00":
        data = lzma.decompress(data)
    elif data[:4] == b"PK\x03\x04":
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = [n for n in archive.namelist() if not n.endswith("/") and not n.startswith("__MACOSX")]
            if not names:
                raise InputError("The zip file is empty")
            with archive.open(names[0]) as handle:
                data = handle.read(maximum_bytes + 1)
    if len(data) > maximum_bytes:
        raise InputError("The regions file is larger than {:,} MB once decompressed".format(maximum_bytes // 1048576))
    return data


def normalize_chromosome(name, valid, cache):
    """'1' -> 'chr1', 'MT' -> 'chrM', 'chrx' -> 'chrX', PLINK's 23/24 -> chrX/chrY; None if not in the assembly."""
    if name in cache:
        return cache[name]
    raw = name.strip().strip('"\'')
    found = raw if raw in valid else None
    if found is None:
        core = raw[3:] if raw.lower().startswith("chr") else raw
        if core.isdigit():
            core = str(int(core))
        elif core.upper() in ("X", "Y", "M", "MT"):
            core = "M" if core.upper() in ("M", "MT") else core.upper()
        candidate = "chr" + core
        if candidate in valid:
            found = candidate
        elif core in ("23", "24", "25", "26"):
            plink = {"23": "chrX", "24": "chrY", "26": "chrM"}.get(core)
            found = plink if plink in valid and "chr23" not in valid else None
    cache[name] = found
    return found


def parse_int(text):
    text = text.strip().strip('"\'').replace(",", "")
    if not text:
        raise ValueError("empty")
    try:
        return int(text)
    except ValueError:
        value = float(text)  # R writes 1e+05
        if not math.isfinite(value) or value != int(value):
            raise ValueError(text)
        return int(value)


def is_int(text):
    try:
        parse_int(text)
        return True
    except ValueError:
        return False


def is_number(text):
    try:
        float(text.strip().replace(",", ""))
        return True
    except ValueError:
        return False


class RegionReport:
    """What read_regions did with the input, shown to the user."""

    def __init__(self):
        self.lines = 0
        self.issues = OrderedDict()
        self.renamed = OrderedDict()
        self.notes = []

    def issue(self, kind, line_number, text):
        entry = self.issues.setdefault(kind, {"count": 0, "examples": []})
        entry["count"] += 1
        if len(entry["examples"]) < MAX_ISSUE_EXAMPLES:
            entry["examples"].append("line {}: {}".format(line_number, text[:160]))


ISSUE_TEXT = {
    "unknownChromosome": "chromosome not in the assembly (skipped)",
    "badCoordinates": "coordinates that are not whole numbers (skipped)",
    "negative": "negative coordinates (skipped)",
    "beyondEnd": "start past the chromosome's end (skipped)",
    "clipped": "end past the chromosome's end (clipped to it)",
    "swapped": "start after end (swapped)",
    "tooFewColumns": "too few columns (skipped)",
}


def _split_lines(text):
    for number, line in enumerate(text.splitlines(), 1):
        yield number, line


def _header_columns(fields):
    names = [f.strip().strip('"\'').lstrip("#").strip().lower() for f in fields]
    return names


def _find(names, candidates):
    for candidate in candidates:
        if candidate in names:
            return names.index(candidate)
    return None


def _mostly(values):
    """True when at least 80% of the values are true (a few malformed lines do not change the layout)."""
    values = [bool(v) for v in values]
    return bool(values) and sum(values) >= 0.8 * len(values)


def _detect_delimiter(samples):
    tabs = sum(1 for s in samples if "\t" in s)
    commas = sum(1 for s in samples if "," in s and "\t" not in s)
    semicolons = sum(1 for s in samples if ";" in s and "\t" not in s and "," not in s)
    if tabs >= max(1, len(samples) // 2):
        return "\t"
    if commas >= max(1, len(samples) // 2):
        return ","
    if semicolons >= max(1, len(samples) // 2):
        return ";"
    return None  # any whitespace


def _split(line, delimiter):
    if delimiter is None:
        return line.split()
    if delimiter == ",":
        return next(csv.reader([line]))
    return [f.strip() for f in line.split(delimiter)]


def read_regions(data, sizes, coordinates="auto", maximum_bytes=200 * 1048576, maximum_regions=None):
    """(regions, report) from the bytes of a regions file: regions are (chrom, start, end, name, summit or None),
    0-based half-open; report is a dict for the user."""
    data = decompress(data, maximum_bytes)
    text = data.decode("utf-8-sig", errors="replace")
    valid = set(sizes)
    report = RegionReport()
    cache = {}

    # Look at the first lines that are not comments to choose a layout.
    header, samples, header_line = None, [], 0
    for number, line in _split_lines(text):
        stripped = line.strip()
        if not stripped or stripped.startswith(("track", "browser")):
            continue
        if stripped.startswith("#"):
            names = _header_columns(re.split(r"[\t,]|\s+", stripped.lstrip("#").strip()))
            if _find(names, CHROM_COLUMNS) is not None and (_find(names, START_COLUMNS) is not None
                                                           or _find(names, POSITION_COLUMNS) is not None):
                header, header_line = stripped.lstrip("#").strip(), number
            continue
        samples.append(stripped)
        if len(samples) >= 50:
            break
    if not samples:
        raise InputError("No regions found: the input is empty or holds only comment lines")
    if _mostly(REGION_STRING.match(re.split(r"\t|\s+", sample)[0]) for sample in samples):
        delimiter = "\t" if _mostly("\t" in sample for sample in samples) else None  # "chr1:1,000-2,000" has commas
    else:
        delimiter = _detect_delimiter(samples)
    first = _split(samples[0], delimiter)
    if header is None and not any(is_int(f) for f in first[1:4]) and not REGION_STRING.match(first[0]):
        names = _header_columns(first)
        if any(n in CHROM_COLUMNS + START_COLUMNS + REGION_COLUMNS + NAME_COLUMNS + POSITION_COLUMNS for n in names):
            header = samples[0]
    names = _header_columns(_split(header, delimiter)) if header else None

    layout = {"chrom": None, "start": None, "end": None, "name": None, "region": None, "position": None,
              "summit": None, "summitOffset": None}
    one_based = False
    kind = "BED"
    if names:
        layout["chrom"] = _find(names, CHROM_COLUMNS)
        layout["start"] = _find(names, START_COLUMNS)
        layout["end"] = _find(names, END_COLUMNS)
        layout["position"] = _find(names, POSITION_COLUMNS) if layout["start"] is None else None
        layout["name"] = _find([n for n in names], NAME_COLUMNS)
        layout["summit"] = _find(names, ("abs_summit",))
        if layout["chrom"] is None or (layout["start"] is None and layout["position"] is None):
            # a column of region strings ("chr1:100-200"), e.g. a Seurat / Signac peak table
            for index in range(len(names)):
                values = [_split(s, delimiter) for s in samples[1:6] if s != header]
                if values and all(len(v) > index and REGION_STRING.match(v[index]) for v in values):
                    layout["region"] = index
                    break
            if layout["region"] is None:
                raise InputError("The header row names no chromosome and start columns (found: {})".format(
                    ", ".join(names[:12])))
            kind = "table with a region column"
            one_based = True
        else:
            kind = "table with a header row"
            one_based = ("seqnames" in names or "width" in names or "abs_summit" in names or "pileup" in names
                         or layout["position"] is not None)
            if "abs_summit" in names and "pileup" in names:
                kind = "MACS peak table (.xls)"
        if layout["name"] in (layout["chrom"], layout["start"], layout["end"], layout["region"]):
            layout["name"] = None
    else:
        rows = [_split(s, delimiter) for s in samples]
        widths = sorted(len(c) for c in rows)
        width = widths[len(widths) // 2]  # the usual number of columns; a few odd lines do not decide the layout
        columns = [c for c in rows if len(c) >= width] or rows
        start_column = None
        for index in range(max(0, width - 2)):
            if _mostly(normalize_chromosome(c[index], valid, cache) or re.fullmatch(r"(chr)?[0-9A-Za-z_.]{1,30}", c[index])
                       for c in columns) and _mostly(is_int(c[index + 1]) and is_int(c[index + 2]) for c in columns):
                start_column = index
                break
        if start_column is not None:
            layout.update(chrom=start_column, start=start_column + 1, end=start_column + 2)
            if width > start_column + 3 and not _mostly(is_number(c[start_column + 3]) for c in columns):
                layout["name"] = start_column + 3
            if start_column == 0 and width >= 10 and _mostly(is_int(c[9]) for c in columns) and \
                    _mostly(is_number(c[6]) for c in columns):
                kind = "narrowPeak"
                layout["summitOffset"] = 9
            elif start_column == 0:
                kind = "BED{}".format(min(width, 12)) if width >= 3 else "BED"
                if width == 4 and _mostly(is_number(c[3]) for c in columns):
                    kind = "bedGraph"
            else:
                kind = "table (chromosome in column {})".format(start_column + 1)
        elif _mostly(REGION_STRING.match(c[0]) for c in columns):
            layout["region"] = 0
            if width > 1 and not _mostly(is_number(c[1]) for c in columns):
                layout["name"] = 1
            kind = "region strings (chr:start-end)"
            one_based = True
        elif width >= 2 and _mostly(is_int(c[1]) for c in columns):
            layout.update(chrom=0, position=1)
            kind = "positions (chromosome, position)"
            one_based = True
        else:
            raise InputError("Could not find chromosome, start and end in the first line: “{}”".format(samples[0][:120]))
    if coordinates == "bed":
        one_based = False
    elif coordinates == "one":
        one_based = True
    report.notes.append("Read as {}{}".format(kind, {"\t": ", tab-separated", ",": ", comma-separated",
                                                     ";": ", semicolon-separated"}.get(delimiter, "")))
    report.notes.append("Coordinates: {}".format("1-based (start - 1 taken)" if one_based else "0-based, half-open (BED)"))

    regions = []
    shift = 1 if one_based else 0
    needed = max(i for i in (layout["chrom"], layout["start"], layout["end"], layout["region"], layout["position"])
                 if i is not None) + 1
    for number, line in _split_lines(text):
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "track", "browser")):
            continue
        if header and not header_line and stripped == header:
            continue  # the header row (one starting with # is skipped above)
        report.lines += 1
        fields = _split(stripped, delimiter)
        if len(fields) < needed:
            report.issue("tooFewColumns", number, stripped)
            continue
        summit = None
        try:
            if layout["region"] is not None:
                match = REGION_STRING.match(fields[layout["region"]])
                if not match:
                    report.issue("badCoordinates", number, stripped)
                    continue
                chrom_text, start, end = match.group(1), parse_int(match.group(2)) - shift, parse_int(match.group(3))
            elif layout["position"] is not None:
                chrom_text = fields[layout["chrom"]]
                position = parse_int(fields[layout["position"]])
                start, end = position - shift, position - shift + 1
            else:
                chrom_text = fields[layout["chrom"]]
                start, end = parse_int(fields[layout["start"]]) - shift, parse_int(fields[layout["end"]])
            if layout["summit"] is not None and len(fields) > layout["summit"]:
                summit = parse_int(fields[layout["summit"]]) - shift
            elif layout["summitOffset"] is not None and len(fields) > layout["summitOffset"]:
                offset = parse_int(fields[layout["summitOffset"]])
                summit = start + offset if offset >= 0 else None
        except ValueError:
            report.issue("badCoordinates", number, stripped)
            continue
        chrom = normalize_chromosome(chrom_text, valid, cache)
        if chrom is None:
            report.issue("unknownChromosome", number, stripped)
            continue
        if chrom != chrom_text.strip().strip('"\''):
            report.renamed.setdefault(chrom_text.strip(), chrom)
        if start > end:
            start, end = end, start
            report.issue("swapped", number, stripped)
        if start < 0:
            report.issue("negative", number, stripped)
            continue
        size = sizes[chrom]
        if start >= size:
            report.issue("beyondEnd", number, stripped)
            continue
        if end > size:
            end = size
            report.issue("clipped", number, stripped)
        name = fields[layout["name"]].strip().strip('"\'') if layout["name"] is not None and \
            len(fields) > layout["name"] else ""
        if name == ".":  # BED's "no name"
            name = ""
        if summit is not None and not start <= summit <= end:
            summit = None
        regions.append((chrom, start, end, name, summit))
        if maximum_regions and len(regions) > maximum_regions:
            raise InputError("More than {:,} regions; this server's limit is {:,}".format(maximum_regions,
                                                                                         maximum_regions))
    if not regions:
        problems = "; ".join("{} {}".format(v["count"], ISSUE_TEXT.get(k, k)) for k, v in report.issues.items())
        raise InputError("No usable regions" + (": " + problems if problems else ""))
    duplicates = len(regions) - len({r[:3] for r in regions})
    summary = {
        "notes": report.notes,
        "lines": report.lines,
        "regions": len(regions),
        "issues": [{"kind": k, "text": ISSUE_TEXT.get(k, k), "count": v["count"], "examples": v["examples"]}
                   for k, v in report.issues.items()],
        "renamed": [[k, v] for k, v in list(report.renamed.items())[:20]],
        "duplicates": duplicates,
        "summits": sum(1 for r in regions if r[4] is not None),
        "columns": header_columns_summary(names, layout),
    }
    return regions, summary


def header_columns_summary(names, layout):
    """Which input columns were used, for the report."""
    used = {}
    for key in ("chrom", "start", "end", "name", "region", "position", "summit", "summitOffset"):
        index = layout.get(key)
        if index is not None:
            used[key] = names[index] if names and index < len(names) else "column {}".format(index + 1)
    return used


def region_label(region):
    """The name GREAT reports for a region: its name, else chrom:start-end (1-based start, as the UCSC browser)."""
    chrom, start, end, name = region[0], region[1], region[2], region[3]
    return name or "{}:{}-{}".format(chrom, start + 1, end)


# ---------------------------------------------------------------- assembly data

def open_text(path):
    return gzip.open(str(path), "rt", encoding="utf-8") if str(path).endswith(".gz") else open(str(path), encoding="utf-8")


class Ontology:
    """One ontology of one assembly: terms and their genes (indexes into the assembly's genes)."""

    def __init__(self, payload, gene_count):
        self.key = payload["key"]
        self.name = payload["name"]
        self.category = payload["category"]
        self.version = payload.get("version", "")
        self.term_ids = [t[0] for t in payload["terms"]]
        self.term_names = [t[1] for t in payload["terms"]]
        self.offsets = array("l", [0])
        self.genes = array("i")
        for genes in payload["genes"]:
            self.genes.extend(genes)
            self.offsets.append(len(self.genes))
        self.index = {t: i for i, t in enumerate(self.term_ids)}
        counts = [0] * (gene_count + 1)
        for g in self.genes:
            counts[g + 1] += 1
        for i in range(gene_count):
            counts[i + 1] += counts[i]
        self.gene_offsets = array("l", counts)
        fill = list(counts[:-1])
        gene_terms = array("i", [0]) * len(self.genes)
        for term in range(len(self.term_ids)):
            for p in range(self.offsets[term], self.offsets[term + 1]):
                g = self.genes[p]
                gene_terms[fill[g]] = term
                fill[g] += 1
        self.gene_terms = gene_terms

    def __len__(self):
        return len(self.term_ids)

    def term_genes(self, term):
        return self.genes[self.offsets[term]:self.offsets[term + 1]]

    def terms_of_gene(self, gene):
        return self.gene_terms[self.gene_offsets[gene]:self.gene_offsets[gene + 1]]


class Assembly:
    """data/<assembly>/ as written by tools/fetch_great_data.py."""

    def __init__(self, folder):
        self.folder = Path(folder)
        self.name = self.folder.name
        self.manifest = json.loads((self.folder / "manifest.json").read_text())
        self.genes = []  # (id, symbol, chrom, tss, strand)
        with open_text(self.folder / "genes.tsv") as handle:
            for line in handle:
                if line.startswith("#") or not line.strip():
                    continue
                f = line.rstrip("\n").split("\t")
                self.genes.append((f[0], f[4], f[1], int(f[2]), f[3]))
        self.gene_index = {g[0]: i for i, g in enumerate(self.genes)}
        self.sizes = OrderedDict()
        with open_text(self.folder / "chrom.sizes") as handle:
            for line in handle:
                f = line.split()
                if len(f) >= 2:
                    self.sizes[f[0]] = int(f[1])
        gaps = defaultdict(list)
        with open_text(self.folder / "gaps.bed") as handle:
            for line in handle:
                f = line.split()
                if len(f) >= 3 and f[0] in self.sizes:
                    gaps[f[0]].append((int(f[1]), min(int(f[2]), self.sizes[f[0]])))
        self.gaps = {}
        for chrom, items in gaps.items():
            merged = []
            for start, end in sorted(items):
                if merged and start <= merged[-1][1]:
                    merged[-1][1] = max(merged[-1][1], end)
                else:
                    merged.append([start, end])
            starts = [s for s, _ in merged]
            cumulative = [0]
            for s, e in merged:
                cumulative.append(cumulative[-1] + e - s)
            self.gaps[chrom] = (starts, [e for _, e in merged], cumulative)
        self.non_gap = sum(size - self.gap_bases(chrom, 0, size) for chrom, size in self.sizes.items())
        self.curated = {}
        curated_file = self.folder / "curated.tsv"
        if curated_file.is_file():
            with open_text(curated_file) as handle:
                for line in handle:
                    if line.startswith("#") or not line.strip():
                        continue
                    f = line.rstrip("\n").split("\t")
                    gene = self.gene_index.get(f[3])
                    if gene is not None:
                        old = self.curated.get(gene)
                        start, end = int(f[1]), int(f[2])
                        self.curated[gene] = (f[0], min(start, old[1]) if old else start, max(end, old[2]) if old else end)
        self.ontology_info = [o for o in self.manifest.get("ontologies", [])
                              if (self.folder / (o["key"] + ".json.gz")).is_file()]
        self._ontologies = {}

    def gap_bases(self, chrom, start, end):
        """Assembly-gap bases in [start, end)."""
        if chrom not in self.gaps or end <= start:
            return 0
        starts, ends, cumulative = self.gaps[chrom]
        total = 0
        i = bisect_right(starts, start) - 1
        if i >= 0 and ends[i] > start:  # a gap that begins before start
            total += min(ends[i], end) - start
        j = bisect_right(starts, start)
        k = bisect_right(starts, end - 1)
        if k > j:  # whole gaps from j to k - 1, the last one maybe cut at end
            total += cumulative[k] - cumulative[j]
            if ends[k - 1] > end:
                total -= ends[k - 1] - end
        return total

    def ontology(self, key):
        if key not in self._ontologies:
            path = self.folder / (key + ".json.gz")
            if not path.is_file():
                raise InputError("Ontology {} is not available for {}".format(key, self.name))
            with open_text(path) as handle:
                self._ontologies[key] = Ontology(json.load(handle), len(self.genes))
        return self._ontologies[key]


# ---------------------------------------------------------------- regulatory domains (createRegulatoryDomains.c)

def regulatory_domains(genes, sizes, rule, upstream=5000, downstream=1000, extension=1000000):
    """[(start, end)] per gene (genes: (id, symbol, chrom, tss, strand)), as GREAT's createRegulatoryDomains."""
    if rule not in RULES:
        raise InputError("Unknown association rule {!r}: use {}".format(rule, ", ".join(RULES)))
    order = sorted((i for i, g in enumerate(genes) if g[2] in sizes),
                   key=lambda i: (genes[i][2], genes[i][3], 0 if genes[i][4] == "+" else 1, genes[i][0]))
    domains = [(0, 0)] * len(genes)
    if rule == "oneClosest":
        for position, i in enumerate(order):
            chrom, tss = genes[i][2], genes[i][3]
            prev = order[position - 1] if position > 0 and genes[order[position - 1]][2] == chrom else None
            nxt = order[position + 1] if position + 1 < len(order) and genes[order[position + 1]][2] == chrom else None
            start = max(0, tss - extension)
            if prev is not None:
                start = max(start, (tss + genes[prev][3]) // 2)
            end = min(tss + extension, sizes[chrom])
            if nxt is not None:
                end = min(end, (tss + genes[nxt][3]) // 2)
            domains[i] = (start, end)
        return domains
    up, down = (upstream, downstream) if rule == "basalPlusExt" else (0, 0)

    def basal(i):
        chrom, tss, strand = genes[i][2], genes[i][3], genes[i][4]
        if strand == "+":
            return max(0, tss - up), min(sizes[chrom], tss + down)
        return max(0, tss - down), min(sizes[chrom], tss + up)

    for position, i in enumerate(order):
        chrom, tss = genes[i][2], genes[i][3]
        size = sizes[chrom]
        prev = order[position - 1] if position > 0 and genes[order[position - 1]][2] == chrom else None
        nxt = order[position + 1] if position + 1 < len(order) and genes[order[position + 1]][2] == chrom else None
        basal_start, basal_end = basal(i)
        start = min(basal_start, max(0, tss - extension))
        if prev is not None:
            prev_end = genes[prev][3] + (down if genes[prev][4] == "+" else up)
            start = min(basal_start, max(prev_end, start))
        end = max(basal_end, min(size, tss + extension))
        if nxt is not None:
            next_start = genes[nxt][3] - (up if genes[nxt][4] == "+" else down)
            end = max(basal_end, min(next_start, end))
        domains[i] = (start, end)
    return domains


class DomainModel:
    """Regulatory domains of one assembly under one rule, cut into segments with a constant set of genes."""

    def __init__(self, assembly, rule, upstream, downstream, extension, curated):
        self.assembly = assembly
        self.key = (rule, upstream, downstream, extension, curated)
        domains = regulatory_domains(assembly.genes, assembly.sizes, rule, upstream, downstream, extension)
        self.curated_genes = []
        if curated:
            for gene, (chrom, start, end) in assembly.curated.items():
                if assembly.genes[gene][2] == chrom:
                    domains[gene] = (start, end)
                    self.curated_genes.append(gene)
        self.domains = domains
        by_chrom = defaultdict(list)
        for gene, (start, end) in enumerate(domains):
            if end > start:
                by_chrom[assembly.genes[gene][2]].append(gene)
        self.chrom_offset = {}
        self.breaks = {}
        self.segment_genes = []
        self.segment_bases = array("q")
        self.gene_range = [(0, 0)] * len(domains)
        for chrom in assembly.sizes:
            genes = by_chrom.get(chrom, [])
            points = sorted({p for g in genes for p in domains[g]})
            offset = len(self.segment_genes)
            self.chrom_offset[chrom] = offset
            self.breaks[chrom] = points
            covering = [[] for _ in range(max(0, len(points) - 1))]
            for g in genes:
                a = bisect_right(points, domains[g][0]) - 1
                b = bisect_right(points, domains[g][1]) - 1
                self.gene_range[g] = (offset + a, offset + b)
                for s in range(a, b):
                    covering[s].append(g)
            for s in range(len(points) - 1):
                self.segment_genes.append(tuple(covering[s]))
                self.segment_bases.append(points[s + 1] - points[s] - assembly.gap_bases(chrom, points[s], points[s + 1]))
        self.prefix_bases = array("q", [0])
        for bases in self.segment_bases:
            self.prefix_bases.append(self.prefix_bases[-1] + bases)
        ranked = sorted(range(len(domains)), key=lambda g: self.gene_range[g])
        self.gene_rank = [0] * len(domains)
        for rank, g in enumerate(ranked):
            self.gene_rank[g] = rank
        self.ranked_ranges = [self.gene_range[g] for g in ranked]
        self._coverage = {}

    def segment(self, chrom, position):
        """Index of the segment holding position, or -1 outside every domain."""
        points = self.breaks.get(chrom)
        if not points:
            return -1
        s = bisect_right(points, position) - 1
        if s < 0 or s >= len(points) - 1:
            return -1
        return self.chrom_offset[chrom] + s

    def coverage(self, ontology):
        """(fraction of the non-gap genome per term, run offsets, run starts, run ends): each term's genes'
        domains merged into runs of segments."""
        if ontology.key not in self._coverage:
            fractions = array("d")
            offsets, run_a, run_b = array("l", [0]), array("l"), array("l")
            prefix = self.prefix_bases
            total = float(self.assembly.non_gap)
            rank, ranked = self.gene_rank, self.ranked_ranges
            for term in range(len(ontology)):
                bases = 0
                current_a = current_b = -1
                for r in sorted(rank[g] for g in ontology.term_genes(term)):
                    a, b = ranked[r]
                    if a == b:
                        continue
                    if current_b < 0:
                        current_a, current_b = a, b
                    elif a <= current_b:
                        if b > current_b:
                            current_b = b
                    else:
                        run_a.append(current_a)
                        run_b.append(current_b)
                        bases += prefix[current_b] - prefix[current_a]
                        current_a, current_b = a, b
                if current_b >= 0:
                    run_a.append(current_a)
                    run_b.append(current_b)
                    bases += prefix[current_b] - prefix[current_a]
                offsets.append(len(run_a))
                fractions.append(bases / total)
            self._coverage[ontology.key] = (fractions, offsets, run_a, run_b)
        return self._coverage[ontology.key]


class Engine:
    """Loaded assemblies and domain models (kept for the life of a worker)."""

    def __init__(self, data_dir, max_models=6):
        self.data_dir = Path(data_dir)
        self.assemblies = {}
        self.models = OrderedDict()
        self.max_models = max_models

    def available(self):
        return [a for a in ASSEMBLIES if (self.data_dir / a / "manifest.json").is_file()]

    def assembly(self, name):
        if name not in self.assemblies:
            if name not in ASSEMBLIES or not (self.data_dir / name / "manifest.json").is_file():
                raise InputError("Assembly {} is not available on this server".format(name))
            self.assemblies[name] = Assembly(self.data_dir / name)
        return self.assemblies[name]

    def model(self, assembly, params):
        rule = params["rule"]
        if rule == "basalPlusExt":
            key = (assembly, rule, params["upstream"], params["downstream"], params["extension"], params["curated"])
        elif rule == "twoClosest":
            key = (assembly, rule, 0, 0, params["twoDistance"], params["curated"])
        else:
            key = (assembly, rule, 0, 0, params["oneDistance"], params["curated"])
        if key in self.models:
            self.models.move_to_end(key)
            return self.models[key]
        model = DomainModel(self.assembly(assembly), *key[1:])
        self.models[key] = model
        while len(self.models) > self.max_models:
            self.models.popitem(last=False)
        return model


# ---------------------------------------------------------------- one analysis

def normalized_params(raw):
    """Analysis parameters from a request (GREAT's names and units accepted: kb for rGREAT's adv_* fields)."""
    params = dict(DEFAULT_PARAMS)
    rule = str(raw.get("rule") or params["rule"])
    if rule == "basalPlusExtension":
        rule = "basalPlusExt"
    if rule not in RULES:
        raise InputError("Unknown association rule {!r}: use basalPlusExt, twoClosest or oneClosest".format(rule))
    params["rule"] = rule

    def distance(name, kb_names, default):
        if raw.get(name) not in (None, ""):
            value = float(raw[name])
        else:
            value = next((float(raw[k]) * 1000 for k in kb_names if raw.get(k) not in (None, "")), default)
        if not math.isfinite(value) or value < 0 or value > 100000000:
            raise InputError("{} must be between 0 and 100,000 kb".format(name))
        return int(round(value))

    params["upstream"] = distance("upstream_bp", ("upstream", "adv_upstream"), params["upstream"])
    params["downstream"] = distance("downstream_bp", ("downstream", "adv_downstream"), params["downstream"])
    params["extension"] = distance("extension_bp", ("span", "adv_span"), params["extension"])
    params["twoDistance"] = distance("twoDistance_bp", ("twoDistance", "adv_twoDistance"), params["twoDistance"])
    params["oneDistance"] = distance("oneDistance_bp", ("oneDistance", "adv_oneDistance"), params["oneDistance"])
    curated = raw.get("includeCuratedRegDoms", raw.get("curated", True))
    params["curated"] = str(curated).strip().lower() not in ("0", "false", "no", "off", "")
    params["anchor"] = "summit" if str(raw.get("anchor", "")).lower() == "summit" else "midpoint"
    coordinates = str(raw.get("coordinates", "auto")).lower()
    params["coordinates"] = coordinates if coordinates in ("auto", "bed", "one") else "auto"
    return params


def rule_description(params):
    if params["rule"] == "basalPlusExt":
        text = "Basal+extension: {:g} kb upstream, {:g} kb downstream, {:g} kb max extension".format(
            params["upstream"] / 1000, params["downstream"] / 1000, params["extension"] / 1000)
    elif params["rule"] == "twoClosest":
        text = "Two nearest genes within {:g} kb".format(params["twoDistance"] / 1000)
    else:
        text = "Single nearest gene within {:g} kb".format(params["oneDistance"] / 1000)
    return text + (", curated regulatory domains included" if params["curated"] else "")


def anchor_of(region, params):
    """The base GREAT places a region at: its midpoint, floor((start + end - 1) / 2) of the 0-based bases (this
    reproduces the distances of great.stanford.edu's results exactly), or the peak summit."""
    if params["anchor"] == "summit" and region[4] is not None:
        return region[4]
    return max(region[1], (region[1] + region[2] - 1) // 2)


def associate(model, regions, params):
    """Per region: (segment, [(gene, signed distance)]): + = the region lies downstream of the TSS. A minus-strand
    gene's TSS position is the end of its transcript, so its first base is TSS - 1 (as GREAT counts it)."""
    genes = model.assembly.genes
    out = []
    for region in regions:
        position = anchor_of(region, params)
        s = model.segment(region[0], position)
        pairs = []
        if s >= 0:
            for g in model.segment_genes[s]:
                tss, strand = genes[g][3], genes[g][4]
                pairs.append((g, position - tss if strand == "+" else tss - 1 - position))
        out.append((s, pairs))
    return out


def association_graphs(associations):
    """GREAT's three graphs: genes per region (0, 1, 2, > 2) and region-gene distances, signed and absolute."""
    per_region = [0, 0, 0, 0]
    signed = [0.0] * 8  # <= -500, (-500, -50], (-50, -5], [-5, 0], [0, 5], (5, 50], (50, 500], > 500 kb
    absolute = [0, 0, 0, 0]
    for _, pairs in associations:
        per_region[min(len(pairs), 3)] += 1
        for _, d in pairs:
            magnitude = abs(d)
            bin_ = 0 if magnitude <= DISTANCE_BINS[0] else 1 if magnitude <= DISTANCE_BINS[1] else \
                2 if magnitude <= DISTANCE_BINS[2] else 3
            absolute[bin_] += 1
            if d == 0:  # exactly on the TSS: split between [-5, 0] and [0, 5]
                signed[3] += 0.5
                signed[4] += 0.5
            elif d < 0:
                signed[3 - bin_] += 1
            else:
                signed[4 + bin_] += 1
    return {"genesPerRegion": per_region, "signedDistance": signed, "absoluteDistance": absolute}


def term_hits_by_runs(coverage, counts_prefix):
    """Regions in each term's domains, from the term's runs of segments and a prefix sum of regions per segment."""
    _, offsets, run_a, run_b = coverage
    hits = array("l", [0]) * (len(offsets) - 1)
    for term in range(len(offsets) - 1):
        total = 0
        for r in range(offsets[term], offsets[term + 1]):
            total += counts_prefix[run_b[r]] - counts_prefix[run_a[r]]
        hits[term] = total
    return hits


def term_hits_by_segments(model, ontology, segment_counts):
    """The same from the other side, faster for small inputs: each hit segment's terms get its regions."""
    hits = array("l", [0]) * len(ontology)
    for s, count in segment_counts.items():
        terms = set()
        for g in model.segment_genes[s]:
            terms.update(ontology.terms_of_gene(g))
        for t in terms:
            hits[t] += count
    return hits


def region_hits(model, ontology, segments):
    counts = defaultdict(int)
    for s in segments:
        if s >= 0:
            counts[s] += 1
    offsets = ontology.gene_offsets
    work = sum(offsets[g + 1] - offsets[g] for s in counts for g in model.segment_genes[s])
    coverage = model.coverage(ontology)
    if work < len(coverage[2]):
        return term_hits_by_segments(model, ontology, counts)
    prefix = array("l", [0]) * (len(model.segment_genes) + 1)
    running = 0
    for s in range(len(model.segment_genes)):
        running += counts.get(s, 0)
        prefix[s + 1] = running
    return term_hits_by_runs(coverage, prefix)


def gene_hits(ontology, genes):
    hits = array("l", [0]) * len(ontology)
    for g in genes:
        for t in ontology.terms_of_gene(g):
            hits[t] += 1
    return hits


def finite(value):
    return value if isinstance(value, (int, float)) and math.isfinite(value) else 0.0


def enrichment_whole_genome(model, ontology, segments, hit_genes, n_regions):
    """Rows of the whole-genome test, in rGREAT's column order: ID, name, Binom_Genome_Fraction, Binom_Expected,
    Binom_Observed_Region_Hits, Binom_Fold_Enrichment, Binom_Region_Set_Coverage, Binom_Raw_PValue, Hyper_Total_Genes,
    Hyper_Expected, Hyper_Observed_Gene_Hits, Hyper_Fold_Enrichment, Hyper_Gene_Set_Coverage,
    Hyper_Term_Gene_Coverage, Hyper_Raw_PValue."""
    fractions = model.coverage(ontology)[0]
    k_regions = region_hits(model, ontology, segments)
    k_genes = gene_hits(ontology, hit_genes)
    total_genes, n_genes = len(model.assembly.genes), len(hit_genes)
    rows = []
    for t in range(len(ontology)):
        p, k = fractions[t], k_regions[t]
        expected = n_regions * p
        total = ontology.offsets[t + 1] - ontology.offsets[t]
        kg = k_genes[t]
        hyper_expected = n_genes * total / total_genes if total_genes else 0.0
        rows.append([
            ontology.term_ids[t], ontology.term_names[t], finite(p), finite(expected), k,
            finite(k / expected) if expected > 0 else 0.0, finite(k / n_regions) if n_regions else 0.0,
            binomial_upper(n_regions, k, p), total, finite(hyper_expected), kg,
            finite(kg / hyper_expected) if hyper_expected > 0 else 0.0, finite(kg / n_genes) if n_genes else 0.0,
            finite(kg / total) if total else 0.0, hypergeometric_upper(total_genes, total, n_genes, kg),
        ])
    return rows


def enrichment_background(model, ontology, fg_segments, bg_segments, fg_genes, bg_genes):
    """Rows of the foreground/background test (hypergeometric over regions), rGREAT's columns: ID, name,
    Hyper_Total_Regions, Hyper_Expected, Hyper_Foreground_Region_Hits, Hyper_Fold_Enrichment,
    Hyper_Region_Set_Coverage, Hyper_Term_Region_Coverage, Hyper_Foreground_Gene_Hits, Hyper_Background_Gene_Hits,
    Total_Genes_Annotated, Hyper_Raw_PValue."""
    k_fg = region_hits(model, ontology, fg_segments)
    k_bg = region_hits(model, ontology, bg_segments)
    g_fg = gene_hits(ontology, fg_genes)
    g_bg = gene_hits(ontology, bg_genes)
    n, total = len(fg_segments), len(bg_segments)
    rows = []
    for t in range(len(ontology)):
        k, K = k_fg[t], k_bg[t]
        expected = n * K / total if total else 0.0
        rows.append([
            ontology.term_ids[t], ontology.term_names[t], K, finite(expected), k,
            finite(k / expected) if expected > 0 else 0.0, finite(k / n) if n else 0.0, finite(k / K) if K else 0.0,
            g_fg[t], g_bg[t], ontology.offsets[t + 1] - ontology.offsets[t], hypergeometric_upper(total, K, n, k),
        ])
    return rows


def write_json_gz(path, payload):
    part = Path(str(path) + ".part")
    with gzip.open(str(part), "wb", compresslevel=1) as handle:
        handle.write(json.dumps(payload, separators=(",", ":")).encode("utf-8"))  # dumps uses the C encoder
    part.replace(path)


def read_json_gz(path):
    with gzip.open(str(path), "rt", encoding="utf-8") as handle:
        return json.load(handle)


def read_job_regions(job_dir, name):
    path = Path(job_dir) / name
    if not path.is_file():
        return None
    regions = []
    with gzip.open(str(path), "rt", encoding="utf-8") as handle:
        for line in handle:
            f = line.rstrip("\n").split("\t")
            regions.append((f[0], int(f[1]), int(f[2]), f[3], int(f[4]) if f[4] != "" else None))
    return regions


def write_job_regions(job_dir, name, regions):
    path = Path(job_dir) / name
    part = Path(str(path) + ".part")
    with gzip.open(str(part), "wt", encoding="utf-8", compresslevel=5) as handle:
        for r in regions:
            handle.write("{}\t{}\t{}\t{}\t{}\n".format(r[0], r[1], r[2], r[3].replace("\t", " "),
                                                       "" if r[4] is None else r[4]))
    part.replace(path)


def analyze(engine, job_dir):
    """Run a stored job (job.json + fg.tsv.gz [+ bg.tsv.gz]): writes associations.tsv.gz, one <ontology>.json.gz
    of rows per ontology and the summary into job.json."""
    job_dir = Path(job_dir)
    job = json.loads((job_dir / "job.json").read_text())
    params = job["params"]
    assembly = engine.assembly(job["assembly"])
    model = engine.model(job["assembly"], params)
    fg = read_job_regions(job_dir, "fg.tsv.gz")
    bg = read_job_regions(job_dir, "bg.tsv.gz")
    fg_assoc = associate(model, fg, params)
    fg_segments = [s for s, _ in fg_assoc]
    fg_genes = {g for _, pairs in fg_assoc for g, _ in pairs}
    with gzip.open(str(job_dir / "associations.tsv.gz.part"), "wt", encoding="utf-8", compresslevel=5) as handle:
        for region, (_, pairs) in zip(fg, fg_assoc):
            handle.write("{}\t{}\n".format(region_label(region), ",".join("{}:{}".format(g, d) for g, d in pairs)))
    (job_dir / "associations.tsv.gz.part").replace(job_dir / "associations.tsv.gz")
    summary = {"regions": len(fg), "associatedRegions": sum(1 for _, p in fg_assoc if p),
               "genesHit": len(fg_genes), "genes": len(assembly.genes), "nonGapBases": assembly.non_gap,
               "graphs": association_graphs(fg_assoc), "ontologies": []}
    if bg is not None:
        bg_assoc = associate(model, bg, params)
        bg_segments = [s for s, _ in bg_assoc]
        bg_genes = {g for _, pairs in bg_assoc for g, _ in pairs}
        summary["backgroundRegions"] = len(bg)
        summary["backgroundGenesHit"] = len(bg_genes)
    for info in assembly.ontology_info:
        ontology = assembly.ontology(info["key"])
        if bg is None:
            rows = enrichment_whole_genome(model, ontology, fg_segments, fg_genes, len(fg))
        else:
            rows = enrichment_background(model, ontology, fg_segments, bg_segments, fg_genes, bg_genes)
        write_json_gz(job_dir / (info["key"] + ".json.gz"), rows)
        summary["ontologies"].append({"key": info["key"], "name": info["name"], "category": info["category"],
                                      "terms": len(rows), "version": ontology.version,
                                      "hit": sum(1 for r in rows if r[4] > 0)})
    job["summary"] = summary
    job["status"] = "done"
    part = job_dir / "job.json.part"
    part.write_text(json.dumps(job))
    part.replace(job_dir / "job.json")
    return summary


def read_associations(job_dir):
    """[(region label, [(gene, distance)])] of a finished job."""
    out = []
    with gzip.open(str(Path(job_dir) / "associations.tsv.gz"), "rt", encoding="utf-8") as handle:
        for line in handle:
            label, _, rest = line.rstrip("\n").partition("\t")
            pairs = []
            for item in rest.split(",") if rest else []:
                g, _, d = item.partition(":")
                pairs.append((int(g), int(d)))
            out.append((label, pairs))
    return out


def term_details(engine, job_dir, ontology_key, term_id):
    """The regions and genes behind one term: region -> genes and gene -> regions tables, the term's graphs."""
    job_dir = Path(job_dir)
    job = json.loads((job_dir / "job.json").read_text())
    assembly = engine.assembly(job["assembly"])
    ontology = assembly.ontology(ontology_key)
    term = ontology.index.get(term_id)
    if term is None:
        raise InputError("Term {} is not in {}".format(term_id, ontology.name))
    term_genes = set(ontology.term_genes(term))
    fg = read_job_regions(job_dir, "fg.tsv.gz")
    associations = read_associations(job_dir)
    by_region, by_gene = [], defaultdict(list)
    term_assoc = []
    for index, (label, pairs) in enumerate(associations):
        kept = [(g, d) for g, d in pairs if g in term_genes]
        if kept:
            region = fg[index]
            by_region.append({"region": label, "chrom": region[0], "start": region[1], "end": region[2],
                              "genes": [[assembly.genes[g][1], d] for g, d in kept]})
            for g, d in kept:
                by_gene[g].append([label, d, index])
            term_assoc.append((None, kept))
    model = engine.model(job["assembly"], job["params"])
    genes = []
    for g in sorted(by_gene, key=lambda g: (-len(by_gene[g]), assembly.genes[g][1])):
        domain = model.domains[g]
        genes.append({"gene": assembly.genes[g][1], "id": assembly.genes[g][0], "chrom": assembly.genes[g][2],
                      "tss": assembly.genes[g][3], "strand": assembly.genes[g][4], "domain": list(domain),
                      "regions": [[label, d] for label, d, _ in by_gene[g]]})
    regions_per_gene = [0, 0, 0, 0, 0]  # genes with 1, 2, 3, 4, >= 5 of the term's regions
    for g in by_gene:
        regions_per_gene[min(len(by_gene[g]), 5) - 1] += 1
    graphs = association_graphs(term_assoc)
    graphs["regionsPerGene"] = regions_per_gene
    return {"ontology": ontology.key, "ontologyName": ontology.name, "term": term_id, "name": ontology.term_names[term],
            "annotatedGenes": len(term_genes), "byRegion": by_region, "byGene": genes, "graphs": graphs}


def term_region_names(engine, job_dir, ontology_key, term_ids):
    """{term: (region labels, gene symbols)} for export tables."""
    job_dir = Path(job_dir)
    job = json.loads((job_dir / "job.json").read_text())
    assembly = engine.assembly(job["assembly"])
    ontology = assembly.ontology(ontology_key)
    associations = read_associations(job_dir)
    regions_of_gene = defaultdict(list)
    for index, (_, pairs) in enumerate(associations):
        for g, _ in pairs:
            regions_of_gene[g].append(index)
    out = {}
    for term_id in term_ids:
        term = ontology.index.get(term_id)
        if term is None:
            continue
        genes = [g for g in ontology.term_genes(term) if g in regions_of_gene]
        indexes = sorted({i for g in genes for i in regions_of_gene[g]})
        out[term_id] = ([associations[i][0] for i in indexes], sorted(assembly.genes[g][1] for g in genes))
    return out


# ---------------------------------------------------------------- worker process

def worker(data_dir):
    """Answers one JSON request per line on stdin: {"op": "analyze" | "term" | "names" | "warm", ...}."""
    engine = Engine(data_dir)
    out = sys.stdout
    for line in sys.stdin:
        try:
            request = json.loads(line)
            op = request.get("op")
            if op == "analyze":
                result = analyze(engine, request["job"])
            elif op == "term":
                result = term_details(engine, request["job"], request["ontology"], request["term"])
            elif op == "names":
                result = {k: list(v) for k, v in term_region_names(engine, request["job"], request["ontology"],
                                                                   request["terms"]).items()}
            elif op == "warm":
                for name in request.get("assemblies", []):
                    if name in engine.available():
                        model = engine.model(name, normalized_params({}))
                        for info in model.assembly.ontology_info:
                            model.coverage(model.assembly.ontology(info["key"]))
                result = {"warm": request.get("assemblies", [])}
            elif op == "ping":
                result = {"pong": True}
            else:
                raise InputError("Unknown request {!r}".format(op))
            response = {"ok": True, "result": result}
        except InputError as exc:
            response = {"ok": False, "error": str(exc), "user": True}
        except Exception as exc:  # reported to the server, which logs it
            response = {"ok": False, "error": "{}: {}".format(type(exc).__name__, exc)}
        out.write(json.dumps(response, separators=(",", ":")) + "\n")
        out.flush()


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--worker":
        worker(sys.argv[2])
    else:
        sys.exit("usage: great.py --worker <data dir>")
