"""Recognize what a path on /nfs holds and how the hub can show it.

inspect(path) returns a dict with
  type     viewer-slide | viewer-project | crispr-screen | viewer-collection   (ready: load directly)
           h5ad | h5-10x | loom | rds | xenium | visium | table                         (convertible)
           unsupported
  ready    True when the files are already in the ATHENA viewer format
  kind     suggested viewer: spatial | singlecell | crispr (None when unknown)
  details  format-specific facts shown in the hub (cells, genes, annotations, ...)
  output   the folder conversion writes to (<source>.vizhub next to the source) and whether it
           already holds a finished conversion of this exact source (converted=True)
"""
import json
import os
import re
from pathlib import Path

OUTPUT_SUFFIX = '.vizhub'
TABLE_EXT = ('.tsv', '.tsv.gz', '.csv', '.csv.gz', '.txt', '.txt.gz', '.parquet')
EMBEDDING_HINTS = ('umap', 'tsne', 'fle', 'draw_graph', 'phate', 'fa2', 'force')
SPATIAL_KEYS = ('spatial', 'X_spatial', 'spatial_coords', 'X_centroids')

# column aliases for CRISPR result tables (SCEPTRE / IGVF / generic DE output)
CRISPR_COLUMNS = {
    'target': ['intended_target_name', 'grna_target', 'target', 'target_name', 'perturbation', 'guide_target',
               'element', 'element_id', 'grna_group'],
    'gene': ['gene_symbol', 'response_id', 'gene', 'gene_name', 'readout_gene', 'feature', 'response'],
    'log2fc': ['sceptre_log2_fc', 'log_2_fold_change', 'log2_fc', 'log2fc', 'log2FoldChange', 'avg_log2FC', 'logFC', 'lfc'],
    'p': ['sceptre_p_value', 'p_value', 'pvalue', 'p_val', 'P.Value', 'pval', 'p'],
    'padj': ['sceptre_adj_p_value', 'adj_p_value', 'padj', 'p_val_adj', 'adj.P.Val', 'fdr', 'FDR', 'qvalue', 'q_value'],
}


def output_dir(src: Path) -> Path:
    return src.parent / (src.name + OUTPUT_SUFFIX)


def source_stamp(src: Path):
    st = src.stat()
    return dict(source=str(src), sourceSize=st.st_size, sourceMtime=int(st.st_mtime))


def read_manifest(folder: Path):
    try:
        return json.loads((folder / 'vizhub.json').read_text())
    except Exception:
        return None


def find_column(columns, role):
    lower = {c.lower(): c for c in columns}
    for alias in CRISPR_COLUMNS[role]:
        if alias in columns:
            return alias
        if alias.lower() in lower:
            return lower[alias.lower()]
    return None


# ----------------------------------------------------------------- ready-made viewer folders

def _is_slide(d: Path):
    return os.path.isfile(d / 'slide.json') and os.path.isfile(d / 'cells.bin') and os.path.isfile(d / 'expr.idx')


def _slide_meta(d: Path):
    try:
        m = json.loads((d / 'slide.json').read_text())
        return dict(id=m.get('id'), title=m.get('title'), spatial=m.get('spatial', True) is not False,
                    nCells=m.get('nCells'), nGenes=len(m.get('genes') or []), technology=m.get('technology'),
                    species=m.get('species'))
    except Exception:
        return None


def _project(d: Path):
    """project.json + slide folders -> description, or None."""
    pj = d / 'project.json'
    meta = {}
    if os.path.isfile(pj):
        try:
            meta = json.loads(pj.read_text())
        except Exception:
            meta = {}
    if meta.get('kind') in ('enhancer', 'perturbseq'):
        ok = os.path.isfile(d / 'tests.json') if meta['kind'] == 'enhancer' else os.path.isfile(d / 'targets.json')
        return dict(type='crispr-screen', kind='crispr', title=meta.get('title') or d.name, screenKind=meta['kind'],
                    species=meta.get('species')) if ok else None
    listed = [s.get('id') for s in meta.get('slides') or [] if s.get('id')]
    try:
        subs = listed or sorted(c.name for c in d.iterdir() if os.path.isdir(c) and not c.name.startswith('.'))
    except OSError:
        return None
    slides = []
    for s in subs:
        if _is_slide(d / s):
            sm = _slide_meta(d / s) or {}
            title = next((x.get('title') for x in meta.get('slides') or [] if x.get('id') == s), None)
            slides.append(dict(id=s, title=title or sm.get('title') or s, spatial=sm.get('spatial', True),
                               nCells=sm.get('nCells')))
    if not slides:
        return None
    spatial = any(s['spatial'] for s in slides)
    return dict(type='viewer-project', kind='spatial' if spatial else 'singlecell', title=meta.get('title') or d.name,
                slides=slides, species=meta.get('species'), technology=meta.get('technology'))


SPACERANGER_POSITIONS = ('tissue_positions.parquet', 'tissue_positions.csv', 'tissue_positions_list.csv')


def spaceranger(d: Path):
    """A Space Ranger output folder (Visium, or one Visium HD bin size such as binned_outputs/square_008um):
    spatial/ with tissue positions, scale factors and the tissue images, and a feature-barcode matrix."""
    sp = d / 'spatial'
    pos = next((sp / n for n in SPACERANGER_POSITIONS if os.path.isfile(sp / n)), None)
    if pos is None:
        return None
    matrix = next((d / n for n in ('filtered_feature_bc_matrix.h5', 'raw_feature_bc_matrix.h5') if os.path.isfile(d / n)), None)
    if matrix is None:
        matrix = next((m for m in (d / 'filtered_feature_bc_matrix', d) if any(os.path.isfile(m / f) for f in ('matrix.mtx', 'matrix.mtx.gz'))), None)
    if matrix is None:
        return None
    images = [n for n in ('tissue_hires_image.png', 'tissue_lowres_image.png') if os.path.isfile(sp / n)]
    return dict(positions=str(pos), matrix=str(matrix), images=images, scalefactors=os.path.isfile(sp / 'scalefactors_json.json'))


def inspect_folder(d: Path):
    if _is_slide(d):
        sm = _slide_meta(d) or {}
        return dict(type='viewer-slide', ready=True, kind='spatial' if sm.get('spatial', True) else 'singlecell',
                    details=sm, title=sm.get('title') or d.name)
    if os.path.isfile(d / 'experiment.xenium'):
        details = {}
        try:
            exp = json.loads((d / 'experiment.xenium').read_text())
            details = dict(panel=exp.get('panel_name'), run=exp.get('run_name'), cells=exp.get('num_cells'),
                           pixelSize=exp.get('pixel_size'), preservation=exp.get('preservation_method'))
        except Exception:
            pass
        return dict(type='xenium', ready=False, kind='spatial', details=details, title=d.name)
    sr = spaceranger(d)
    if sr:
        return dict(type='visium', ready=False, kind='spatial', details=sr, title=d.parent.name if d.name == 'outs' else d.name)
    p = _project(d)
    if p:
        return dict(ready=True, details=p, **{k: p[k] for k in ('type', 'kind', 'title')})
    # a folder of projects / screens (e.g. ATHENA's data/view)
    items = []
    try:
        children = sorted(c for c in d.iterdir() if os.path.isdir(c) and not c.name.startswith('.'))
    except PermissionError:
        children = []
    for c in children[:500]:
        if _is_slide(c):
            sm = _slide_meta(c) or {}
            items.append(dict(path=str(c), type='viewer-slide', kind='spatial' if sm.get('spatial', True) else 'singlecell',
                              title=sm.get('title') or c.name))
            continue
        cp = _project(c)
        if cp:
            items.append(dict(path=str(c), type=cp['type'], kind=cp['kind'], title=cp['title']))
    if items:
        return dict(type='viewer-collection', ready=True, kind=None, title=d.name, details=dict(items=items))
    return dict(type='unsupported', ready=False, kind=None, title=d.name,
                details=dict(reason='This folder is not a Xenium output bundle or a viewer dataset. Open it to pick a file.'))


# ----------------------------------------------------------------- files

def _h5ad_details(path: Path):
    import h5py
    import numpy as np
    d = dict(obsColumns=[], categorical=[], numeric=[], obsm={}, layers=[], raw=False)
    with h5py.File(path, 'r') as f:
        def n_rows(group):
            g = f[group]
            if isinstance(g, h5py.Dataset):
                return g.shape[0]
            key = g.attrs.get('_index', '_index')
            if isinstance(key, bytes):
                key = key.decode()
            return g[key].shape[0] if key in g else None
        d['nCells'], d['nGenes'] = n_rows('obs'), n_rows('var')
        X = f.get('X')
        if isinstance(X, h5py.Group):
            enc = X.attrs.get('encoding-type', X.attrs.get('h5sparse_format', ''))
            d['X'] = enc.decode() if isinstance(enc, bytes) else str(enc)
            sample = X['data'][:200000] if 'data' in X else np.array([])
        elif isinstance(X, h5py.Dataset):
            d['X'] = 'dense'
            sample = X[:min(200, X.shape[0])] if X.ndim == 2 else np.array([])
        else:
            sample = np.array([])
        sample = np.asarray(sample, dtype=np.float64).ravel()
        d['xInteger'] = bool(sample.size == 0 or (np.all(sample >= 0) and np.all(np.mod(sample, 1) == 0)))
        d['raw'] = 'raw' in f
        if 'layers' in f:
            d['layers'] = list(f['layers'].keys())
        if 'obsm' in f:
            for k, v in f['obsm'].items():
                shape = v.shape if isinstance(v, h5py.Dataset) else None
                d['obsm'][k] = list(shape) if shape else None
        obs = f['obs']
        if isinstance(obs, h5py.Group):
            order = obs.attrs.get('column-order', [])
            order = [c.decode() if isinstance(c, bytes) else str(c) for c in order] or [k for k in obs.keys() if k not in ('_index', '__categories')]
            for c in order:
                if c not in obs:
                    continue
                v = obs[c]
                d['obsColumns'].append(c)
                if isinstance(v, h5py.Group) and 'categories' in v:
                    d['categorical'].append(dict(name=c, n=int(v['categories'].shape[0])))
                elif isinstance(v, h5py.Dataset) and 'categories' in v.attrs:  # anndata < 0.8
                    d['categorical'].append(dict(name=c, n=int(obs['__categories'][c].shape[0]) if '__categories' in obs and c in obs['__categories'] else 0))
                elif isinstance(v, h5py.Dataset) and v.dtype.kind in 'fiu':
                    d['numeric'].append(c)
                elif isinstance(v, h5py.Dataset) and v.dtype.kind in 'SOU':
                    d['categorical'].append(dict(name=c, n=None))
        if 'uns' in f and 'spatial' in f['uns']:
            d['visium'] = list(f['uns']['spatial'].keys())
    d['spatialKey'] = next((k for k in SPATIAL_KEYS if k in d['obsm']), None)
    d['embeddings'] = [k for k in d['obsm'] if any(h in k.lower() for h in EMBEDDING_HINTS)]
    d['countsSource'] = 'layers["counts"]' if 'counts' in d['layers'] else ('raw.X (checked when converting)' if d['raw'] else
                                                                           ('X' if d['xInteger'] else 'none (X is normalized; values shown as stored)'))
    return d


def _table_details(path: Path):
    import pandas as pd
    if path.name.endswith('.parquet'):
        import pyarrow.parquet as pq
        cols = pq.ParquetFile(path).schema.names
        head = pd.read_parquet(path).head(2000)
    else:
        sep = ',' if '.csv' in path.name else '\t'
        head = pd.read_csv(path, sep=sep, nrows=2000)
        if head.shape[1] == 1 and sep == '\t':
            head = pd.read_csv(path, sep=',', nrows=2000)
        cols = list(head.columns)
    found = {role: find_column(cols, role) for role in CRISPR_COLUMNS}
    crispr = bool(found['target'] and found['gene'] and (found['p'] or found['padj']))
    return dict(columns=cols[:60], nColumns=len(cols), crisprColumns=found, crispr=crispr,
                screenKind=guess_screen_kind(head, found) if crispr else None)


def guess_screen_kind(df, cols):
    """'enhancer' (element -> gene tests) or 'perturbseq' (gene TSS targets x transcriptome)."""
    if 'type' in df.columns and df['type'].astype(str).str.contains('element').any():
        return 'enhancer'
    targets = df[cols['target']].astype(str)
    if targets.str.match(r'^chr[\w]+[:_-]\d+[-_]\d+').mean() > 0.5:
        return 'enhancer'
    genes = set(df[cols['gene']].astype(str))
    t = set(targets)
    return 'perturbseq' if len(t & genes) >= max(1, 0.3 * len(t)) or len(t) > 50 else 'enhancer'


def inspect_file(path: Path):
    name = path.name.lower()
    size = path.stat().st_size
    base = dict(title=re.sub(r'\.(h5ad|h5mu|h5|loom|rds|tsv|csv|txt|parquet)(\.gz)?$', '', path.name, flags=re.I), size=size)
    if name.endswith('.h5ad'):
        try:
            det = _h5ad_details(path)
        except Exception as e:  # unreadable or exotic layout: let the converter try anndata
            det = dict(error=f'could not inspect: {e}')
        kind = 'spatial' if det.get('spatialKey') else 'singlecell'
        return dict(type='h5ad', ready=False, kind=kind, details=det, **base)
    if name.endswith('.h5mu'):
        det = {}
        try:
            import h5py
            with h5py.File(path, 'r') as f:
                mods = list(f['mod'].keys()) if 'mod' in f else []
                det['modalities'] = mods
                det['note'] = f'MuData with modalities {", ".join(mods)}: each becomes an assay of the single-cell viewer'
        except Exception as e:
            det['error'] = f'could not inspect: {e}'
        return dict(type='h5mu', ready=False, kind='singlecell', details=det, **base)
    if name.endswith('.h5'):
        try:
            import h5py
            with h5py.File(path, 'r') as f:
                tenx = 'matrix' in f and 'features' in f['matrix']
                shape = list(f['matrix']['shape'][:]) if tenx else None
        except Exception:
            tenx, shape = False, None
        if tenx and spaceranger(path.parent):  # filtered_feature_bc_matrix.h5 of a Space Ranger run: convert the run
            sr = spaceranger(path.parent)
            if os.path.samefile(sr['matrix'], path):
                return dict(type='visium', ready=False, kind='spatial', details=dict(sr, shape=shape), **base)
        if tenx:
            return dict(type='h5-10x', ready=False, kind='singlecell', details=dict(shape=shape, note='10x feature-barcode matrix; UMAP and clusters are computed'), **base)
        return dict(type='unsupported', ready=False, kind=None, details=dict(reason='HDF5 file that is not a 10x matrix (use .h5ad)'), **base)
    if name.endswith('.loom'):
        return dict(type='loom', ready=False, kind='singlecell', details={}, **base)
    if name.endswith('.rds'):
        lname = name
        kind = 'crispr' if any(k in lname for k in ('sceptre', 'discovery', 'crispr', 'perturb_result')) else None
        return dict(type='rds', ready=False, kind=kind, details=dict(
            note='R object: Seurat (v3–v5) or SingleCellExperiment → spatial/single-cell viewer; '
                 'sceptre_object or data.frame of results → CRISPR explorer. The type is read when converting.'), **base)
    if name.endswith(TABLE_EXT):
        try:
            det = _table_details(path)
        except Exception as e:
            det = dict(error=str(e), crispr=False)
        if det.get('crispr'):
            return dict(type='table', ready=False, kind='crispr', details=det, **base)
        return dict(type='unsupported', ready=False, kind=None, details=dict(
            reason='Table without CRISPR result columns (target, gene, log2FC, p value)', **det), **base)
    return dict(type='unsupported', ready=False, kind=None, details=dict(reason='Unsupported file type'), **base)


def inspect(path):
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(path)
    info = inspect_folder(p) if os.path.isdir(p) else inspect_file(p)
    info['path'] = str(p)
    if not info.get('ready') and info['type'] != 'unsupported':
        out = output_dir(p)
        m = read_manifest(out)
        st = source_stamp(p)
        converted = bool(m and m.get('status') == 'done' and m.get('sourceSize') == st['sourceSize']
                         and m.get('sourceMtime') == st['sourceMtime'])
        info['output'] = dict(path=str(out), exists=out.exists(), converted=converted, manifest=m,
                              writable=os.access(p.parent, os.W_OK))
    return info
