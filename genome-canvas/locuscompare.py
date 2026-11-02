"""LocusCompare for Genome Canvas: a GWAS track against eQTLs (GTEx) in one region, as LocusCompare does
(Liu et al. 2019, Nat Genet; github.com/boxiangliu/locuscomparer).

- GWAS: the track's file, read with tabix (Genome Canvas GWAS tracks are BGZF + Tabix with a header row).
- eQTL: per-tissue tabix files with the eQTL Catalogue's columns (gene_id, chromosome, position, ref, alt, pvalue,
  beta, se, rsid): GTEx v8 from the eQTL Catalogue (all tested pairs, `tools/fetch_gtex_eqtl.py`; tissues not
  downloaded are read remotely with tabix, one query at a time) or GTEx parquet files converted by
  `tools/gtex_parquet_to_eqtl.py` (v10 / v11 significant pairs only).
- Variants are matched by hg38 position and alleles (either strand), else by rsid.
- The lead SNP is LocusCompare's: the smallest sum of the two p-values (the largest sum of -log10 p when p-values
  underflow), or the one the user picks.
- LD: r² with the lead in a 1000 Genomes phase 3 population (PLINK2 on the `ldReferenceHg19` panel, matched by
  rsid), or the study's own LD when the GWAS file carries it (R2 to its SIGNAL; precomputed ld_r2 tracks).

Standard library only; tabix and plink2 are external programs.
"""

import gzip
import json
import math
import os
import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path

MAX_REGION = 3_000_000
POPULATIONS = ("EUR", "AFR", "AMR", "EAS", "SAS", "ALL")
REMOTE_GAP_SECONDS = 2.0  # EBI asks for no more than one remote tabix query every couple of seconds

# eQTL Catalogue release 7, study QTS000015 (GTEx v8), gene expression: (dataset id, key, GTEx tissue, samples).
# tools/fetch_gtex_eqtl.py downloads these.
GTEX_V8 = (
    ("QTD000116", "adipose_subcutaneous", "Adipose - Subcutaneous", 581),
    ("QTD000121", "adipose_visceral", "Adipose - Visceral (Omentum)", 469),
    ("QTD000126", "adrenal_gland", "Adrenal Gland", 233),
    ("QTD000131", "artery_aorta", "Artery - Aorta", 387),
    ("QTD000136", "artery_coronary", "Artery - Coronary", 213),
    ("QTD000141", "artery_tibial", "Artery - Tibial", 584),
    ("QTD000146", "brain_amygdala", "Brain - Amygdala", 129),
    ("QTD000151", "brain_anterior_cingulate_cortex", "Brain - Anterior cingulate cortex (BA24)", 147),
    ("QTD000156", "brain_caudate", "Brain - Caudate (basal ganglia)", 194),
    ("QTD000161", "brain_cerebellar_hemisphere", "Brain - Cerebellar Hemisphere", 175),
    ("QTD000166", "brain_cerebellum", "Brain - Cerebellum", 209),
    ("QTD000171", "brain_cortex", "Brain - Cortex", 205),
    ("QTD000176", "brain_frontal_cortex", "Brain - Frontal Cortex (BA9)", 175),
    ("QTD000181", "brain_hippocampus", "Brain - Hippocampus", 165),
    ("QTD000186", "brain_hypothalamus", "Brain - Hypothalamus", 170),
    ("QTD000191", "brain_nucleus_accumbens", "Brain - Nucleus accumbens (basal ganglia)", 202),
    ("QTD000196", "brain_putamen", "Brain - Putamen (basal ganglia)", 170),
    ("QTD000201", "brain_spinal_cord", "Brain - Spinal cord (cervical c-1)", 126),
    ("QTD000206", "brain_substantia_nigra", "Brain - Substantia nigra", 114),
    ("QTD000211", "breast", "Breast - Mammary Tissue", 394),
    ("QTD000216", "fibroblast", "Cells - Cultured fibroblasts", 483),
    ("QTD000221", "LCL", "Cells - EBV-transformed lymphocytes", 147),
    ("QTD000226", "colon_sigmoid", "Colon - Sigmoid", 318),
    ("QTD000231", "colon_transverse", "Colon - Transverse", 368),
    ("QTD000236", "esophagus_gej", "Esophagus - Gastroesophageal Junction", 330),
    ("QTD000241", "esophagus_mucosa", "Esophagus - Mucosa", 493),
    ("QTD000246", "esophagus_muscularis", "Esophagus - Muscularis", 461),
    ("QTD000251", "heart_atrial_appendage", "Heart - Atrial Appendage", 372),
    ("QTD000256", "heart_left_ventricle", "Heart - Left Ventricle", 382),
    ("QTD000261", "kidney_cortex", "Kidney - Cortex", 73),
    ("QTD000266", "liver", "Liver", 208),
    ("QTD000271", "lung", "Lung", 510),
    ("QTD000276", "minor_salivary_gland", "Minor Salivary Gland", 144),
    ("QTD000281", "muscle", "Muscle - Skeletal", 702),
    ("QTD000286", "nerve_tibial", "Nerve - Tibial", 532),
    ("QTD000291", "ovary", "Ovary", 167),
    ("QTD000296", "pancreas", "Pancreas", 305),
    ("QTD000301", "pituitary", "Pituitary", 237),
    ("QTD000306", "prostate", "Prostate", 218),
    ("QTD000311", "skin_not_sun_exposed", "Skin - Not Sun Exposed (Suprapubic)", 517),
    ("QTD000316", "skin_sun_exposed", "Skin - Sun Exposed (Lower leg)", 602),
    ("QTD000321", "small_intestine", "Small Intestine - Terminal Ileum", 174),
    ("QTD000326", "spleen", "Spleen", 227),
    ("QTD000331", "stomach", "Stomach", 324),
    ("QTD000336", "testis", "Testis", 322),
    ("QTD000341", "thyroid", "Thyroid", 574),
    ("QTD000346", "uterus", "Uterus", 129),
    ("QTD000351", "vagina", "Vagina", 141),
    ("QTD000356", "blood", "Whole Blood", 670),
)
GTEX_V8_REMOTE = "http://ftp.ebi.ac.uk/pub/databases/spot/eQTL/sumstats/QTS000015/{id}/{id}.all.tsv.gz"
CATALOGUE_COLUMNS = ("molecular_trait_id", "chromosome", "position", "ref", "alt", "variant", "ma_samples", "maf",
                     "pvalue", "beta", "se", "type", "ac", "an", "r2", "molecular_trait_object_id", "gene_id",
                     "median_tpm", "rsid")
DEFAULT_TISSUE = "artery_coronary"

GWAS_FIELDS = {
    "chrom": ("chromosome", "chr", "chrom", "#chrom", "#chr", "chromosome_name", "seqnames"),
    "pos": ("position", "pos", "bp", "base_pair_location", "pos_hg38", "chr_pos"),
    "p": ("pvalue", "p", "pval", "p_value", "p.value", "p-value", "association_pvalue"),
    "logp": ("neg_log_pvalue", "log10p", "logp", "mlog10p", "neg_log10_p", "minus_log10_p", "neg_logp",
             "association_neg_log_pvalue"),
    "rsid": ("rsid", "rs", "snp", "rsid.x", "rs_id", "snpid", "markername", "variant_id", "id"),
    "ref": ("ref", "other_allele", "other_a", "reference_allele", "nea", "noneff", "allele2", "a2"),
    "alt": ("alt", "effect_allele", "effect_a", "eff", "ea", "allele1", "a1"),
    "beta": ("beta", "effect", "log_or", "b", "effect_size"),
    "subset": ("group", "ld_pop", "population", "ancestry"),
}
# a GWAS file's own LD: (r² column, its reference column)
STUDY_LD = (("ld_r2", "ld_reference"), ("r2", "signal"), ("ld_r2", "lead"))


class Problem(ValueError):
    """A request that cannot be answered (shown to the user)."""


def chrom_number(value):
    value = str(value or "").strip()
    value = value[3:] if value.lower().startswith("chr") else value
    return {"23": "X", "24": "Y", "M": "MT"}.get(value.upper(), value.upper())


def parse_float(value):
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def neg_log10(p, logp=None):
    if logp is not None and logp >= 0:
        return logp
    if p is None:
        return None
    if p <= 0:
        return 320.0
    return -math.log10(min(1.0, p))


COMPLEMENT = str.maketrans("ACGT", "TGCA")


def allele_keys(ref, alt):
    """Keys that match a variant on either strand and in either allele order."""
    ref, alt = str(ref or "").upper(), str(alt or "").upper()
    if not ref or not alt or ref == "." or alt == ".":
        return ()
    first = "/".join(sorted((ref, alt)))
    second = "/".join(sorted((ref.translate(COMPLEMENT), alt.translate(COMPLEMENT))))
    return (first,) if first == second else (first, second)


def read_header(path):
    """The first line of a (BGZF) text file, split at tabs."""
    opener = gzip.open if str(path).endswith((".gz", ".bgz")) else open
    with opener(path, "rt") as f:
        return f.readline().rstrip("\n").split("\t")


def pick(header, names):
    lower = [h.strip().lower() for h in header]
    for name in names:
        if name in lower:
            return lower.index(name)
    return None


class Tabix:
    """Region queries with the tabix program; chromosome names as the file has them."""

    def __init__(self):
        self.names = {}
        self.lock = threading.Lock()
        self.remote_at = 0.0

    def chromosomes(self, target, cwd=None):
        if target not in self.names:
            result = subprocess.run(["tabix", "-l", str(target)], capture_output=True, text=True, timeout=60, cwd=cwd)
            self.names[target] = result.stdout.split() if result.returncode == 0 else []
        return self.names[target]

    def query(self, target, chrom, start, end, cwd=None):
        names = self.chromosomes(target, cwd)
        n = chrom_number(chrom)
        name = next((c for c in names if chrom_number(c) == n), None)
        if name is None:
            return []
        remote = str(target).startswith(("http://", "https://", "ftp://"))
        if remote:
            with self.lock:  # one remote query at a time, spaced out
                wait = self.remote_at + REMOTE_GAP_SECONDS - time.time()
                if wait > 0:
                    time.sleep(wait)
                result = self._run(target, name, start, end, cwd, 120)
                self.remote_at = time.time()
        else:
            result = self._run(target, name, start, end, cwd, 60)
        return result

    @staticmethod
    def _run(target, name, start, end, cwd, timeout):
        result = subprocess.run(["tabix", str(target), "{}:{}-{}".format(name, max(1, start), end)],
                                capture_output=True, text=True, timeout=timeout, cwd=cwd)
        if result.returncode != 0:
            raise Problem("Could not read {}: {}".format(Path(str(target)).name, result.stderr.strip()[:200]))
        return result.stdout.splitlines()


class LRU:
    def __init__(self, size):
        self.size, self.items, self.lock = size, {}, threading.Lock()

    def get(self, key):
        with self.lock:
            value = self.items.pop(key, None)
            if value is not None:
                self.items[key] = value
            return value

    def put(self, key, value):
        with self.lock:
            self.items.pop(key, None)
            self.items[key] = value
            while len(self.items) > self.size:
                self.items.pop(next(iter(self.items)))
        return value


class LocusCompare:
    def __init__(self, settings, app_dir, state_dir):
        config = settings.get("locusCompare") if isinstance(settings.get("locusCompare"), dict) else {}
        self.app_dir = Path(app_dir)
        self.cache_dir = Path(state_dir) / "locuscompare"
        resolve = lambda value: (lambda p: p if p.is_absolute() else self.app_dir / p)(Path(os.path.expanduser(str(value))))
        self.genes_file = resolve(config.get("genes") or "data/eQTL/genes.gencode_v39.hg38.tsv")
        ld = settings.get("ldReferenceHg19") if isinstance(settings.get("ldReferenceHg19"), dict) else {}
        self.ld_bfile = ld.get("bfileTemplate")
        self.ld_panel = ld.get("panel") or config.get("ldPanel")
        self.ld_label = str(ld.get("label") or "1000 Genomes Phase 3")
        sources = config.get("eqtl")
        if not isinstance(sources, list):
            sources = [{"id": "gtex_v8", "label": "GTEx v8 · all pairs (eQTL Catalogue)", "dir": "data/eQTL/GTEx_v8",
                        "catalogue": "gtex_v8", "remote": True},
                       {"id": "gtex_v11", "label": "GTEx v11 · significant pairs", "dir": "data/eQTL/GTEx_v11_signif"}]
        self.sources = []
        for s in sources:
            if not isinstance(s, dict) or not s.get("id") or not s.get("dir"):
                continue
            self.sources.append(dict(s, dir=resolve(s["dir"])))
        self.tabix = Tabix()
        self.gwas_cache = LRU(8)
        self.eqtl_cache = LRU(8)
        self.ld_cache = LRU(32)
        self._genes = None

    # ------------------------------------------------------------------ catalog

    def tissues(self, source):
        """[{key, label, samples, target (path or URL), local, cwd}] of an eQTL source."""
        d = source["dir"]
        out = []
        allowed = set(source.get("tissues") or [])  # a source's "tissues": the ones offered (default: all)
        if source.get("catalogue") == "gtex_v8":
            for qtd, key, label, n in GTEX_V8:
                if allowed and key not in allowed:
                    continue
                path = d / "{}.all.tsv.gz".format(qtd)
                local = path.is_file() and Path(str(path) + ".tbi").is_file()
                if local or source.get("remote"):
                    out.append({"key": key, "label": label, "samples": n, "local": local, "columns": None,
                                "target": str(path) if local else GTEX_V8_REMOTE.format(id=qtd), "cwd": str(d)})
            return out
        try:
            manifest = json.loads((d / "manifest.json").read_text())
        except (OSError, ValueError):
            return out
        for t in manifest.get("tissues") or []:
            if allowed and t["key"] not in allowed:
                continue
            path = d / t.get("file", "")
            if path.is_file() and Path(str(path) + ".tbi").is_file():
                out.append({"key": t["key"], "label": t.get("label") or t["key"], "samples": t.get("samples"),
                            "local": True, "target": str(path), "cwd": str(d), "columns": None})
        source["significantOnly"] = bool(manifest.get("significantOnly"))
        return out

    def catalog(self):
        sources = []
        for s in self.sources:
            tissues = self.tissues(s)
            if tissues:
                sources.append({"id": s["id"], "label": s.get("label") or s["id"], "significantOnly": bool(s.get("significantOnly")),
                                "tissues": [{k: t[k] for k in ("key", "label", "samples", "local")} for t in tissues]})
        ld = self.ld_available()
        return {"available": bool(sources), "sources": sources, "defaultTissue": DEFAULT_TISSUE,
                "ld": {"available": ld, "label": self.ld_label, "populations": list(POPULATIONS) if ld else []},
                "maxRegion": MAX_REGION}

    def ld_available(self):
        return bool(self.ld_bfile) and bool(self.ld_panel) and Path(os.path.expanduser(str(self.ld_panel))).is_file()

    def source_tissue(self, source_id, tissue_key):
        source = next((s for s in self.sources if s["id"] == source_id), None) or (self.sources[0] if self.sources and not source_id else None)
        if not source:
            raise Problem("No eQTL source {}".format(source_id))
        tissues = self.tissues(source)
        tissue = next((t for t in tissues if t["key"] == tissue_key), None)
        if tissue is None and not tissue_key:
            tissue = next((t for t in tissues if t["key"] == DEFAULT_TISSUE), tissues[0] if tissues else None)
        if tissue is None:
            raise Problem("{} has no tissue {}".format(source.get("label") or source["id"], tissue_key))
        return source, tissue

    # ------------------------------------------------------------------ genes

    def genes(self):
        if self._genes is None:
            genes = []
            try:
                with open(self.genes_file) as f:
                    head = f.readline().rstrip("\n").split("\t")
                    col = {h: i for i, h in enumerate(head)}
                    for line in f:
                        p = line.rstrip("\n").split("\t")
                        genes.append({"id": p[col["gene_id"]], "symbol": p[col["symbol"]], "chrom": chrom_number(p[col["chrom"]]),
                                      "start": int(p[col["start"]]), "end": int(p[col["end"]]), "strand": p[col["strand"]],
                                      "type": p[col["type"]] if "type" in col else ""})
            except (OSError, KeyError, ValueError, IndexError):
                genes = []
            self._genes = genes
        return self._genes

    def genes_in(self, chrom, start, end):
        n = chrom_number(chrom)
        return [g for g in self.genes() if g["chrom"] == n and g["end"] >= start and g["start"] <= end]

    def gene_by_id(self):
        if getattr(self, "_by_id", None) is None:
            self._by_id = {g["id"]: g for g in self.genes()}
        return self._by_id

    def gene_named(self, name):
        name = str(name or "").strip()
        for g in self.genes():
            if g["id"] == name.split(".")[0] or g["symbol"].upper() == name.upper():
                return g
        return None

    # ------------------------------------------------------------------ reading

    def eqtl_rows(self, source, tissue, chrom, start, end):
        """{gene_id: [(pos, ref, alt, p, beta, rsid)]} of a tissue in a region (cached)."""
        key = (source["id"], tissue["key"], chrom_number(chrom), start, end)
        cached = self.eqtl_cache.get(key)
        if cached is not None:
            return cached
        header = list(CATALOGUE_COLUMNS) if not tissue["local"] else read_header(tissue["target"])
        col = {h: i for i, h in enumerate(header)}
        need = ("gene_id", "position", "ref", "alt", "pvalue")
        if any(n not in col for n in need):
            raise Problem("{} lacks the columns {}".format(tissue["label"], ", ".join(n for n in need if n not in col)))
        ig, ip, ir, ia, iv = (col[n] for n in need)
        ib, irs = col.get("beta"), col.get("rsid")
        by_gene = {}
        for line in self.tabix.query(tissue["target"], chrom, start, end, cwd=tissue["cwd"]):
            f = line.split("\t")
            if len(f) <= max(ig, ip, ir, ia, iv) or f[0] == header[0]:
                continue
            p = parse_float(f[iv])
            if p is None:
                continue
            try:
                pos = int(f[ip])
            except ValueError:
                continue
            rs = f[irs] if irs is not None and irs < len(f) else "."
            by_gene.setdefault(f[ig].split(".")[0], []).append(
                (pos, f[ir], f[ia], p, parse_float(f[ib]) if ib is not None and ib < len(f) else None, rs if rs.startswith("rs") else ""))
        return self.eqtl_cache.put(key, by_gene)

    def gwas_rows(self, path, chrom, start, end):
        """(rows, info) of a GWAS file in a region: rows {pos, ref, alt, rsid, p, logp, beta, subset, sr2, sref}."""
        stat = path.stat()
        key = (str(path), stat.st_mtime_ns, chrom_number(chrom), start, end)
        cached = self.gwas_cache.get(key)
        if cached is not None:
            return cached
        header = read_header(path)
        col = {name: pick(header, names) for name, names in GWAS_FIELDS.items()}
        if col["pos"] is None or (col["p"] is None and col["logp"] is None):
            raise Problem("{} has no position or p-value column".format(path.name))
        lower = [h.strip().lower() for h in header]
        study = next(((lower.index(a), lower.index(b)) for a, b in STUDY_LD if a in lower and b in lower), None)
        rows, subsets = [], {}
        for line in self.tabix.query(path, chrom, start, end):
            f = line.split("\t")
            get = lambda name: f[col[name]] if col[name] is not None and col[name] < len(f) else ""
            try:
                pos = int(get("pos"))
            except ValueError:
                continue
            p = parse_float(get("p"))
            logp = neg_log10(p, parse_float(get("logp")) if p is None else None)
            if logp is None:
                continue
            rsid = get("rsid")
            ref, alt = get("ref"), get("alt")
            if (not ref or not alt) and re.fullmatch(r"(?:chr)?[0-9XYM]+[:_]\d+[:_][ACGTN]+[:_][ACGTN]+", rsid or "", re.I):
                ref, alt = re.split(r"[:_]", rsid)[2:4]
            subset = get("subset")
            if subset:
                subsets[subset] = subsets.get(subset, 0) + 1
            row = {"pos": pos, "ref": ref, "alt": alt, "rsid": rsid if rsid.startswith("rs") else "", "p": p, "logp": logp,
                   "beta": parse_float(get("beta")), "subset": subset}
            if study:
                row["sr2"] = parse_float(f[study[0]]) if study[0] < len(f) else None
                row["sref"] = f[study[1]] if study[1] < len(f) else ""
            rows.append(row)
        info = {"columns": {k: header[v] for k, v in col.items() if v is not None}, "studyLD": bool(study),
                "subsets": sorted(subsets, key=lambda s: -subsets[s])}
        return self.gwas_cache.put(key, (rows, info))

    # ------------------------------------------------------------------ LD

    def keep_file(self, population):
        target = self.cache_dir / "keep-{}.txt".format(population)
        if not target.is_file():
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            ids = []
            with open(os.path.expanduser(str(self.ld_panel))) as f:
                head = f.readline().split()
                isample = head.index("sample") if "sample" in head else 0
                ipop = head.index("super_pop") if "super_pop" in head else 2
                for line in f:
                    p = line.split()
                    if len(p) > max(isample, ipop) and (population == "ALL" or p[ipop] == population):
                        ids.append("{0}\t{0}".format(p[isample]))
            tmp = target.with_name(target.name + ".part")
            tmp.write_text("\n".join(ids) + "\n")
            tmp.replace(target)
        return target

    def ld_1000g(self, chrom, rsid, population, window_kb):
        """{rsid: r²} with `rsid` in a 1000 Genomes population, or (None, reason)."""
        if not self.ld_available():
            return None, "No 1000 Genomes LD panel is configured"
        if population not in POPULATIONS:
            raise Problem("population is one of {}".format(", ".join(POPULATIONS)))
        key = (chrom_number(chrom), rsid, population, window_kb)
        cached = self.ld_cache.get(key)
        if cached is not None:
            return cached
        bfile = Path(os.path.expanduser(str(self.ld_bfile).format(chrom=chrom_number(chrom))))
        if not Path(str(bfile) + ".bed").is_file():
            return None, "No 1000 Genomes panel for chromosome {}".format(chrom_number(chrom))
        keep = self.keep_file(population)
        with tempfile.TemporaryDirectory(prefix="genome-canvas-lc-") as tmp:
            out = Path(tmp) / "ld"
            # --window is the width around --snp (half on each side); --ld-window-kb the reach from the lead
            cmd = ["plink2", "--bfile", str(bfile), "--snp", rsid, "--window", str(2 * window_kb), "--ld-snp", rsid,
                   "--r2-unphased", "cols=chrom,pos,id,ref,alt1", "--ld-window", "99999999", "--ld-window-kb", str(window_kb),
                   "--ld-window-r2", "0", "--threads", "2", "--out", str(out)]
            if population != "ALL":
                cmd[3:3] = ["--keep", str(keep)]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
            vcor = Path(str(out) + ".vcor")
            if result.returncode != 0 or not vcor.is_file():
                log = Path(str(out) + ".log")
                text = log.read_text(errors="replace") if log.is_file() else result.stderr
                why = "is not in the 1000 Genomes panel" if "not found" in text or "No variants" in text else \
                    "appears more than once in the panel" if "uplicate" in text else "could not be computed"
                return self.ld_cache.put(key, (None, "LD: {} {}".format(rsid, why)))
            r2 = {rsid: 1.0}
            with open(vcor) as f:
                head = f.readline().lstrip("#").rstrip("\n").split("\t")
                iid, ir2 = head.index("ID_B"), head.index("UNPHASED_R2")
                for line in f:
                    p = line.rstrip("\n").split("\t")
                    value = parse_float(p[ir2])
                    if value is not None:
                        r2[p[iid]] = max(0.0, min(1.0, value))
        return self.ld_cache.put(key, (r2, None))

    # ------------------------------------------------------------------ the plot's data

    def compare(self, gwas_path, gwas_label, chrom, start, end, source_id="", tissue_key="", gene="", population="EUR",
                lead="", subset=""):
        chrom = chrom_number(chrom)
        start, end = int(start), int(end)
        if end <= start:
            raise Problem("Give a region (start < end)")
        if end - start > MAX_REGION:
            raise Problem("Pick a region of {} Mb or less".format(MAX_REGION // 1_000_000))
        source, tissue = self.source_tissue(source_id, tissue_key)
        gwas, info = self.gwas_rows(Path(gwas_path), chrom, start, end)
        if info["subsets"]:
            subset = subset if subset in info["subsets"] else next((s for s in info["subsets"] if "EUR" in s.upper()), info["subsets"][0])
            gwas = [r for r in gwas if r["subset"] == subset]
        else:
            subset = ""
        by_gene = self.eqtl_rows(source, tissue, chrom, start, end)
        genes = self.genes_in(chrom, start, end)
        names = self.gene_by_id()
        egenes = []
        for gid, rows in by_gene.items():
            g = names.get(gid) or {}
            best = min(r[3] for r in rows)
            egenes.append({"id": gid, "symbol": g.get("symbol") or gid, "minP": best, "n": len(rows),
                           "start": g.get("start"), "end": g.get("end"), "strand": g.get("strand")})
        egenes.sort(key=lambda g: g["minP"])
        if not egenes:
            raise Problem("{} has no eQTLs in this region".format(tissue["label"]))
        wanted = None
        if gene:
            g = self.gene_named(gene)
            gid = g["id"] if g else str(gene).split(".")[0]
            wanted = next((e for e in egenes if e["id"] == gid or e["symbol"].upper() == str(gene).upper()), None)
            if wanted is None:
                raise Problem("{} has no eQTLs for {} in this region".format(tissue["label"], gene))
        else:
            # the gene whose eQTLs best overlap the GWAS signal: smallest p of the pair sum at shared variants
            wanted = self.best_gene(gwas, by_gene, egenes)
        merged = self.merge(gwas, by_gene[wanted["id"]], chrom)
        if not merged:
            raise Problem("No variant is in both the GWAS and the {} eQTLs of {} here".format(tissue["label"], wanted["symbol"]))
        snp = self.pick_lead(merged, lead)
        ld = self.color(merged, snp, chrom, start, end, population)
        return {
            "region": {"chrom": "chr" + chrom, "start": start, "end": end},
            "gwas": {"label": gwas_label, "rows": len(gwas), "subset": subset, "subsets": info["subsets"],
                     "studyLD": info["studyLD"], "columns": info["columns"]},
            "eqtl": {"source": source["id"], "sourceLabel": source.get("label") or source["id"], "tissue": tissue["key"],
                     "tissueLabel": tissue["label"], "local": tissue["local"], "significantOnly": bool(source.get("significantOnly")),
                     "gene": wanted["id"], "symbol": wanted["symbol"], "rows": len(by_gene[wanted["id"]])},
            "egenes": egenes[:200],
            "lead": snp["id"],
            "ld": ld,
            "snps": merged,
            "genes": [dict(g, chrom="chr" + g["chrom"]) for g in genes if g["type"] in ("protein_coding", "lncRNA", "")][:400],
        }

    @staticmethod
    def index(rows):
        by_key, by_rsid = {}, {}
        for r in rows:
            for k in allele_keys(r[1], r[2]):
                by_key.setdefault((r[0], k), r)
            if r[5]:
                by_rsid.setdefault(r[5], r)
        return by_key, by_rsid

    def merge(self, gwas, eqtl_rows, chrom):
        by_key, by_rsid = self.index(eqtl_rows)
        merged, seen = [], set()
        for g in gwas:
            e = None
            for k in allele_keys(g["ref"], g["alt"]):
                e = by_key.get((g["pos"], k))
                if e:
                    break
            if e is None and g["rsid"]:
                e = by_rsid.get(g["rsid"])
            if e is None:
                continue
            ident = (e[0], e[1], e[2])
            if ident in seen:
                continue
            seen.add(ident)
            rsid = g["rsid"] or e[5]
            merged.append({"id": rsid or "{}:{}:{}:{}".format(chrom, e[0], e[1], e[2]), "rsid": rsid,
                           "pos": e[0], "ref": e[1], "alt": e[2], "p1": g["p"], "logp1": round(g["logp"], 4), "beta1": g["beta"],
                           "p2": e[3], "logp2": round(neg_log10(e[3]), 4), "beta2": e[4],
                           "sr2": g.get("sr2"), "sref": g.get("sref", "")})
        merged.sort(key=lambda m: m["pos"])
        return merged

    def best_gene(self, gwas, by_gene, egenes):
        best, score = None, -1.0
        gwas_index = {}
        for g in gwas:
            for k in allele_keys(g["ref"], g["alt"]):
                gwas_index[(g["pos"], k)] = g["logp"]
        for e in egenes[:60]:
            top = 0.0
            for r in by_gene[e["id"]]:
                for k in allele_keys(r[1], r[2]):
                    lg = gwas_index.get((r[0], k))
                    if lg is not None:
                        top = max(top, lg + neg_log10(r[3]))
                        break
            if top > score:
                best, score = e, top
        return best or egenes[0]

    @staticmethod
    def pick_lead(merged, lead):
        if lead:
            text = str(lead).strip()
            hit = next((m for m in merged if text in (m["id"], m["rsid"]) or text == str(m["pos"])), None)
            if hit:
                return hit
            raise Problem("{} is not among the variants in both studies".format(text))
        usable = all(m["p1"] is not None and m["p1"] > 0 and m["p2"] > 0 for m in merged)
        if usable:
            return min(merged, key=lambda m: m["p1"] + m["p2"])
        return max(merged, key=lambda m: m["logp1"] + m["logp2"])

    def color(self, merged, snp, chrom, start, end, population):
        """Fills m["r2"] (None: unknown) and returns how LD was found."""
        for m in merged:
            m["r2"] = None
        if population == "study":
            ref = snp.get("sref")
            if not ref:
                return {"available": False, "source": "study", "reason": "The lead has no LD in the study file", "reference": snp["id"]}
            for m in merged:
                if m.get("sref") == ref and m.get("sr2") is not None:
                    m["r2"] = m["sr2"]
            snp["r2"] = 1.0
            return {"available": True, "source": "study", "label": "study LD (r² to {})".format(ref), "reference": ref}
        if not snp["rsid"]:
            return {"available": False, "source": "1000G", "population": population,
                    "reason": "The lead has no rsid to look up in 1000 Genomes: click another SNP"}
        window_kb = max(500, min(3000, (max(snp["pos"] - start, end - snp["pos"]) + 999) // 1000 + 50))
        r2, why = self.ld_1000g(chrom, snp["rsid"], population, window_kb)
        if r2 is None:
            return {"available": False, "source": "1000G", "population": population, "reason": why}
        for m in merged:
            if m["rsid"] in r2:
                m["r2"] = round(r2[m["rsid"]], 4)
        return {"available": True, "source": "1000G", "population": population,
                "label": "{} {}".format(self.ld_label.replace(" ALL", ""), population), "reference": snp["rsid"]}

    def egenes(self, source_id, tissue_key, chrom, start, end):
        source, tissue = self.source_tissue(source_id, tissue_key)
        by_gene = self.eqtl_rows(source, tissue, chrom_number(chrom), int(start), int(end))
        names = self.gene_by_id()
        out = [{"id": gid, "symbol": (names.get(gid) or {}).get("symbol") or gid, "minP": min(r[3] for r in rows), "n": len(rows)}
               for gid, rows in by_gene.items()]
        return sorted(out, key=lambda g: g["minP"])
