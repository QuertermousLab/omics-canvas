#!/usr/bin/env python3
"""Convert a Xenium output bundle (+ optional Seurat objects) into the ATHENA spatial viewer format.

The viewer reads a handful of flat binary files with HTTP range requests, so a slide is a
single folder of ~10 files that can live on Google Drive (or any file store) unchanged:

  slide.json        metadata: genes, cell annotations, image pyramid, file layout
  cells.bin         float32 x/y (full-resolution image pixels), then one array per annotation
  cell_ids.txt      one Xenium cell_id per line, same order as cells.bin
  expr.bin          per gene: uint32 cell indices, then uint16 counts (4-byte aligned blocks)
  expr.idx          float64 [byte offset, nnz] per gene, same order as slide.json "genes"
  boundaries.bin    uint32 start index per cell (n+1), then float32 x/y vertices (pixels)
  img_<c>.bin/.idx  JPEG tiles of morphology channel c; .idx = float64 [offset, length] per tile

Usage:
  python xenium_to_viewer.py BUNDLE OUT_DIR --id fresh --title "Fresh frozen" \
      --obs fresh_processed.rds --region "Cropped region=fresh_processed_crop.rds"

--obs takes a Seurat .rds (meta.data and UMAP are read without R) or a CSV whose first column
is the cell id. With --obs, only cells present in the first --obs table are kept (the
analysed, QC-passed cells) unless --keep-all-cells is given.
"""
import argparse
import io
import json
import re
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

TILE = 512
MAX_CATEGORIES = 200
CHANNEL_COLORS = ['#3b6cff', '#3ddc84', '#ffd23d', '#ff4d4d', '#d16bff', '#4dd9ff']


# ----------------------------------------------------------------- Seurat (.rds) reading

def _r_vector(obj):
    """rds2py's dict for an R vector -> (numpy/list values, factor levels or None)."""
    data = obj.get('data')
    levels = (obj.get('attributes') or {}).get('levels')
    return data, (levels['data'] if levels else None)


def _rds_attr(obj, name):
    return (obj.get('attributes') or {}).get(name)


def read_seurat(path):
    """meta.data (pandas DataFrame indexed by cell) and UMAP (DataFrame or None) from a Seurat .rds."""
    from rds2py import parse_rds  # imported lazily: only needed for .rds inputs

    root = parse_rds(str(path))
    md = _rds_attr(root, 'meta.data')
    names = _rds_attr(md, 'names')['data']
    cells = _rds_attr(md, 'row.names')['data']
    cols = {}
    for name, col in zip(names, md['data']):
        data, levels = _r_vector(col)
        if levels is not None:
            codes = np.asarray(data)
            labels = np.array(list(levels) + [None], dtype=object)
            cols[name] = pd.Categorical(labels[np.where(codes > 0, codes - 1, len(levels))], categories=list(levels))
        else:
            cols[name] = np.asarray(data) if not isinstance(data, list) else data
    meta = pd.DataFrame(cols, index=pd.Index(cells, name='cell_id'))

    umap = None
    red = _rds_attr(root, 'reductions')
    if red and _rds_attr(red, 'names'):
        for rname, robj in zip(_rds_attr(red, 'names')['data'], red['data']):
            if rname.lower() != 'umap':
                continue
            emb = _rds_attr(robj, 'cell.embeddings')
            dim = _rds_attr(emb, 'dim')['data']
            mat = np.asarray(emb['data'], dtype=np.float32).reshape(dim[1], dim[0]).T  # R is column-major
            dimnames = _rds_attr(emb, 'dimnames')
            rows = dimnames['data'][0]['data'] if dimnames else cells
            umap = pd.DataFrame(mat[:, :2], index=rows, columns=['x', 'y'])
    return meta, umap


def read_obs(path):
    path = Path(path)
    if path.suffix.lower() == '.rds':
        return read_seurat(path)
    df = pd.read_csv(path)
    return df.set_index(df.columns[0]), None


# ----------------------------------------------------------------- annotations

# readable names for columns Seurat always creates; anything else can be named with --label
SEURAT_LABELS = {
    'seurat_clusters': 'Seurat clusters', 'nCount_Xenium': 'Transcripts (Seurat)',
    'segmentation_method': 'Segmentation method', 'prediction.score.max': 'Label transfer score (max)',
}


def default_label(col):
    if col in SEURAT_LABELS:
        return SEURAT_LABELS[col]
    if col.startswith('prediction.score.'):
        return f'Label transfer score: {col[len("prediction.score."):]}'
    if col.endswith('_snn_res.') or '_snn_res.' in col:
        assay, res = col.split('_snn_res.')
        return f'Clusters ({assay}, resolution {res})'
    return col


class Columns:
    """Accumulates per-cell arrays into cells.bin and describes them for slide.json."""

    def __init__(self, n):
        self.n = n
        self.chunks = []
        self.offset = 0
        self.specs = []

    def _add(self, arr):
        arr = np.ascontiguousarray(arr)
        off = self.offset
        self.chunks.append(arr.tobytes())
        self.offset += arr.nbytes
        pad = (-self.offset) % 4
        if pad:
            self.chunks.append(b'\0' * pad)
            self.offset += pad
        return off

    def raw(self, arr):
        return self._add(arr)

    def add(self, key, values, label=None, group=None, hidden=False):
        s = pd.Series(np.asarray(values) if not isinstance(values, pd.Series) else values.reset_index(drop=True))
        label = label or key
        numeric = pd.api.types.is_numeric_dtype(s) and not isinstance(s.dtype, pd.CategoricalDtype)
        if numeric and not pd.api.types.is_bool_dtype(s):
            uniq = pd.unique(s.dropna())
            integer_like = len(uniq) <= 50 and np.all(np.mod(uniq, 1) == 0)
            if not integer_like:
                v = s.to_numpy(dtype=np.float32)
                finite = v[np.isfinite(v)]
                spec = dict(key=key, label=label, type='number', dtype='float32', offset=self._add(v),
                            min=float(finite.min()) if finite.size else 0, max=float(finite.max()) if finite.size else 0)
                if group: spec['group'] = group
                if hidden: spec['hidden'] = True
                self.specs.append(spec)
                return
            s = s.astype('Int64').astype(str).replace('<NA>', None)
        if not isinstance(s.dtype, pd.CategoricalDtype):
            s = s.astype(object).where(s.notna(), None)
            uniq = pd.unique(s.dropna())
            if len(uniq) > MAX_CATEGORIES:
                print(f'  skip {key}: {len(uniq)} distinct values', file=sys.stderr)
                return
            try:
                cats = sorted(uniq, key=lambda x: (0, float(x)) if str(x).lstrip('-').replace('.', '', 1).isdigit() else (1, str(x)))
            except Exception:
                cats = sorted(map(str, uniq))
            s = pd.Series(pd.Categorical(s, categories=cats))
        else:
            s = s.cat.remove_unused_categories() if len(s.cat.categories) > MAX_CATEGORIES else s
        cats = [str(c) for c in s.cat.categories]
        if len(cats) < 2 and not s.isna().any():
            return  # a single value tells the viewer nothing
        codes = np.asarray(s.cat.codes, dtype=np.int32)
        if (codes < 0).any():  # missing values become an explicit last category
            cats.append('NA')
            codes = np.where(codes < 0, len(cats) - 1, codes)
        dtype = 'uint8' if len(cats) < 256 else 'uint16'
        spec = dict(key=key, label=label, type='category', dtype=dtype,
                    offset=self._add(codes.astype(dtype)), categories=cats)
        if group: spec['group'] = group
        self.specs.append(spec)

    def bytes(self):
        return b''.join(self.chunks)


# ----------------------------------------------------------------- morphology image tiles

def channel_names(tif_path):
    import re
    import tifffile
    with tifffile.TiffFile(tif_path) as t:
        return re.findall(r'<Channel[^>]*Name="([^"]+)"', t.ome_metadata or '')


def tile_channel(args):
    """Write img_<c>.bin/.idx for channel c, every pyramid level from min_level up."""
    tif_path, c, out_dir, min_level, quality = args
    import tifffile
    import zarr
    from PIL import Image

    with tifffile.TiffFile(tif_path) as t:
        levels = [lv.shape[-2:] for lv in t.series[0].levels]

    # contrast window from a low-resolution level, applied to every level
    probe_level = min(3, len(levels) - 1)
    probe = np.asarray(zarr.open(tifffile.imread(tif_path, aszarr=True, level=probe_level), mode='r')[c])
    nz = probe[probe > 0]
    lo = float(np.percentile(nz, 0.5)) if nz.size else 0.0
    hi = float(np.percentile(nz, 99.95)) if nz.size else 1.0
    hi = max(hi, lo + 1)

    entries, out = [], open(out_dir / f'img_{c}.bin', 'wb')
    offset, info = 0, []
    for L in range(min_level, len(levels)):
        h, w = levels[L]
        rows, cols = -(-h // TILE), -(-w // TILE)
        info.append(dict(level=L, width=int(w), height=int(h), rows=rows, cols=cols))
        z = zarr.open(tifffile.imread(tif_path, aszarr=True, level=L), mode='r')
        for ty0 in range(0, rows, 2):  # read 1024-row strips (the source tile height)
            strip = np.asarray(z[c, ty0 * TILE:min(h, (ty0 + 2) * TILE), :]).astype(np.float32)
            strip = np.clip((strip - lo) * (255.0 / (hi - lo)), 0, 255).astype(np.uint8)
            for ty in range(ty0, min(rows, ty0 + 2)):
                band = strip[(ty - ty0) * TILE:(ty - ty0 + 1) * TILE]
                for tx in range(cols):
                    tile = band[:, tx * TILE:(tx + 1) * TILE]
                    if tile.size == 0 or tile.max() <= 2:
                        entries.append((offset, 0))
                        continue
                    if tile.shape != (TILE, TILE):  # pad edge tiles so every tile is TILE x TILE
                        full = np.zeros((TILE, TILE), np.uint8)
                        full[:tile.shape[0], :tile.shape[1]] = tile
                        tile = full
                    buf = io.BytesIO()
                    Image.fromarray(tile, 'L').save(buf, 'JPEG', quality=quality)
                    data = buf.getvalue()
                    out.write(data)
                    entries.append((offset, len(data)))
                    offset += len(data)
        print(f'  channel {c} level {L}: {rows}x{cols} tiles, {offset / 1e6:.0f} MB so far', flush=True)
    out.close()
    np.asarray(entries, dtype=np.float64).tofile(out_dir / f'img_{c}.idx')
    return c, info, lo, hi, offset


# ----------------------------------------------------------------- main

def find_he(b: Path, a):
    """(H&E OME-TIFF, 3 x 3 matrix H&E pixel -> morphology pixel) or None. Xenium Explorer writes the matrix in that
    direction; the inverse is taken when only it lands the image on the cells."""
    tif = a.he_image or next((f for f in sorted(b.glob('*.tif*')) if re.search(r'(^|[_.-])(he|h&e)([_.-]|$)|he_image', f.name, re.I)), None)
    csv = a.he_alignment or next(iter(sorted(b.glob('*imagealignment*.csv'))), None)
    if not tif:
        return None
    if not csv:
        print(f'H&E {tif.name} found but no alignment (*imagealignment.csv from Xenium Explorer): not added', flush=True)
        return None
    rows = [r for r in (line.strip() for line in csv.read_text().splitlines()) if r and re.match(r'^[-+0-9.eE, ]+$', r)]
    m = np.array([[float(v) for v in r.split(',')] for r in rows[:3]], dtype=np.float64)
    if m.shape != (3, 3):
        print(f'{csv.name}: not a 3 x 3 matrix: H&E not added', flush=True)
        return None
    return tif, m


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('bundle', type=Path, help='Xenium output folder')
    ap.add_argument('out', type=Path, help='output slide folder (created)')
    ap.add_argument('--id', required=True, help='slide id used in URLs, e.g. "fresh"')
    ap.add_argument('--title', help='display name (default: the id)')
    ap.add_argument('--obs', action='append', default=[], help='Seurat .rds or CSV with per-cell annotations (repeatable)')
    ap.add_argument('--region', action='append', default=[],
                    help='"Name=file.rds|csv": preset region made of the cells in that object (repeatable)')
    ap.add_argument('--label', action='append', default=[],
                    help='"column=Display name" shown in the viewer, e.g. "predicted.celltype=Cell type (RCTD)" (repeatable)')
    ap.add_argument('--default', help='annotation column selected when the viewer opens')
    ap.add_argument('--keep-all-cells', action='store_true', help='keep cells missing from the first --obs table')
    ap.add_argument('--channels', default='all', help='morphology channels to tile, e.g. "0,1" (default all)')
    ap.add_argument('--min-level', type=int, default=0, help='finest pyramid level to keep (1 halves the image size)')
    ap.add_argument('--jpeg-quality', type=int, default=85)
    ap.add_argument('--no-image', action='store_true')
    ap.add_argument('--he-image', type=Path, help='post-Xenium H&E (pyramidal OME-TIFF); default: *he_image*.tif in the bundle')
    ap.add_argument('--he-alignment', type=Path, help='Xenium Explorer alignment (3 x 3, H&E pixel -> morphology pixel); '
                                                      'default: *imagealignment.csv in the bundle')
    ap.add_argument('--no-boundaries', action='store_true')
    ap.add_argument('--workers', type=int, default=4)
    a = ap.parse_args()

    b, out = a.bundle, a.out
    out.mkdir(parents=True, exist_ok=True)
    exp = json.loads((b / 'experiment.xenium').read_text())
    px = float(exp.get('pixel_size', 0.2125))

    # cells ---------------------------------------------------------------
    cells = pq.read_table(b / 'cells.parquet').to_pandas().set_index('cell_id')
    obs_tables = []
    for p in a.obs:
        print(f'reading {p}')
        obs_tables.append(read_obs(p))
    if obs_tables and not a.keep_all_cells:
        keep = cells.index.intersection(obs_tables[0][0].index, sort=False)
        print(f'keeping {len(keep)} of {len(cells)} cells (present in {a.obs[0]})')
        cells = cells.loc[keep]
    ids = cells.index.to_numpy()
    n = len(ids)
    pos = np.empty(n * 2, np.float32)
    pos[0::2] = cells['x_centroid'].to_numpy() / px
    pos[1::2] = cells['y_centroid'].to_numpy() / px

    # expression (cell_feature_matrix, genes only) --------------------------
    from scipy.io import mmread
    mdir = b / 'cell_feature_matrix'
    feats = pd.read_csv(mdir / 'features.tsv.gz', sep='\t', header=None)
    barcodes = pd.read_csv(mdir / 'barcodes.tsv.gz', header=None)[0].to_numpy()
    print('reading expression matrix')
    m = mmread(str(mdir / 'matrix.mtx.gz'), spmatrix=False).tocsr()  # genes x barcodes
    gene_rows = np.flatnonzero(feats[2].to_numpy() == 'Gene Expression')
    genes = feats[1].to_numpy()[gene_rows]
    col_of = pd.Series(np.arange(len(barcodes)), index=barcodes)
    m = m[gene_rows][:, col_of.loc[ids].to_numpy()].tocsr()  # genes x kept cells, cell order = ids
    m.sort_indices()
    total = np.asarray(m.sum(axis=0)).ravel().astype(np.float32)
    if m.data.max() > 65535:
        sys.exit('counts above 65535 do not fit uint16')

    idx = np.zeros((len(genes), 2), np.float64)
    with open(out / 'expr.bin', 'wb') as f:
        off = 0
        for g in range(len(genes)):
            s, e = m.indptr[g], m.indptr[g + 1]
            block = m.indices[s:e].astype(np.uint32).tobytes() + m.data[s:e].astype(np.uint16).tobytes()
            block += b'\0' * ((-len(block)) % 4)
            f.write(block)
            idx[g] = (off, e - s)
            off += len(block)
    idx.tofile(out / 'expr.idx')
    print(f'expression: {len(genes)} genes, {m.nnz} non-zero, {off / 1e6:.0f} MB')

    # annotations ---------------------------------------------------------
    labels = dict(spec.split('=', 1) for spec in a.label)
    cols = Columns(n)
    cols.raw(pos)
    cols.add('total_counts', total, 'Total counts (genes)', group='Xenium', hidden=True)
    graph = b / 'analysis/clustering/gene_expression_graphclust/clusters.csv'
    for i, (meta, _) in enumerate(obs_tables):
        meta = meta.reindex(ids)
        src = Path(a.obs[i]).stem
        group = ('Seurat' if len(a.obs) == 1 else f'Seurat · {src}') if a.obs[i].lower().endswith('.rds') else src
        for col in meta.columns:
            if col.startswith(('nCount_', 'nFeature_')) and col != 'nCount_Xenium':
                continue
            if meta[col].isna().all():
                continue
            key = col if i == 0 else f'{src}:{col}'
            cols.add(key, meta[col], labels.get(col) or default_label(col), group=group)
    if graph.exists():
        cl = pd.read_csv(graph).set_index('Barcode')['Cluster'].reindex(ids)
        cols.add('xenium_graphclust', cl.to_numpy(), 'Xenium graph-based clusters', group='Xenium')
    for key, label in [('cell_area', 'Cell area (µm²)'), ('nucleus_area', 'Nucleus area (µm²)'), ('transcript_counts', 'Transcripts')]:
        if key in cells:
            cols.add(key, cells[key].to_numpy(dtype=np.float64), label, group='Xenium')

    radius = np.sqrt(cells['cell_area'].to_numpy(dtype=np.float32) / np.pi) / px if 'cell_area' in cells else np.full(n, 5 / px, np.float32)
    radius_off = cols.raw(np.nan_to_num(radius, nan=5 / px).astype(np.float32))

    embeddings = []
    for i, (_, umap) in enumerate(obs_tables):
        if umap is None:
            continue
        u = umap.reindex(ids)
        arr = np.empty(n * 2, np.float32)
        arr[0::2] = u['x'].to_numpy(dtype=np.float32)
        arr[1::2] = u['y'].to_numpy(dtype=np.float32)
        embeddings.append(dict(key='umap', label='UMAP', offset=cols.raw(arr)))
        break

    (out / 'cells.bin').write_bytes(cols.bytes())
    (out / 'cell_ids.txt').write_text('\n'.join(map(str, ids)) + '\n')

    regions = []
    pos_of = pd.Series(np.arange(n), index=ids)
    for spec in a.region:
        name, _, path = spec.partition('=')
        meta, _ = read_obs(path)
        members = pos_of.reindex(meta.index).dropna().astype(int).to_numpy()
        regions.append(dict(name=name, cells=sorted(members.tolist())))
        print(f'region {name}: {len(members)} cells')

    # boundaries ------------------------------------------------------------
    boundaries = None
    if not a.no_boundaries and (b / 'cell_boundaries.parquet').exists():
        bt = pq.read_table(b / 'cell_boundaries.parquet', columns=['cell_id', 'vertex_x', 'vertex_y']).to_pandas()
        bt['i'] = pos_of.reindex(bt['cell_id'].to_numpy()).to_numpy()
        bt = bt.dropna(subset=['i'])
        bt['i'] = bt['i'].astype(np.int64)
        bt = bt.sort_values('i', kind='stable')
        counts = np.bincount(bt['i'].to_numpy(), minlength=n)
        starts = np.zeros(n + 1, np.uint32)
        np.cumsum(counts, out=starts[1:])
        xy = np.empty(len(bt) * 2, np.float32)
        xy[0::2] = bt['vertex_x'].to_numpy() / px
        xy[1::2] = bt['vertex_y'].to_numpy() / px
        with open(out / 'boundaries.bin', 'wb') as f:
            f.write(starts.tobytes())
            f.write(xy.tobytes())
        boundaries = dict(file='boundaries.bin', vertices=int(len(bt)))
        print(f'boundaries: {len(bt)} vertices')

    # image -------------------------------------------------------------------
    image = None
    tif = b / 'morphology_focus' / 'morphology_focus_0000.ome.tif'
    if not a.no_image and tif.exists():
        names = channel_names(tif)
        chans = range(len(names)) if a.channels == 'all' else [int(c) for c in a.channels.split(',')]
        jobs = [(tif, c, out, a.min_level, a.jpeg_quality) for c in chans]
        with ProcessPoolExecutor(max_workers=a.workers) as ex:
            results = sorted(ex.map(tile_channel, jobs))
        image = dict(tileSize=TILE, format='jpeg', levels=results[0][1], channels=[
            dict(name=names[c], file=f'img_{c}', color=CHANNEL_COLORS[c % len(CHANNEL_COLORS)], visible=c == 0,
                 window=[round(lo, 1), round(hi, 1)])
            for c, _, lo, hi, _ in results])
        start = 0
        for lv in image['levels']:
            lv['start'] = start
            start += lv['rows'] * lv['cols']

    h, w = (image['levels'][0]['height'], image['levels'][0]['width']) if image else (
        float(pos[1::2].max()) + 50, float(pos[0::2].max()) + 50)
    if image and a.min_level:
        h, w = h * 2 ** a.min_level, w * 2 ** a.min_level

    # post-Xenium H&E, registered in Xenium Explorer: an RGB channel under the morphology channels
    he = None if a.no_image else find_he(b, a)
    if he:
        import image_tiles
        tif, m = he
        levels_he = image_tiles.tiff_levels(tif)

        def cover(mat):  # fraction of the image's bounding box that lies on the slide
            lo, hi = image_tiles.Source(levels_he, mat[:2]).bounds()
            ix = max(0.0, min(hi[0], w) - max(lo[0], 0)) * max(0.0, min(hi[1], h) - max(lo[1], 0))
            return ix / max(1e-9, (hi[0] - lo[0]) * (hi[1] - lo[1]))
        if cover(m) < 0.2 and cover(np.linalg.inv(m)) > 2 * cover(m):
            print('H&E alignment: the matrix maps the other way; using its inverse', flush=True)
            m = np.linalg.inv(m)
        src = image_tiles.Source(levels_he, m[:2], mode='rgb')
        lo, hi = src.bounds()
        print(f'H&E {tif.name}: covers x {lo[0]:.0f}-{hi[0]:.0f}, y {lo[1]:.0f}-{hi[1]:.0f} of {w:.0f} x {h:.0f}', flush=True)
        c = len(image['channels']) if image else 0
        levels, size = image_tiles.write_channel([src], out, c, w, h, levels=image['levels'] if image else None,
                                                 min_level=None if image else a.min_level, quality=a.jpeg_quality)
        print(f'H&E: {size / 1e6:.0f} MB', flush=True)
        if image is None:
            image = dict(tileSize=TILE, format='mixed', levels=levels, channels=[])
        for ch in image['channels']:
            ch['visible'] = False
        image['channels'].append(dict(name='H&E', file=f'img_{c}', color='#ffffff', visible=True, window=[0, 255], mode='rgb'))

    cats = [s['key'] for s in cols.specs if s['type'] == 'category']
    if a.default:
        cats = [a.default] + [c for c in cats if c != a.default]
    rank = lambda k: next((r for r, pat in enumerate(['celltype', 'cell_type', 'predicted.id', 'annotation', 'seurat_clusters', 'cluster'])
                           if pat in k.lower()), 99)
    default = (a.default if a.default in cats else min(cats, key=rank)) if cats else None
    slide = dict(
        version=1, id=a.id, title=a.title or a.id, technology='xenium',
        panel=exp.get('panel_name'), preservation=exp.get('preservation_method'), run=exp.get('run_name'),
        nCells=n, pixelSize=px, width=float(w), height=float(h),
        genes=genes.tolist(), obs=cols.specs, defaultObs=default, radiusOffset=radius_off,
        embeddings=embeddings, regions=regions, boundaries=boundaries, image=image,
        normalization='log1p(counts / total_counts * 10000)',
    )
    (out / 'slide.json').write_text(json.dumps(slide, separators=(',', ':')))
    total_mb = sum(f.stat().st_size for f in out.iterdir()) / 1e6
    print(f'done: {out} ({total_mb:.0f} MB)')


if __name__ == '__main__':
    main()
