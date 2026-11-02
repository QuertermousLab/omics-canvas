"""Expression of chosen cells, for the spatial and single-cell viewers and the hub's API.

- `cells x genes matrices`: the values of some genes in some cells, as CSV / TSV (optionally gzip) or a 10x-style
  Matrix Market folder (zip): raw counts or log-normalized values (LogNormalize, scale 10,000, as the viewers show).
- `distribution`: one gene's (or a module score's) values per group of cells, for violin and ridge plots: a density
  (Gaussian kernel, R's default bandwidth bw.nrd0, trimmed to the group's range), quartiles and a sample of points.

Cells are picked by number (row of cells.bin), cell id (cell_ids.txt), a bit mask, and/or annotation values; genes by
name (module_score.resolve: case and Ensembl versions forgiven). The matrix (expr.bin) is stored gene by gene, so a
matrix reads only the genes asked for.
"""

import base64
import json
import math
import re
import zipfile
import zlib
from pathlib import Path

import numpy as np

import module_score

DENSE_MAX = 200_000_000  # cell x gene values in one CSV / TSV
MAX_GROUPS = 80  # violins / ridges in one plot (the largest groups)
DTYPES = {'uint8': '<u1', 'uint16': '<u2', 'float32': '<f4'}


class Problem(ValueError):
    """A request that cannot be answered as asked (the hub turns it into a 400)."""


class Cells:
    """The cells of a slide folder: their ids and annotations (obs, columns of cells.bin)."""

    def __init__(self, folder: Path):
        self.folder = folder
        self.meta = json.loads((folder / 'slide.json').read_text())
        self.n = int(self.meta['nCells'])
        self.obs = self.meta.get('obs') or []
        self._ids = None

    def ids(self):
        if self._ids is None:
            try:
                ids = (self.folder / 'cell_ids.txt').read_text().split('\n')[:self.n]
            except OSError:
                ids = []
            self._ids = ids if len(ids) == self.n else [str(i) for i in range(self.n)]
        return self._ids

    def spec(self, name):
        name = str(name or '').strip()
        for test in (lambda o: o['key'] == name, lambda o: o.get('label') == name,
                     lambda o: o['key'].lower() == name.lower() or str(o.get('label', '')).lower() == name.lower()):
            o = next((o for o in self.obs if test(o)), None)
            if o:
                return o
        raise Problem(f'No annotation {name!r}; there are: {", ".join(o["key"] for o in self.obs if not o.get("hidden"))[:600]}')

    def column(self, o):
        return np.fromfile(self.folder / 'cells.bin', dtype=DTYPES[o['dtype']], count=self.n, offset=o['offset'])

    def text(self, o, rows):
        """Annotation `o` of cell numbers `rows` as strings."""
        v = self.column(o)[rows]
        if o['type'] == 'category':
            cats = np.array([str(c) for c in o['categories']] + [''], dtype=object)
            return cats[np.minimum(v.astype(np.int64), len(cats) - 1)]
        return np.array(['' if not math.isfinite(x) else f'{x:.6g}' for x in v.tolist()], dtype=object)

    def public(self):
        return [{'key': o['key'], 'label': o.get('label') or o['key'], 'type': o['type'],
                 **({'categories': o['categories']} if o['type'] == 'category' else {'min': o.get('min'), 'max': o.get('max')})}
                for o in self.obs if not o.get('hidden')]


def as_list(x):
    if x is None or x == '':
        return []
    return list(x) if isinstance(x, (list, tuple)) else [x]


def category_codes(o, wanted):
    """Category numbers of annotation `o` for the names `wanted` (exact, else ignoring case; a name with commas that
    is no category is split at them, for GET links like values=SMC,EC)."""
    cats = [str(c) for c in o['categories']]
    exact = {c: i for i, c in enumerate(cats)}
    lower = {c.lower(): i for i, c in reversed(list(enumerate(cats)))}
    codes, missing = [], []
    for w in as_list(wanted):
        w = str(w).strip()
        parts = [w] if w in exact or w.lower() in lower or ',' not in w else [p.strip() for p in w.split(',') if p.strip()]
        for p in parts:
            i = exact.get(p, lower.get(p.lower()))
            (missing.append(p) if i is None else codes.append(i))
    if missing:
        raise Problem(f'{o["key"]} has no {", ".join(map(repr, missing[:10]))}; its values: {", ".join(cats[:40])}'
                      + (' …' if len(cats) > 40 else ''))
    return codes


def select(cells: Cells, indices=None, ids=None, mask=None, where=None):
    """Sorted cell numbers matching every criterion given (all cells when none), and notes on what did not match.
    where: {annotation: [category names] | {"min": x, "max": y}}."""
    keep = np.ones(cells.n, bool)
    info = {}
    if mask:
        bits = np.unpackbits(np.frombuffer(base64.b64decode(mask), np.uint8), bitorder='little')[:cells.n]
        if len(bits) < cells.n:
            raise Problem(f'The mask has {len(bits)} bits for {cells.n} cells')
        keep &= bits.astype(bool)
    if indices is not None and as_list(indices):
        idx = np.asarray([int(i) for i in as_list(indices)], np.int64)
        if len(idx) and (idx.min() < 0 or idx.max() >= cells.n):
            raise Problem(f'Cell numbers run from 0 to {cells.n - 1}')
        m = np.zeros(cells.n, bool)
        m[idx] = True
        keep &= m
    wanted = [str(x) for x in as_list(ids)]
    if len(wanted) == 1:
        wanted = [t for t in re.split(r'[\s,;]+', wanted[0]) if t]
    if wanted:
        pos = {c: i for i, c in enumerate(cells.ids())}
        found, missing = [], []
        lower = None
        for w in wanted:
            w = w.strip('"\'')
            i = pos.get(w)
            if i is None:
                lower = lower or {c.lower(): i for c, i in pos.items()}
                i = lower.get(w.lower())
            (missing.append(w) if i is None else found.append(i))
        if not found:
            raise Problem(f'None of the {len(wanted)} cell ids is in this sample (e.g. {", ".join(cells.ids()[:3])})')
        m = np.zeros(cells.n, bool)
        m[found] = True
        keep &= m
        if missing:
            info['missingCells'] = len(missing)
            info['missingCellExamples'] = missing[:10]
    for name, rule in (where or {}).items():
        o = cells.spec(name)
        col = cells.column(o)
        if o['type'] == 'category':
            keep &= np.isin(col, category_codes(o, rule))
        elif isinstance(rule, dict):
            if rule.get('min') is not None:
                keep &= col >= float(rule['min'])
            if rule.get('max') is not None:
                keep &= col <= float(rule['max'])
        else:
            raise Problem(f'{o["key"]} is a number: give {{"min": …, "max": …}}')
    rows = np.flatnonzero(keep)
    if not len(rows):
        raise Problem('No cell matches')
    return rows, info


def genes_of(matrix, genes, limit=None):
    """Feature numbers for the names asked (all features when none), and what was matched."""
    wanted = module_score.parse_genes(' '.join(map(str, genes)) if isinstance(genes, (list, tuple)) else genes)
    if not wanted:
        return list(range(len(matrix.names))), {'given': 0, 'used': len(matrix.names), 'converted': {}, 'missing': []}
    if limit and len(wanted) > limit:
        raise Problem(f'{len(wanted)} genes: at most {limit} at once (or none for all, as Matrix Market)')
    idx, used, converted, missing = module_score.resolve(matrix.names, wanted)
    if not idx:
        raise Problem(f'None of the {len(wanted)} genes is in this dataset ({", ".join(wanted[:8])}…)')
    return idx, {'given': len(wanted), 'used': len(idx), 'converted': converted, 'missing': missing}


def gene_values(matrix, g, value):
    cells, values = matrix.gene(g)
    if value == 'counts':
        return cells, values
    return cells, matrix.lognorm(cells, values)


def check_value(matrix, value):
    if value not in ('counts', 'normalized'):
        raise Problem('value is "counts" or "normalized"')
    if value == 'counts' and matrix.float:
        raise Problem(f'{matrix.label} keeps no counts here (values as stored): export "normalized"')


# ---------------------------------------------------------------- matrix files

def _quote(s, sep):
    s = str(s)
    return '"' + s.replace('"', '""') + '"' if (sep in s or '"' in s or '\n' in s) else s


def _formatter(value, matrix):
    if value == 'counts':
        return lambda a: list(map(str, a.astype(np.int64).tolist()))
    return lambda a: list(map('%.5g'.__mod__, a.tolist()))


def _triplets(matrix, genes, rows, value):
    """(row in `rows`, gene position, value) of the non-zero values, sorted by row."""
    pos = np.full(matrix.n, -1, np.int64)
    pos[rows] = np.arange(len(rows))
    r_, c_, v_ = [], [], []
    for j, g in enumerate(genes):
        cells, values = gene_values(matrix, g, value)
        r = pos[cells]
        k = (r >= 0) & (values != 0)
        r_.append(r[k])
        c_.append(np.full(int(k.sum()), j, np.int32))
        v_.append(np.asarray(values, np.float64)[k])
    r = np.concatenate(r_) if r_ else np.zeros(0, np.int64)
    c = np.concatenate(c_) if c_ else np.zeros(0, np.int32)
    v = np.concatenate(v_) if v_ else np.zeros(0)
    order = np.argsort(r, kind='stable')
    return r[order], c[order], v[order]


def dense_text(matrix, cells, genes, rows, value, sep=',', orient='cells', meta=()):
    """Lines of a delimited table: cells as rows (id, annotations, genes) or genes as rows (gene, cells)."""
    if len(rows) * len(genes) > DENSE_MAX:
        raise Problem(f'{len(rows):,} cells × {len(genes):,} genes is too large for a table: pick fewer, or Matrix Market')
    fmt = _formatter(value, matrix)
    ids = cells.ids()
    names = [matrix.names[g] for g in genes]

    def lines():
        if orient == 'genes':
            yield sep.join([_quote('gene', sep)] + [_quote(ids[i], sep) for i in rows]) + '\n'
            pos = np.full(matrix.n, -1, np.int64)
            pos[rows] = np.arange(len(rows))
            for name, g in zip(names, genes):
                cells_, values = gene_values(matrix, g, value)
                r = pos[cells_]
                k = (r >= 0) & (values != 0)
                out = np.full(len(rows), '0', dtype=object)
                if k.any():
                    out[r[k]] = fmt(np.asarray(values)[k])
                yield _quote(name, sep) + sep + sep.join(out.tolist()) + '\n'
            return
        cols = [cells.spec(m) for m in meta]
        yield sep.join(_quote(x, sep) for x in ['cell'] + [o['key'] for o in cols] + names) + '\n'
        r, c, v = _triplets(matrix, genes, rows, value)
        starts = np.searchsorted(r, np.arange(len(rows) + 1))
        block = 2048
        for b0 in range(0, len(rows), block):
            b1 = min(len(rows), b0 + block)
            table = np.full((b1 - b0, len(genes)), '0', dtype=object)
            s0, s1 = starts[b0], starts[b1]
            if s1 > s0:
                table[r[s0:s1] - b0, c[s0:s1]] = fmt(v[s0:s1])
            heads = [[_quote(ids[i], sep)] for i in rows[b0:b1]]
            for o in cols:
                for h, t in zip(heads, cells.text(o, rows[b0:b1])):
                    h.append(_quote(t, sep))
            yield ''.join(sep.join(h + t) + '\n' for h, t in zip(heads, table.tolist()))
    return lines()


def gzipped(chunks):
    z = zlib.compressobj(6, zlib.DEFLATED, 31)
    for chunk in chunks:
        out = z.compress(chunk.encode() if isinstance(chunk, str) else chunk)
        if out:
            yield out
    yield z.flush()


class _Sink:
    """A write-only stream for zipfile: what was written is taken out piece by piece."""

    def __init__(self):
        self.buf, self.pos = bytearray(), 0

    def write(self, b):
        self.buf += b
        self.pos += len(b)
        return len(b)

    def tell(self):
        return self.pos

    def flush(self):
        pass

    def take(self):
        out = bytes(self.buf)
        self.buf.clear()
        return out


def market_zip(matrix, cells, genes, rows, value, meta=(), folder='matrix'):
    """A zip of a 10x-style Matrix Market folder: matrix.mtx (genes x cells), features.tsv, barcodes.tsv, and
    metadata.tsv with the annotations asked for."""
    fmt = _formatter(value, matrix)
    pos = np.full(matrix.n, -1, np.int64)
    pos[rows] = np.arange(len(rows))
    cols = [cells.spec(m) for m in meta]

    def nonzero(g):
        cells_, values = gene_values(matrix, g, value)
        r = pos[cells_]
        k = (r >= 0) & (values != 0)
        return r[k], np.asarray(values)[k]

    def chunks():
        sink = _Sink()
        with zipfile.ZipFile(sink, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            ids = cells.ids()
            with z.open(f'{folder}/barcodes.tsv', 'w', force_zip64=True) as f:
                for b0 in range(0, len(rows), 100_000):
                    f.write(''.join(ids[i] + '\n' for i in rows[b0:b0 + 100_000]).encode())
                    yield sink.take()
            with z.open(f'{folder}/features.tsv', 'w') as f:
                kind = 'Gene Expression' if matrix.type == 'rna' else matrix.label
                f.write(''.join(f'{matrix.names[g]}\t{matrix.names[g]}\t{kind}\n' for g in genes).encode())
            if cols:
                with z.open(f'{folder}/metadata.tsv', 'w', force_zip64=True) as f:
                    f.write(('\t'.join(['cell'] + [o['key'] for o in cols]) + '\n').encode())
                    for b0 in range(0, len(rows), 100_000):
                        part = rows[b0:b0 + 100_000]
                        texts = [cells.text(o, part) for o in cols]
                        f.write(''.join('\t'.join([ids[i]] + [str(t[j]).replace('\t', ' ') for t in texts]) + '\n'
                                        for j, i in enumerate(part)).encode())
                        yield sink.take()
            # the header needs the number of entries: counted first when only some cells are asked for
            if len(rows) == matrix.n and value == 'counts':
                nnz = int(sum(int(matrix.index[g, 1]) for g in genes))
            else:
                nnz = sum(len(nonzero(g)[0]) for g in genes)
            field = 'integer' if value == 'counts' else 'real'
            with z.open(f'{folder}/matrix.mtx', 'w', force_zip64=True) as f:
                f.write(f'%%MatrixMarket matrix coordinate {field} general\n%metadata_json: {{"value": "{value}"}}\n'
                        f'{len(genes)} {len(rows)} {nnz}\n'.encode())
                for j, g in enumerate(genes):
                    r, v = nonzero(g)
                    if len(r):
                        gi = str(j + 1)
                        f.write(''.join(f'{gi} {c} {x}\n' for c, x in zip((r + 1).tolist(), fmt(v))).encode())
                    if sink.buf and len(sink.buf) > 1 << 20:
                        yield sink.take()
        yield sink.take()
    return chunks()


# ---------------------------------------------------------------- distributions (violin and ridge plots)

def bw_nrd0(x):
    """R's default bandwidth (bw.nrd0)."""
    if len(x) < 2:
        return 1.0
    sd = float(np.std(x, ddof=1))
    q1, q3 = np.percentile(x, [25, 75])
    lo = min(sd, (q3 - q1) / 1.34)
    if not lo:
        lo = sd or abs(float(x[0])) or 1.0
    return 0.9 * lo * len(x) ** -0.2


def density(x, lo, hi, points=200):
    """Gaussian kernel density of `x` on `points` steps from lo to hi, zero outside x's own range (trimmed)."""
    fine = 2048
    width = (hi - lo) / fine or 1.0
    counts, _ = np.histogram(x, bins=fine, range=(lo, hi if hi > lo else lo + 1))
    sigma = bw_nrd0(x) / width
    half = int(min(fine, max(1, math.ceil(4 * sigma))))
    t = np.arange(-half, half + 1)
    kernel = np.exp(-0.5 * (t / max(sigma, 1e-9)) ** 2)
    kernel /= kernel.sum()
    smooth = np.convolve(counts, kernel)[half:half + fine] / (len(x) * width)
    grid = np.linspace(lo, hi, points)
    y = np.interp(grid, lo + (np.arange(fine) + 0.5) * width, smooth)
    y[(grid < x.min() - 1e-12) | (grid > x.max() + 1e-12)] = 0
    return y


def distribution(values, rows, groups=None, labels=('All cells',), nonzero=False, points=300, seed=0):
    """Per group (codes `groups` of the cells, or one group): n, cells above 0, mean, quartiles, density, points."""
    v = np.asarray(values, np.float64)[rows]
    g = np.zeros(len(rows), np.int64) if groups is None else np.asarray(groups, np.int64)[rows]
    finite = np.isfinite(v)
    v, g = v[finite], g[finite]
    if nonzero:
        keep = v != 0
        v_all, g_all = v, g
        v, g = v[keep], g[keep]
    else:
        v_all, g_all = v, g
    if not len(v):
        raise Problem('No values to plot' + (' above zero' if nonzero else ''))
    lo, hi = float(v.min()), float(v.max())
    if hi <= lo:
        hi = lo + 1
    sizes = np.bincount(g_all, minlength=len(labels))
    present = [c for c in range(len(labels)) if sizes[c]]
    dropped = 0
    if len(present) > MAX_GROUPS:
        biggest = set(sorted(present, key=lambda c: -sizes[c])[:MAX_GROUPS])
        dropped = len(present) - MAX_GROUPS
        present = [c for c in present if c in biggest]
    rng = np.random.default_rng(seed)
    out = []
    for c in present:
        x = v[g == c]
        everyone = v_all[g_all == c]
        item = {'code': int(c), 'name': str(labels[c]), 'cells': int(len(everyone)), 'n': int(len(x)),
                'expressing': int((everyone > 0).sum())}
        if len(x):
            q = np.percentile(x, [0, 25, 50, 75, 100])
            item.update(mean=float(x.mean()), min=float(q[0]), q1=float(q[1]), median=float(q[2]), q3=float(q[3]),
                        max=float(q[4]), density=[round(float(y), 6) for y in density(x, lo, hi)])
            pick = x if len(x) <= points else rng.choice(x, points, replace=False)
            item['points'] = [round(float(p), 4) for p in pick]
        out.append(item)
    return {'range': [lo, hi], 'grid': 200, 'groups': out, 'droppedGroups': dropped, 'nonzero': bool(nonzero)}
