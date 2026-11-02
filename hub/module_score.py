"""Module scores for the spatial and single-cell viewers: AUCell's AUC (default) or Seurat's AddModuleScore.

For a gene set, a cell's score is the area under its recovery curve over the genes ranked by expression in that cell,
up to rank k (AUCell's aucMaxRank, 5% of the genes): sum over the set's genes ranked r < k of (k - r), divided by
k * (genes in the set). Ranks are 1-based and ties get their mean rank; genes a cell does not express rank last and
add nothing. Ranks within a cell are the same for raw counts and log-normalized values.

The matrix (expr.bin) is stored gene by gene, so the ranks need every cell's top-k values: they are computed once per
slide and assay (`build`, a few seconds to minutes) and kept in a cache folder as one sorted array of
(cell, value) groups — the distinct values among each cell's top k with how many genes have them and how many genes
rank above. A score then reads only the set's genes.

Seurat (AddModuleScore): the set's mean log-normalized expression (LogNormalize, scale 10,000; stored float values as
they are) minus the mean of control genes — for each set gene, 100 genes drawn (seed 1) from the same of 24
equal-size bins of average expression. The genes' averages are kept in the same cache. Saved scores are float32 files next to an index.json, in a
folder of their own per slide (never inside the converted data or its source).
"""

import json
import math
import re
import secrets
import shutil
import threading
import time
from pathlib import Path

import numpy as np

CACHE_VERSION = 2
BLOCK_NNZ = 20_000_000  # (cell, value) pairs sorted at once while building


def max_rank(n_genes):
    """AUCell's default aucMaxRank (5% of the genes), at least 50 genes on small panels."""
    return max(math.ceil(0.05 * n_genes), min(n_genes, 50))


class Matrix:
    """One assay of a slide folder: feature names and the gene-by-gene expr.bin / expr.idx."""

    def __init__(self, folder: Path, assay: str = '', genes_only=True):
        meta = json.loads((folder / 'slide.json').read_text())
        assays = meta.get('assays') or []
        a = next((x for x in assays if x.get('key') == assay), None) or (assays[0] if assays else {})
        self.label, self.type = a.get('label') or 'Gene expression', a.get('type') or 'rna'
        if a.get('type') == 'chromvar' and genes_only:
            raise ValueError('Module scores need genes: pick the gene expression assay')
        sub = folder / a['dir'] if a.get('dir') else folder
        self.key = a.get('key') or 'main'
        self.n = int(meta['nCells'])
        self.names = json.loads((folder / a['features']).read_text())['names'] if a.get('features') else meta['genes']
        self.float = (a.get('exprDtype') or meta.get('exprDtype')) == 'float32'
        self.bin, self.idx_file = sub / 'expr.bin', sub / 'expr.idx'
        self.index = np.fromfile(self.idx_file, dtype='<f8').reshape(-1, 2).astype(np.int64)
        self.stamp = [self.bin.stat().st_size, int(self.bin.stat().st_mtime), self.n, len(self.names)]
        # per-cell totals for log-normalization (counts only)
        total = next((o for o in meta.get('obs', []) if o['key'] == (a.get('total') or 'total_counts')), None)
        self.totals = None
        if total and not self.float:
            dtype = {'uint8': '<u1', 'uint16': '<u2', 'float32': '<f4'}[total['dtype']]
            self.totals = np.fromfile(folder / 'cells.bin', dtype=dtype, count=self.n, offset=total['offset']).astype(np.float64)
            self.totals[self.totals <= 0] = 1

    def lognorm(self, cells, values):
        """Seurat LogNormalize (scale 10,000) of one gene's values; stored float values stay as they are."""
        if self.totals is None:
            return values.astype(np.float64)
        return np.log1p(values / self.totals[cells] * 1e4)

    def gene(self, g, data=None):
        """(cells, values as float32) of gene number g; `data` a memmap of expr.bin, else read from the file."""
        offset, nnz = self.index[g]
        if not nnz:
            return np.zeros(0, np.uint32), np.zeros(0, np.float32)
        width = 4 if self.float else 2
        if data is None:
            with open(self.bin, 'rb') as f:
                f.seek(offset)
                buf = f.read(nnz * (4 + width))
        else:
            buf = data[offset:offset + nnz * (4 + width)]
        cells = np.frombuffer(buf, '<u4', nnz)
        values = np.frombuffer(buf, '<f4' if self.float else '<u2', nnz, nnz * 4).astype(np.float32)
        return cells, values


class Ranks:
    """The cache of one slide assay: groups sorted by `keys` = cell * S + (M - value) (M > every value, S > M)."""

    def __init__(self, folder: Path):
        meta = json.loads((folder / 'ranks.json').read_text())
        self.k, self.S, self.M, self.stamp = meta['k'], meta['S'], meta['M'], meta['stamp']
        self.keys = np.load(folder / 'keys.npy', mmap_mode='r')
        self.count = np.load(folder / 'count.npy', mmap_mode='r')
        self.above = np.load(folder / 'above.npy', mmap_mode='r')
        self.average = np.load(folder / 'average.npy')


def build(matrix: Matrix, out: Path, progress=lambda f: None):
    """Write the rank cache of `matrix` to `out` (atomically: a sibling folder renamed at the end)."""
    k = max_rank(len(matrix.names))
    data = np.memmap(matrix.bin, dtype=np.uint8, mode='r')
    genes = [g for g in range(len(matrix.names)) if matrix.index[g, 1]]
    total = int(matrix.index[:, 1].sum())
    # the largest value, for the sort keys (counts: from the values; float data: the same)
    blocks = max(1, math.ceil(total / BLOCK_NNZ))
    bounds = np.linspace(0, matrix.n, blocks + 1).astype(np.int64)
    parts, M = [], 0.0
    for b in range(blocks):
        c0, c1 = bounds[b], bounds[b + 1]
        cs, vs = [], []
        for g in genes:
            cells, values = matrix.gene(g, data)
            lo, hi = np.searchsorted(cells, c0), np.searchsorted(cells, c1)
            if hi > lo:
                cs.append(cells[lo:hi])
                vs.append(values[lo:hi])
        progress((b + 0.5) / blocks)
        if not cs:
            continue
        c = np.concatenate(cs).astype(np.int64)
        v = np.concatenate(vs)
        order = np.lexsort((-v, c))  # by cell, then highest value first
        c, v = c[order], v[order]
        start = np.flatnonzero(np.concatenate(([True], (c[1:] != c[:-1]) | (v[1:] != v[:-1]))))
        gc, gv = c[start], v[start]
        gn = np.diff(np.append(start, len(c)))
        before = np.cumsum(gn) - gn  # genes in earlier groups of the block
        first = np.flatnonzero(np.concatenate(([True], gc[1:] != gc[:-1])))
        above = before - np.repeat(before[first], np.diff(np.append(first, len(gc))))  # genes ranked above, in the cell
        keep = above < k
        parts.append((gc[keep], gv[keep], gn[keep], above[keep]))
        M = max(M, float(gv.max()))
        progress((b + 1) / blocks)
    M = M + 1.0
    S = M + 1.0
    average = np.zeros(len(matrix.names))
    for g in genes:  # mean log-normalized expression of each gene, for Seurat's control bins
        cells, values = matrix.gene(g, data)
        average[g] = matrix.lognorm(cells, values).sum() / matrix.n
    tmp = out.parent / f'{out.name}.part-{secrets.token_hex(4)}'
    tmp.mkdir(parents=True)
    try:
        cells = np.concatenate([p[0] for p in parts]) if parts else np.zeros(0, np.int64)
        values = np.concatenate([p[1] for p in parts]) if parts else np.zeros(0, np.float32)
        np.save(tmp / 'keys.npy', cells.astype(np.float64) * S + (M - values.astype(np.float64)))
        np.save(tmp / 'count.npy', (np.concatenate([p[2] for p in parts]) if parts else np.zeros(0)).astype(np.uint32))
        np.save(tmp / 'average.npy', average)
        np.save(tmp / 'above.npy', (np.concatenate([p[3] for p in parts]) if parts else np.zeros(0)).astype(np.uint32))
        (tmp / 'ranks.json').write_text(json.dumps({'version': CACHE_VERSION, 'k': k, 'S': S, 'M': M, 'stamp': matrix.stamp,
                                                    'built': time.time()}))
        if out.exists():
            shutil.rmtree(out)
        tmp.rename(out)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


def score(matrix: Matrix, ranks: Ranks, genes):
    """AUC of the gene numbers `genes` in every cell (float32)."""
    out = np.zeros(matrix.n, np.float64)
    k = ranks.k
    for g in genes:
        cells, values = matrix.gene(g)
        if not len(cells):
            continue
        q = cells.astype(np.float64) * ranks.S + (ranks.M - values.astype(np.float64))
        pos = np.searchsorted(ranks.keys, q)
        pos_ok = np.minimum(pos, len(ranks.keys) - 1)
        hit = (pos < len(ranks.keys)) & (np.asarray(ranks.keys[pos_ok]) == q)
        if not hit.any():
            continue
        p = pos_ok[hit]
        rank = np.asarray(ranks.above[p], np.float64) + (np.asarray(ranks.count[p], np.float64) + 1) / 2
        out[cells[hit]] += np.maximum(0.0, k - rank)
    return (out / (k * max(1, len(genes)))).astype(np.float32)


def seurat_score(matrix: Matrix, ranks: Ranks, genes, nbin=24, ctrl=100, seed=1):
    """Seurat's AddModuleScore of the gene numbers `genes` (float32), and how many control genes it used."""
    avg = np.asarray(ranks.average)
    # equal-size bins of average expression (cut_number), with Seurat's tiny jitter breaking ties
    rng = np.random.default_rng(seed)
    order = np.argsort(avg + rng.standard_normal(len(avg)) / 1e30, kind='stable')
    bins = np.empty(len(avg), np.int64)
    bins[order] = np.arange(len(avg)) * nbin // len(avg)
    controls = set()
    for g in genes:
        pool = np.flatnonzero(bins == bins[g])
        controls.update(rng.choice(pool, size=min(ctrl, len(pool)), replace=False).tolist())

    def mean_of(gene_numbers):
        total = np.zeros(matrix.n)
        for g in gene_numbers:
            cells, values = matrix.gene(g)
            if len(cells):
                total[cells] += matrix.lognorm(cells, values)
        return total / max(1, len(gene_numbers))
    return (mean_of(genes) - mean_of(sorted(controls))).astype(np.float32), len(controls)


# ---------------------------------------------------------------- gene names

QUOTES = re.compile(r"[\"'`‘’“”«»\[\](){}<>]")


def parse_genes(text):
    """Gene names from pasted text: spaces, new lines, commas, semicolons or tabs between them; quotes and brackets
    removed; each name once."""
    seen, out = set(), []
    for token in re.split(r"[\s,;|]+", QUOTES.sub(' ', str(text or ''))):
        token = token.strip('.:')
        if token and token.lower() not in seen:
            seen.add(token.lower())
            out.append(token)
    return out


def resolve(names, wanted):
    """Match `wanted` to the matrix's features: exactly, else ignoring case (MYH11 -> Myh11 in mouse data), else by an
    Ensembl id without its version. Returns (indices, used names, {given: used} conversions, missing)."""
    exact = {n: i for i, n in enumerate(names)}
    lower = {}
    for i, n in enumerate(names):
        lower.setdefault(n.lower(), i)
    stripped = {}
    for i, n in enumerate(names):
        if re.fullmatch(r'ENS[A-Z]*G\d+(\.\d+)?', n):
            stripped.setdefault(n.split('.')[0].upper(), i)
    idx, used, converted, missing = [], [], {}, []
    for w in wanted:
        i = exact.get(w)
        if i is None:
            i = lower.get(w.lower())
            if i is None and re.fullmatch(r'ENS[A-Za-z]*G\d+(\.\d+)?', w):
                i = stripped.get(w.split('.')[0].upper())
            if i is not None:
                converted[w] = names[i]
        if i is None:
            missing.append(w)
        elif i not in idx:
            idx.append(i)
            used.append(names[i])
    return idx, used, converted, missing


# ---------------------------------------------------------------- the hub's service

class ModuleScores:
    """Rank caches (built in the background, one at a time) and saved scores, under `state`."""

    def __init__(self, state: Path):
        self.cache_dir = state / 'module-cache'
        self.saved_dir = state / 'module-scores'
        self.building = {}  # cache key -> {"progress", "error", "started"}
        self.lock = threading.Lock()
        self.loaded = {}  # cache key -> Ranks

    @staticmethod
    def folder_key(folder: Path):
        import hashlib
        return hashlib.sha1(str(folder.resolve()).encode()).hexdigest()[:16]

    def cache_key(self, folder, matrix):
        return f'{self.folder_key(folder)}-{re.sub(r"[^A-Za-z0-9_.-]", "_", matrix.key)}'

    def ranks(self, folder: Path, matrix: Matrix):
        """The ready cache, or None while it is built (started here when missing or out of date)."""
        key = self.cache_key(folder, matrix)
        out = self.cache_dir / key
        with self.lock:
            r = self.loaded.get(key)
            if r and r.stamp == matrix.stamp:
                return r
            if (out / 'ranks.json').is_file():
                try:
                    r = Ranks(out)
                    if r.stamp == matrix.stamp and json.loads((out / 'ranks.json').read_text()).get('version') == CACHE_VERSION:
                        self.loaded[key] = r
                        return r
                except (OSError, ValueError, KeyError):
                    pass
            state = self.building.get(key)
            if state and not state.get('error'):
                return None
            self.building[key] = {'progress': 0.0, 'started': time.time()}

        def run():
            try:
                build(matrix, out, progress=lambda f: self.building[key].update(progress=round(f, 3)))
                with self.lock:
                    self.building.pop(key, None)
            except Exception as exc:  # shown to the page
                with self.lock:
                    self.building[key] = {'progress': 0.0, 'error': str(exc)}
        threading.Thread(target=run, daemon=True).start()
        return None

    def status(self, folder, matrix):
        return self.building.get(self.cache_key(folder, matrix)) or {'progress': 0.0}

    # saved scores: <saved_dir>/<folder key>/index.json + <id>.f32

    def saved(self, folder: Path):
        index = self.saved_dir / self.folder_key(folder) / 'index.json'
        try:
            return json.loads(index.read_text())
        except (OSError, ValueError):
            return []

    def save(self, folder: Path, entry: dict, values: np.ndarray):
        where = self.saved_dir / self.folder_key(folder)
        where.mkdir(parents=True, exist_ok=True)
        with self.lock:
            items = self.saved(folder)
            entry['id'] = secrets.token_hex(6)
            values.astype('<f4').tofile(where / f'{entry["id"]}.f32')
            items = [x for x in items if not (x['name'].lower() == entry['name'].lower() and x.get('assay') == entry.get('assay')
                                               and x.get('owner') == entry.get('owner'))] + [entry]
            self._write(where, items)
        return entry

    def delete(self, folder: Path, score_id: str, owner: str):
        where = self.saved_dir / self.folder_key(folder)
        with self.lock:
            items = self.saved(folder)
            item = next((x for x in items if x['id'] == score_id), None)
            if not item:
                raise KeyError(score_id)
            if item.get('owner') != owner:
                raise PermissionError(f'Only {item.get("ownerName") or "its workspace"} can delete this score')
            (where / f'{score_id}.f32').unlink(missing_ok=True)
            self._write(where, [x for x in items if x['id'] != score_id])

    def values_file(self, folder: Path, score_id: str):
        if not re.fullmatch(r'[0-9a-f]{12}', score_id):
            raise KeyError(score_id)
        return self.saved_dir / self.folder_key(folder) / f'{score_id}.f32'

    @staticmethod
    def _write(where, items):
        tmp = where / f'index.json.{secrets.token_hex(3)}'
        tmp.write_text(json.dumps(items, indent=1))
        tmp.replace(where / 'index.json')
