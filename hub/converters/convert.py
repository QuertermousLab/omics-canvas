#!/usr/bin/env python3
"""Convert a dataset on /nfs into the ATHENA viewer format (https://github.com/QuertermousLab/athena).

  convert.py SOURCE OUT --id ID [--kind auto|spatial|singlecell|crispr] [options]

SOURCE may be
  *.h5ad / *.loom / 10x *.h5       AnnData-like single-cell or spatial data
  *.rds                            Seurat (v3-v5), SingleCellExperiment/SpatialExperiment (exported by
                                   rds_export.R), or a sceptre_object / data.frame of CRISPR results
  a Xenium output folder           (experiment.xenium; ATHENA's xenium_to_viewer.py)
  *.tsv / *.csv / *.parquet        CRISPR screen results (crispr_to_viewer.py)

Spatial and single-cell data become one slide folder (slide.json, cells.bin, cell_ids.txt, expr.bin,
expr.idx[, boundaries.bin, img_*]); CRISPR results one screen folder (project.json + tests.json or
targets.json/de.*/bygene.*). OUT/vizhub.json records the source and the outcome for the hub.

Compared with ATHENA's h5ad_to_viewer.py this also accepts normalized-only matrices (stored as float32,
"exprDtype": "float32"), counts in layers["counts"], Visium(-HD)/Slide-seq/Xenium coordinates from
Seurat objects, and computes PCA/UMAP (+ Leiden clusters) when a dataset has no embedding.
Progress is reported on stdout as "@@progress <percent> <message>".
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import scipy.sparse as sp

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from detect import EMBEDDING_HINTS, SPATIAL_KEYS, source_stamp  # noqa: E402
from xenium_to_viewer import Columns, default_label  # noqa: E402  (ATHENA's annotation encoding)

CONVERTER_VERSION = 1


def progress(pct, msg):
    print(f'@@progress {int(pct)} {msg}', flush=True)


def log(msg):
    print(msg, flush=True)


def notice(msg):
    """A message the hub shows above the progress bar (long steps, resuming)."""
    print(f'@@notice {msg}', flush=True)


# ----------------------------------------------------------------- labels (from ATHENA h5ad_to_viewer.py)

EMBEDDING_LABELS = {'X_umap': 'UMAP', 'X_tsne': 't-SNE', 'X_fle': 'Force-directed layout', 'X_draw_graph_fa': 'Force-directed layout',
                    'umap': 'UMAP', 'tsne': 't-SNE', 'wnn.umap': 'WNN UMAP', 'ref.umap': 'Reference UMAP'}
CXG_LABELS = {
    'cell_type': 'Cell type (Cell Ontology)', 'tissue': 'Tissue', 'disease': 'Disease', 'sex': 'Sex',
    'development_stage': 'Age', 'donor_id': 'Donor', 'assay': 'Assay', 'self_reported_ethnicity': 'Ethnicity',
}
AUTHOR_LABELS = {
    'author_cell_type': 'Cell type (authors)', 'Cluster_type': 'Cell type (authors)',
    'Cluster_type_full': 'Cell type (authors)', 'predicted.id': 'Cell type (label transfer)',
    'nCount_RNA': 'UMI counts', 'nFeature_RNA': 'Genes detected', 'percent.mt': '% mitochondrial reads',
    'percent.rp': '% ribosomal reads', 'spot_class': 'RCTD spot class', 'second_type': 'RCTD second cell type',
    'nCount_peaks': 'Fragments in peaks', 'nFeature_peaks': 'Peaks detected', 'TSS.enrichment': 'TSS enrichment',
    'nucleosome_signal': 'Nucleosome signal', 'pct_reads_in_peaks': '% reads in peaks',
    'nCount_SCT': 'UMI counts (SCT)', 'nFeature_SCT': 'Genes detected (SCT)', 'leiden': 'Leiden clusters (computed)',
    'n_genes_by_counts': 'Genes detected', 'total_counts': 'Total counts', 'pct_counts_mt': '% mitochondrial counts',
    'nCount_Spatial': 'UMI counts (Spatial)', 'nFeature_Spatial': 'Genes detected (Spatial)',
}
SKIP = ('_ontology_term_id', 'uuid', 'observation_joinid', 'is_primary_data', 'donor_living_at_sample_collection',
        'mapped_reference_annotation', 'suspension_', 'sample_derivation', 'sample_preservation', 'tissue_type',
        'sequencing_platform', 'log10_', 'TSS.percentile', 'nCount_ACTIVITY', 'nFeature_ACTIVITY')
DEFAULT_PATTERNS = ['author_cell_type', 'cell_type', 'celltype', 'cell.type', 'predicted.id', 'annotation', 'cluster_type',
                    'seurat_clusters', 'leiden', 'louvain', 'cluster']
LATENT = ('pca', 'harmony', 'scvi', 'lsi', 'ica', 'nmf', 'mnn', 'cca', 'X_pca')


def gene_symbols(var):
    for col in ('feature_name', 'gene_symbols', 'gene_name', 'gene_ids_symbol', 'symbol'):
        if col in var:
            names = var[col].astype(str).to_numpy()
            break
    else:
        names = var.index.astype(str).to_numpy()
    seen, out = set(), []
    for name, gid in zip(names, var.index.astype(str)):
        out.append(name if name not in seen else f'{name} ({gid})')
        seen.add(name)
    return np.array(out)


def is_counts(m):
    if m is None:
        return False
    if sp.issparse(m):
        sample = m.data[:1_000_000]
    else:
        arr = np.asarray(m[:min(500, m.shape[0])])
        sample = arr.ravel()
    sample = np.asarray(sample, dtype=np.float64)
    return bool(sample.size == 0 or (np.all(sample >= 0) and np.all(np.mod(sample, 1) == 0)))


def pick_matrix(adata, prefer=None):
    """(name, cells x genes matrix, var, integer counts?) - raw integer counts when the data has them."""
    cands = []
    if prefer:
        if prefer == 'X':
            cands.append(('X', adata.X, adata.var))
        elif prefer == 'raw' and adata.raw is not None:
            cands.append(('raw.X', adata.raw.X, adata.raw.var))
        elif prefer in adata.layers:
            cands.append((f'layers["{prefer}"]', adata.layers[prefer], adata.var))
    for key in ('counts', 'raw_counts', 'count', 'umi', 'UMIs'):
        if key in adata.layers:
            cands.append((f'layers["{key}"]', adata.layers[key], adata.var))
    if adata.raw is not None:
        var = adata.raw.var
        if 'feature_name' not in var and 'feature_name' in adata.var:
            var = var.join(adata.var[['feature_name']], how='left')
        cands.append(('raw.X', adata.raw.X, var))
    cands.append(('X', adata.X, adata.var))
    for name, m, var in cands:
        if m is not None and is_counts(m):
            return name, m, var, True
    name, m, var = cands[0] if prefer else ('X', adata.X, adata.var)
    return name, m, var, False


def slugify_key(k):
    return re.sub(r'[^A-Za-z0-9_.-]+', '_', str(k))


def guess_species(genes):
    sample = [g for g in genes[:5000] if re.match(r'^[A-Za-z][A-Za-z0-9-]+$', g)]
    if not sample:
        return None
    upper = sum(g.isupper() for g in sample) / len(sample)
    title = sum(g[0].isupper() and g[1:].islower() for g in sample if len(g) > 1) / len(sample)
    return 'Human' if upper > 0.6 else 'Mouse' if title > 0.5 else None


def nn_spacing(xy):
    """Median nearest-neighbour distance of (a sample of) the positions."""
    from scipy.spatial import cKDTree
    pts = xy[np.all(np.isfinite(xy), axis=1)]
    if len(pts) < 3:
        return 1.0
    sample = pts[np.random.default_rng(0).choice(len(pts), min(20000, len(pts)), replace=False)]
    d, _ = cKDTree(pts).query(sample, k=2)
    v = np.median(d[:, 1])
    return float(v) if v > 0 else 1.0


# ----------------------------------------------------------------- AnnData -> viewer slide

def compute_umap(counts_csr, integer, n_cells, want_clusters):
    import anndata as ad
    import scanpy as sc
    progress(22, f'No embedding in the data: computing PCA + UMAP for {n_cells:,} cells')
    tmp = ad.AnnData(X=counts_csr.astype(np.float32))
    if integer:
        sc.pp.normalize_total(tmp, target_sum=1e4)
        sc.pp.log1p(tmp)
    try:
        sc.pp.highly_variable_genes(tmp, n_top_genes=min(2000, tmp.n_vars))
        tmp = tmp[:, tmp.var['highly_variable'].to_numpy()].copy()
    except Exception as e:
        log(f'  highly variable genes failed ({e}); using all genes')
    sc.tl.pca(tmp, n_comps=min(50, tmp.n_vars - 1, tmp.n_obs - 1))
    progress(25, 'Computing neighbours and UMAP')
    sc.pp.neighbors(tmp, n_neighbors=15)
    sc.tl.umap(tmp)
    clusters = None
    if want_clusters:
        progress(28, 'Computing Leiden clusters')
        try:
            sc.tl.leiden(tmp, resolution=0.8, flavor='igraph', n_iterations=2, directed=False)
            clusters = tmp.obs['leiden'].to_numpy()
        except Exception as e:
            log(f'  Leiden clustering failed: {e}')
    return np.asarray(tmp.obsm['X_umap'], dtype=np.float32), clusters


def visium_scale(adata):
    """(µm per coordinate unit, spot diameter in coordinate units) from uns["spatial"], or (None, None)."""
    try:
        lib = next(iter(adata.uns['spatial'].values()))
        sf = lib['scalefactors']
    except Exception:
        return None, None
    spot = sf.get('spot_diameter_fullres')
    mpp = sf.get('microns_per_pixel')
    if mpp:
        return float(mpp), float(spot) if spot else None
    if spot:
        return 55.0 / float(spot), float(spot)  # standard Visium spots are 55 µm
    return None, None


ASSAY_FEATURE = {'rna': 'Gene expression', 'peaks': 'Peak accessibility', 'activity': 'Gene activity',
                 'chromvar': 'chromVAR motif deviation', 'protein': 'Protein (ADT)'}
INLINE_FEATURES = 60000  # more features (e.g. peaks) go to a separate features.json


def write_expression(counts, names_all, dest: Path, integer: bool, what: str, lo: float, hi: float):
    """expr.bin/expr.idx for a cells x features CSR matrix (ATHENA's layout; float32 values when not counts).
    Returns the names of the features kept (those with any non-zero value)."""
    by_gene = counts.T.tocsr()
    by_gene.sort_indices()
    keep = np.flatnonzero(np.diff(by_gene.indptr) > 0)
    names = np.asarray(names_all)[keep]
    idx = np.zeros((len(keep), 2), np.float64)
    dest.mkdir(parents=True, exist_ok=True)
    vdtype = np.uint16 if integer else np.float32
    step = max(1, len(keep) // 40)
    with open(dest / 'expr.bin', 'wb') as f:
        off = 0
        for k, g in enumerate(keep):
            s, e = by_gene.indptr[g], by_gene.indptr[g + 1]
            block = by_gene.indices[s:e].astype(np.uint32).tobytes() + by_gene.data[s:e].astype(vdtype).tobytes()
            block += b'\0' * ((-len(block)) % 4)
            f.write(block)
            idx[k] = (off, e - s)
            off += len(block)
            if k % step == 0:
                progress(lo + (hi - lo) * k / max(1, len(keep)), f'Writing {what}: {k:,} of {len(keep):,} features')
    idx.tofile(dest / 'expr.idx')
    log(f'{what}: {len(keep)} features, {by_gene.nnz} non-zero, {off / 1e6:.0f} MB')
    return names


def prepare_counts(m, what):
    """CSR matrix, integer?, per-cell totals; counts capped to uint16."""
    m = sp.csr_matrix(m)
    integer = is_counts(m)
    total = np.asarray(m.sum(axis=1)).ravel().astype(np.float32)
    if integer and m.nnz and m.data.max() > 65535:
        log(f'  {what}: capping {int((m.data > 65535).sum())} counts above 65535')
        m.data = np.minimum(m.data, 65535)
    return m, integer, total


def motif_table(motifs):
    """{id: (name, 4 x L counts or probabilities)} -> {id: {name, pwm: [[A, C, G, T] per position] as probabilities}}."""
    out = {}
    for mid, (name, mat) in motifs.items():
        m = np.asarray(mat, dtype=np.float64)
        m = m / np.maximum(m.sum(axis=0, keepdims=True), 1e-9)
        out[mid] = dict(name=name, pwm=np.round(m.T, 4).tolist())
    return out


def write_slide(adata, out: Path, a, *, spatial_xy=None, groups=None, technology=None, title=None,
                primary=None, extras=None, motifs=None):
    """The core of ATHENA's h5ad_to_viewer.py, generalized. Returns the slide.json dict.

    primary: {key, label, type} of adata's matrix; extras: further assays of the same cells (multiome:
    peaks, gene activity, chromVAR, ...) as [{key, label, type, X (cells x features), names}];
    motifs: {id: (name, PWM)} for chromVAR assays (motif logos)."""
    n = adata.n_obs
    ids = adata.obs_names.astype(str).to_numpy()
    progress(20, f'Preparing expression matrix ({n:,} cells)')
    mname, counts, var, integer = pick_matrix(adata, a.matrix)
    log(f'  expression from {mname} ({"integer counts" if integer else "non-integer values, stored as float32"})')
    counts = sp.csr_matrix(counts)
    if not integer and counts.nnz and np.any(counts.data < 0):
        log('  matrix has negative values (scaled data?); they are kept as they are')
    total = np.asarray(counts.sum(axis=1)).ravel().astype(np.float32)
    if integer:
        over = int((counts.data > 65535).sum())
        if over:
            log(f'  capping {over} counts above 65535 (max {counts.data.max():.0f})')
            counts.data = np.minimum(counts.data, 65535)
    names_all = gene_symbols(var)

    # embeddings need the matrix before it is transposed (computed UMAP)
    embeddings_wanted = {}
    if a.embedding:
        embeddings_wanted = dict(spec.split('=', 1) for spec in a.embedding)
    else:
        for k in adata.obsm.keys():
            low = k.lower()
            if k in SPATIAL_KEYS or any(t in low for t in LATENT):
                continue
            if k in EMBEDDING_LABELS or any(h in low for h in EMBEDDING_HINTS):
                embeddings_wanted[k] = EMBEDDING_LABELS.get(k) or re.sub(r'^X_', '', k).replace('_', ' ').upper()
    computed_umap = None
    has_category = any((isinstance(adata.obs[c].dtype, pd.CategoricalDtype) or adata.obs[c].dtype == object)
                       and 2 <= adata.obs[c].nunique(dropna=True) <= 200 for c in adata.obs.columns)
    if not embeddings_wanted and (spatial_xy is None or a.compute_umap) and not a.no_umap:
        computed_umap, clusters = compute_umap(counts, integer, n, want_clusters=not has_category or a.compute_umap)
        if clusters is not None:
            adata.obs['leiden'] = pd.Categorical(clusters, categories=sorted(set(clusters), key=lambda x: int(x)))

    primary = primary or dict(key='main', label=a.feature, type='rna')
    extras = extras or []
    progress(30, f'Writing {primary["label"]}')
    names = write_expression(counts, names_all, out, integer, primary['label'], 30, 75 if not extras else 55)
    del counts
    assays = [dict(key=primary['key'], label=primary['label'], type=primary['type'], dir='',
                   exprDtype='uint16' if integer else 'float32', total='total_counts', nFeatures=len(names),
                   normalization='log1p(counts / total_counts * 10000)' if integer else 'values as stored')]
    extra_totals = []
    for k, ex in enumerate(extras):
        lo = 55 + 20 * k / len(extras)
        X, ex_int, ex_total = prepare_counts(ex['X'], ex['label'])
        if ex['type'] == 'chromvar':
            ex_int = False  # deviations: never log-normalized
        d = f'assays/{slugify_key(ex["key"])}'
        ex_names = write_expression(X, ex['names'], out / d, ex_int, ex['label'], lo, lo + 20 / len(extras))
        feats = dict(names=list(map(str, ex_names)))
        if ex['type'] == 'chromvar' and motifs:
            feats['labels'] = [f'{motifs[m][0]} ({m})' if m in motifs else str(m) for m in ex_names]
        (out / d / 'features.json').write_text(json.dumps(feats, separators=(',', ':')))
        tkey = f'total_counts__{slugify_key(ex["key"])}'
        extra_totals.append((tkey, ex_total))
        assays.append(dict(key=ex['key'], label=ex['label'], type=ex['type'], dir=d, features=f'{d}/features.json',
                           exprDtype='uint16' if ex_int else 'float32', total=tkey, nFeatures=len(ex_names),
                           normalization=f'log1p(counts / {tkey} * 10000)' if ex_int else
                           ('chromVAR deviation z-score' if ex['type'] == 'chromvar' else 'values as stored')))
    if motifs and any(x['type'] == 'chromvar' for x in assays):
        (out / 'motifs.json').write_text(json.dumps(motif_table(motifs), separators=(',', ':')))
        for x in assays:
            if x['type'] == 'chromvar':
                x['motifs'] = 'motifs.json'
    if len(names) > INLINE_FEATURES:  # e.g. a peaks-only object: keep slide.json small
        (out / 'features.json').write_text(json.dumps(dict(names=list(map(str, names))), separators=(',', ':')))
        assays[0]['features'] = 'features.json'

    progress(80, 'Writing positions and annotations')
    if spatial_xy is not None:
        xy = np.asarray(spatial_xy, dtype=np.float64)[:, :2]
        mpp, spot_units = visium_scale(adata)
        pixel_size = a.pixel_size or mpp or 1.0
        scale_unknown = not (a.pixel_size or mpp)
        if a.spot_diameter:
            spot_units = a.spot_diameter / pixel_size
        elif not spot_units:
            spot_units = 0.9 * nn_spacing(xy)
        log(f'  spatial: {pixel_size:.4g} µm per unit, spots drawn {spot_units:.3g} units wide')
        margin = 2 * spot_units
        xy = xy - np.nanmin(xy, axis=0) + margin
        width, height = (np.nanmax(xy, axis=0) + margin).tolist()
        radius = np.full(n, spot_units / 2, np.float32)
    else:
        pixel_size, scale_unknown = 1.0, False
        first = next(iter(embeddings_wanted), None)
        if first:
            xy = np.asarray(adata.obsm[first], dtype=np.float64)[:, :2]
        elif computed_umap is not None:
            xy = computed_umap.astype(np.float64)
        else:
            raise SystemExit('no embedding to show (the data has none and --no-umap was given)')
        width, height = float(np.nanmax(xy[:, 0])), float(np.nanmax(xy[:, 1]))
        radius = np.ones(n, np.float32)
    pos = np.empty(n * 2, np.float32)
    pos[0::2], pos[1::2] = xy[:, 0], xy[:, 1]

    labels = {**AUTHOR_LABELS, **dict(spec.split('=', 1) for spec in a.label)}
    cols = Columns(n)
    cols.raw(pos)
    cols.add('total_counts', total, 'Total counts', hidden=True)
    for tkey, t in extra_totals:
        cols.add(tkey, t, f'Total counts ({tkey.split("__", 1)[1]})', hidden=True)
    obs = adata.obs
    order = [c for c in CXG_LABELS if c in obs] + [c for c in obs.columns if c not in CXG_LABELS]
    groups = groups or {}
    for col in order:
        if any(s in col for s in SKIP) or obs[col].isna().all():
            continue
        if col == 'total_counts':
            continue
        group = groups.get(col) or ('CELLxGENE' if col in CXG_LABELS else 'Annotations')
        before = len(cols.specs)
        try:
            cols.add(col, obs[col], CXG_LABELS.get(col) or labels.get(col) or default_label(col), group=group)
        except Exception as e:
            log(f'  skip {col}: {e}')
            continue
        palette = adata.uns.get(f'{col}_colors')
        spec = cols.specs[-1] if len(cols.specs) > before else None
        if spec and spec['type'] == 'category' and palette is not None and hasattr(obs[col], 'cat') \
                and len(palette) >= len(obs[col].cat.categories):
            by_cat = dict(zip(map(str, obs[col].cat.categories), map(str, palette)))
            spec['colors'] = [by_cat.get(c, '#999999') for c in spec['categories']]
    radius_off = cols.raw(radius)

    embeddings = []
    for key, label in embeddings_wanted.items():
        e = np.asarray(adata.obsm[key], dtype=np.float32)[:, :2]
        arr = np.empty(n * 2, np.float32)
        arr[0::2], arr[1::2] = e[:, 0], e[:, 1]
        embeddings.append(dict(key=key, label=label, offset=cols.raw(arr)))
    if computed_umap is not None:
        arr = np.empty(n * 2, np.float32)
        arr[0::2], arr[1::2] = computed_umap[:, 0], computed_umap[:, 1]
        embeddings.append(dict(key='X_umap_vizhub', label='UMAP (computed)', offset=cols.raw(arr)))

    (out / 'cells.bin').write_bytes(cols.bytes())
    (out / 'cell_ids.txt').write_text('\n'.join(ids) + '\n')

    cats = [s['key'] for s in cols.specs if s['type'] == 'category']
    rank = lambda k: next((r for r, pat in enumerate(DEFAULT_PATTERNS) if pat in k.lower()), 99)
    default = a.default if a.default in cats else (min(cats, key=rank) if cats else None)
    uns = adata.uns
    organism = a.species or uns.get('organism') or (obs['organism'].iloc[0] if 'organism' in obs else None) or guess_species(list(names))
    slide = dict(
        version=1, id=a.id, title=title or a.title or a.id, technology=technology or a.technology,
        species=str(organism) if organism is not None else None, spatial=spatial_xy is not None,
        feature=primary['label'] if len(assays) > 1 else a.feature,
        nCells=n, pixelSize=float(pixel_size), width=float(width), height=float(height),
        genes=names.tolist() if len(names) <= INLINE_FEATURES else [], obs=cols.specs, assays=assays, defaultObs=default, radiusOffset=radius_off,
        embeddings=embeddings, regions=[], boundaries=None, image=None,
        normalization='log1p(counts / total_counts * 10000)' if integer else f'values as stored ({mname})',
        exprDtype='uint16' if integer else 'float32',
        scaleUnknown=bool(spatial_xy is not None and scale_unknown),
    )
    (out / 'slide.json').write_text(json.dumps(slide, separators=(',', ':')))
    return slide


# ----------------------------------------------------------------- readers

def read_rds_export(d: Path, a):
    """The files written by rds_export.R -> (AnnData, spatial xy or None, technology, annotation groups)."""
    import anndata as ad
    info = json.loads((d / 'export.json').read_text())
    m = info['matrix']
    progress(42, f'Reading exported matrix ({m["nCells"]:,} cells x {m["nGenes"]:,} genes)')
    x = np.fromfile(d / 'x.bin', dtype=np.float64)
    i = np.fromfile(d / 'i.bin', dtype=np.int32)
    p = np.fromfile(d / 'p.bin', dtype=np.int32)
    # R's dgCMatrix is genes x cells, column-compressed == cells x genes, row-compressed
    X = sp.csr_matrix((x.astype(np.float32 if not m['integer'] else np.float64), i, p), shape=(m['nCells'], m['nGenes']))
    genes = (d / 'genes.txt').read_text().splitlines()
    cells = (d / 'cells.txt').read_text().splitlines()
    meta = pd.read_csv(d / 'meta.csv', dtype={'cell': str}, keep_default_na=True, low_memory=False).set_index('cell')
    for col, t in (info.get('meta') or {}).items():
        if col not in meta:
            continue
        if t.get('type') == 'factor':
            levels = [str(v) for v in t.get('levels') or []]
            meta[col] = pd.Categorical(meta[col].astype('string').astype(object).where(meta[col].notna(), None), categories=levels)
        elif t.get('type') in ('logical', 'character'):
            meta[col] = meta[col].astype(object).where(meta[col].notna(), None)
    meta = meta.reindex(cells)
    adata = ad.AnnData(X=X, obs=meta, var=pd.DataFrame(index=pd.Index(genes)))
    adata.obs_names = cells
    adata.var_names_make_unique()
    for e in info.get('embeddings') or []:
        df = pd.read_csv(d / e['file'], dtype={'cell': str}).set_index('cell').reindex(cells)
        key = e['name']
        if any(t in key.lower() for t in LATENT) and not any(h in key.lower() for h in EMBEDDING_HINTS):
            continue
        adata.obsm[f'X_{key}' if not key.startswith('X_') else key] = df[['x', 'y']].to_numpy(dtype=np.float32)
        EMBEDDING_LABELS.setdefault(f'X_{key}', key.replace('.', ' ').replace('_', ' ').upper() if key.lower() in ('umap', 'tsne') else key)

    groups = {c: 'Seurat' if info['type'] == 'seurat' else 'colData' for c in meta.columns}
    images = info.get('images') or []
    xy, technology = None, None
    if images and a.kind != 'singlecell':
        parts, offset = [], 0.0
        for img in images:
            df = pd.read_csv(d / img['file'], dtype={'cell': str}).drop_duplicates('cell').set_index('cell')
            pts = df[['x', 'y']].to_numpy(dtype=np.float64)
            if len(images) > 1:  # several sections in one object: lay them out side by side
                pts[:, 0] += offset - np.nanmin(pts[:, 0])
                pts[:, 1] -= np.nanmin(pts[:, 1])
                offset = np.nanmax(pts[:, 0]) + 0.1 * (np.nanmax(pts[:, 0]) - np.nanmin(pts[:, 0]) + 1)
            parts.append(pd.DataFrame(pts, index=df.index, columns=['x', 'y']).assign(section=img['name']))
        allxy = pd.concat(parts)
        allxy = allxy[~allxy.index.duplicated()]
        keep = adata.obs_names.intersection(allxy.index, sort=False)
        if len(keep) < adata.n_obs:
            log(f'  {adata.n_obs - len(keep):,} cells have no tissue position and are left out')
        adata = adata[keep].copy()
        xy = allxy.loc[keep, ['x', 'y']].to_numpy()
        if len(images) > 1:
            adata.obs['section'] = pd.Categorical(allxy.loc[keep, 'section'].to_numpy())
            groups['section'] = 'Spatial'
        img0 = images[0]
        cls = img0.get('class', '')
        spot = img0.get('spot')
        assay_name = img0.get('assay') or info.get('assay') or ''
        bin_um = re.search(r'(\d+)um', assay_name)
        if not a.pixel_size and spot and spot == spot:  # not NaN
            if bin_um:  # Visium HD bins, e.g. Spatial.008um
                a.pixel_size = int(bin_um.group(1)) / float(spot)
                technology = f'Visium HD ({int(bin_um.group(1))} µm bins)'
            elif cls.startswith('Visium'):
                # standard Visium: 55 µm spots 100 µm apart (pitch / spot ~1.8). Other assays stored the Visium way
                # (spatial ATAC, DBiT, ...) have other spot sizes: leave their scale unknown unless --pixel-size.
                ratio = nn_spacing(xy) / float(spot)
                if 1.6 <= ratio <= 2.05:
                    a.pixel_size = 55.0 / float(spot)
                    technology = 'Visium'
                else:
                    log(f'  spots are {ratio:.2f} spot widths apart (Visium: 1.8): not a standard Visium layout, scale unknown '
                        '(give µm per coordinate unit to show a scale bar)')
                    technology = 'Visium-style spots'
            if not a.spot_diameter and a.pixel_size:
                a.spot_diameter = float(spot) * a.pixel_size
        technology = technology or {'FOV': 'Imaging-based (FOV)', 'SlideSeq': 'Slide-seq', 'STARmap': 'STARmap'}.get(cls, cls or None)
    return adata, xy, technology, groups, info


def assay_label(name, kind):
    if kind == 'chromvar':
        return 'chromVAR'
    if kind == 'peaks':
        return f'{name} · peaks' if name.lower() not in ('peaks', 'atac') else ('ATAC · peaks' if name.lower() == 'atac' else 'Peaks')
    if kind == 'activity':
        return 'Gene activity' if 'activity' in name.lower() or name.upper() == 'RNA' else f'{name} · gene activity'
    return name


def mark_gene_activity(adata, primary, extras, mode='auto'):
    """scATAC objects often keep Signac gene activity in an assay called "RNA". It is computed from the same
    fragments, so its per-cell totals follow the peak totals (log correlation ~0.98; real multiome RNA ~0.2).
    Such assays become type "activity" (the viewer shows the accessibility tracks with them)."""
    if mode == 'no':
        return
    assays = ([dict(primary, X=adata.X)] if primary else []) + extras
    peaks = next((x for x in assays if x['type'] == 'peaks'), None)
    if not peaks:
        return
    has_gex = any(str(c).startswith('gex_') for c in adata.obs.columns)  # cellranger-arc multiome QC
    t_peaks = np.log1p(np.asarray(sp.csr_matrix(peaks['X']).sum(axis=1)).ravel())
    for x in assays:
        if x['type'] != 'rna':
            continue
        if mode == 'auto':
            if has_gex:
                continue
            t = np.log1p(np.asarray(sp.csr_matrix(x['X']).sum(axis=1)).ravel())
            r = float(np.corrcoef(t, t_peaks)[0, 1]) if t.std() and t_peaks.std() else 0.0
            log(f'  {x["key"]}: per-cell totals vs {peaks["key"]} peaks, log correlation {r:.2f}')
            if r < 0.8:
                continue
        target = primary if primary and x['key'] == primary['key'] else next(e for e in extras if e['key'] == x['key'])
        target['type'] = 'activity'
        target['label'] = assay_label(x['key'], 'activity')
        log(f'  {x["key"]} is gene activity (computed from the fragments): shown with the accessibility tracks')


def read_dgc(d: Path, integer: bool):
    """One matrix written by rds_export.R -> (cells x features CSR, feature names, cell names)."""
    names = (d / 'genes.txt').read_text().splitlines()
    cells = (d / 'cells.txt').read_text().splitlines()
    x = np.fromfile(d / 'x.bin', dtype=np.float64)
    X = sp.csr_matrix((x if integer else x.astype(np.float32), np.fromfile(d / 'i.bin', dtype=np.int32),
                       np.fromfile(d / 'p.bin', dtype=np.int32)), shape=(len(cells), len(names)))
    return X, names, cells


def align_rows(X, cells, order):
    """Rows of X (named by cells) in `order`; cells missing from X get empty rows."""
    pos = pd.Index(cells).get_indexer(order)
    Y = X[np.maximum(pos, 0)].tocsr()
    if (pos < 0).any():
        Y = sp.diags((pos >= 0).astype(Y.dtype)) @ Y
    return sp.csr_matrix(Y)


def rds_multiome(d: Path, info, order, matrices=True):
    """The other assays of an exported Seurat object, its motifs and accessibility inputs, for the cells in `order`.
    matrices=False (resuming after the viewer files were written) skips reading the assay matrices."""
    assays = info.get('assays') or []
    primary, extras = None, []
    if len(assays) > 1 and matrices:
        a0 = assays[0]
        primary = dict(key=a0['name'], label=assay_label(a0['name'], a0['type']), type=a0['type'])
        for a in assays[1:]:
            progress(44, f'Reading assay {a["name"]} ({a["matrix"]["nGenes"]:,} features)')
            X, names, cells = read_dgc(d / a['dir'], a['matrix']['integer'])
            extras.append(dict(key=a['name'], label=assay_label(a['name'], a['type']), type=a['type'],
                               X=align_rows(X, cells, order), names=names))
    motifs = {}
    if (d / 'motifs.tsv').exists():
        for line in (d / 'motifs.tsv').read_text().splitlines():
            f = line.split('\t')
            if len(f) >= 6:
                motifs[f[0]] = (f[1], [[float(v) for v in row.split(',')] for row in f[2:6]])
    chrom = info.get('chromatin') or {}
    fragments = []
    if (d / 'fragments.tsv').exists():
        fr = pd.read_csv(d / 'fragments.tsv', sep='\t', dtype=str)
        index = pd.Index(order)
        for path, g in fr.groupby('path', sort=False):
            pos = index.get_indexer(g['cell'])
            ok = pos >= 0
            fragments.append((path, dict(zip(pos[ok].tolist(), g['barcode'].to_numpy()[ok]))))
    annotation = pd.read_csv(d / 'annotation.tsv', sep='\t', dtype={'seqnames': str}) if (d / 'annotation.tsv').exists() else None
    return primary, extras, motifs, fragments, annotation, chrom.get('genome')


def read_h5mu(src: Path, a):
    """MuData: the RNA modality (or the first) as AnnData + the other modalities as assays + fragments."""
    import mudata as md
    mdata = md.read_h5mu(src)
    mods = list(mdata.mod)
    key = next((m for m in mods if m.lower() in ('rna', 'gex')), mods[0])
    adata = mdata.mod[key].copy()
    order = adata.obs_names
    glob = mdata.obs.reindex(order)
    adata.obs = adata.obs.join(glob[[c for c in glob.columns if c not in adata.obs.columns]], how='left')
    for k in mdata.obsm.keys():
        try:
            v = mdata.obsm[k]
            v = v.loc[order].to_numpy() if hasattr(v, 'loc') else np.asarray(v)[mdata.obs_names.get_indexer(order)]
            adata.obsm[k] = v
        except Exception:
            pass
    kind_of = lambda m: 'peaks' if m.lower() in ('atac', 'peaks', 'chromatin') else 'chromvar' if 'chromvar' in m.lower() else \
        'protein' if m.lower() in ('prot', 'adt', 'protein') else 'activity' if 'activity' in m.lower() else 'rna'
    primary = dict(key=key, label=assay_label(key, kind_of(key)), type=kind_of(key)) if len(mods) > 1 else None
    extras, fragments = [], []
    for m in mods:
        if m == key:
            continue
        mod = mdata.mod[m]
        _, X, var, _ = pick_matrix(mod)
        extras.append(dict(key=m, label=assay_label(m, kind_of(m)), type=kind_of(m), X=align_rows(sp.csr_matrix(X), mod.obs_names, order),
                           names=list(map(str, var.index))))
        frag = (mod.uns.get('files') or {}).get('fragments') if kind_of(m) == 'peaks' else None
        if frag and Path(frag).exists():
            pos = pd.Index(order).get_indexer(mod.obs_names)
            fragments.append((frag, {int(p): bc for p, bc in zip(pos, mod.obs_names) if p >= 0}))
    return adata, primary, extras, fragments


def run_r_export(src: Path, work: Path, a):
    rscripts = [r for r in (a.rscript or '').split(',') if r] or ['Rscript']
    last = None
    for rs in rscripts:
        if not shutil.which(rs) and not Path(rs).exists():
            continue
        progress(3, f'Exporting the R object with {rs}')
        cmd = [rs, '--vanilla', str(HERE / 'rds_export.R'), str(src), str(work)] + ([a.assay] if a.assay else [])
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        tail = []
        for line in proc.stdout:
            line = line.rstrip('\n')
            if line.startswith('@@progress '):
                _, pct, msg = line.split(' ', 2)
                progress(3 + int(pct) * 0.37, msg)  # the R part is 3-40 %
            else:
                print(line, flush=True)
                tail.append(line)
                tail = tail[-20:]
        if proc.wait() == 0 and (work / 'export.json').exists():
            return json.loads((work / 'export.json').read_text())
        last = '\n'.join(tail[-8:])
        log(f'  {rs} failed; trying the next R installation' if rs != rscripts[-1] else f'  {rs} failed')
    raise SystemExit(f'could not read the RDS file with R:\n{last}')


# ----------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('source', type=Path)
    ap.add_argument('out', type=Path)
    ap.add_argument('--id', required=True)
    ap.add_argument('--title')
    ap.add_argument('--kind', default='auto', choices=['auto', 'spatial', 'singlecell', 'crispr'])
    ap.add_argument('--technology')
    ap.add_argument('--species')
    ap.add_argument('--feature', default='Gene expression')
    ap.add_argument('--matrix', help='expression source: X, raw or a layer name (default: first with integer counts)')
    ap.add_argument('--assay', help='Seurat assay (default: the active assay)')
    ap.add_argument('--embedding', action='append', default=[], help='"obsm_key=Label"')
    ap.add_argument('--label', action='append', default=[], help='"column=Display name"')
    ap.add_argument('--default', help='annotation shown first')
    ap.add_argument('--pixel-size', type=float, help='µm per coordinate unit (spatial)')
    ap.add_argument('--spot-diameter', type=float, help='µm, drawn size of each spot/cell (spatial)')
    ap.add_argument('--compute-umap', action='store_true', help='also compute a UMAP for spatial data')
    ap.add_argument('--no-umap', action='store_true', help='never compute a UMAP')
    ap.add_argument('--screen-kind', choices=['auto', 'enhancer', 'perturbseq'], default='auto')
    ap.add_argument('--fdr', type=float, default=0.1)
    ap.add_argument('--rscript', default=os.environ.get('VIZHUB_RSCRIPT', 'Rscript'), help='comma-separated Rscript paths, tried in order')
    ap.add_argument('--workers', type=int, default=4, help='Xenium image tiling processes')
    ap.add_argument('--fresh', action='store_true', help='ignore checkpoints of an earlier, interrupted attempt')
    ap.add_argument('--gene-activity', choices=['auto', 'yes', 'no'], default='auto',
                    help='treat RNA-like assays of an ATAC object as gene activity (auto: totals follow the peak totals)')
    ap.add_argument('--no-fragments', action='store_true', help='skip the accessibility tracks even if fragment files are found')
    ap.add_argument('--fragment-workers', type=int, default=16, help='chromosomes processed in parallel')
    ap.add_argument('--atac-chroms', help='only these chromosomes (comma-separated), for a quick test')
    ap.add_argument('--gtf', type=Path, help='GTF for the accessibility gene track when the object has no gene annotation')
    ap.add_argument('--assembly', help='genome assembly shown with the accessibility tracks (default: from the object)')
    a = ap.parse_args()

    src, out = a.source.resolve(), a.out
    t0 = time.time()
    out.mkdir(parents=True, exist_ok=True)
    manifest = dict(version=CONVERTER_VERSION, **source_stamp(src), status='running', started=int(t0), id=a.id)
    (out / 'vizhub.json').write_text(json.dumps(manifest, indent=1))
    work = out / '.work'
    # Checkpoints: an interrupted conversion (THOR or the hub restarted) resumes from the last finished step:
    # the R export, the viewer files, and every finished chromosome of the accessibility tracks.
    ckpt = out / '.checkpoint'
    stamp = source_stamp(src)
    try:
        previous = json.loads((ckpt / 'source.json').read_text())
    except Exception:
        previous = None
    if a.fresh or (previous and previous != stamp):
        if previous and previous != stamp and not a.fresh:
            log('the source changed since the last attempt: starting over')
        for d in (work, ckpt, out / '.atac-work'):
            shutil.rmtree(d, ignore_errors=True)
    ckpt.mkdir(parents=True, exist_ok=True)
    (ckpt / 'source.json').write_text(json.dumps(stamp))
    done = lambda step: (ckpt / f'{step}.done').exists()
    mark = lambda step: (ckpt / f'{step}.done').write_text(str(int(time.time())))
    if done('r-export') or done('slide'):
        notice('Resuming an interrupted conversion: finished steps are skipped.')
    try:
        name = src.name.lower()
        kind = a.kind
        if src.is_dir() and (src / 'experiment.xenium').exists():
            progress(2, 'Xenium bundle: running ATHENA xenium_to_viewer.py')
            import xenium_to_viewer
            sys.argv = ['xenium_to_viewer.py', str(src), str(out), '--id', a.id, '--title', a.title or src.name, '--workers', str(a.workers)]
            for spec in a.label:
                sys.argv += ['--label', spec]
            if a.default:
                sys.argv += ['--default', a.default]
            xenium_to_viewer.main()
            kind, layout = 'spatial', 'slide'
        elif name.endswith(('.tsv', '.tsv.gz', '.csv', '.csv.gz', '.txt', '.txt.gz', '.parquet')):
            import crispr_to_viewer
            crispr_to_viewer.convert_file(src, out, a, progress)
            kind, layout = 'crispr', 'screen'
        else:
            spatial_xy, technology, groups = None, a.technology, None
            primary, extras, motifs, fragments, annotation, genome = None, [], {}, [], None, None
            adata, kind = None, None
            slide_done = done('slide') and (out / 'slide.json').exists()
            if name.endswith('.rds'):
                if done('r-export') and (work / 'export.json').exists():
                    info = json.loads((work / 'export.json').read_text())
                    progress(40, 'R export already done (resumed)')
                else:
                    info = run_r_export(src, work, a)
                    mark('r-export')
                if info['type'] == 'table':
                    if a.kind not in ('auto', 'crispr'):
                        raise SystemExit(f'this RDS holds a table ({info.get("what")}), not cells: choose the CRISPR explorer')
                    import crispr_to_viewer
                    crispr_to_viewer.convert_file(work / 'table.tsv', out, a, progress, source_label=str(src))
                    kind, layout = 'crispr', 'screen'
                    adata = None
                elif slide_done:
                    order = (out / 'cell_ids.txt').read_text().splitlines()
                    _, _, _, fragments, annotation, genome = rds_multiome(work, info, order, matrices=False)
                else:
                    if a.kind == 'crispr':
                        raise SystemExit(f'this RDS holds a {info["type"]} object (cells), not CRISPR results')
                    adata, spatial_xy, tech, groups, info = read_rds_export(work, a)
                    if spatial_xy is not None and any(x.get('type') == 'peaks' for x in info.get('assays') or []):
                        tech = f'Spatial ATAC ({tech})' if tech else 'Spatial ATAC'
                    technology = technology or tech
                    primary, extras, motifs, fragments, annotation, genome = rds_multiome(work, info, list(adata.obs_names))
                    mark_gene_activity(adata, primary, extras, a.gene_activity)
            elif slide_done:
                if name.endswith('.h5mu'):  # only the fragment files are needed
                    _, _, _, fragments = read_h5mu(src, a)
            else:
                import anndata as ad
                progress(3, f'Reading {src.name}')
                if name.endswith('.h5ad'):
                    adata = ad.read_h5ad(src)
                elif name.endswith('.h5mu'):
                    adata, primary, extras, fragments = read_h5mu(src, a)
                    mark_gene_activity(adata, primary, extras, a.gene_activity)
                elif name.endswith('.loom'):
                    adata = ad.read_loom(src)
                elif name.endswith('.h5'):
                    import scanpy as sc
                    adata = sc.read_10x_h5(src)
                    adata.var_names_make_unique()
                else:
                    raise SystemExit(f'unsupported file type: {src.name}')
                progress(15, f'Read {adata.n_obs:,} cells x {adata.n_vars:,} genes')
                key = next((k for k in SPATIAL_KEYS if k in adata.obsm), None)
                if key and a.kind != 'singlecell':
                    spatial_xy = np.asarray(adata.obsm[key])
                if a.kind == 'spatial' and spatial_xy is None:
                    raise SystemExit('no spatial coordinates (obsm["spatial"]) in this file: choose the single-cell viewer')
                if a.kind == 'crispr':
                    raise SystemExit('an AnnData file holds cells, not CRISPR results')
            slide = None
            if slide_done:
                slide = json.loads((out / 'slide.json').read_text())
                progress(80, 'Viewer files already written (resumed)')
            elif adata is not None:
                if a.kind == 'spatial' and spatial_xy is None:
                    raise SystemExit('this object has no tissue coordinates: choose the single-cell viewer')
                slide = write_slide(adata, out, a, spatial_xy=spatial_xy, groups=groups, technology=technology,
                                    primary=primary, extras=extras, motifs=motifs)
                mark('slide')
            if slide is not None:
                kind, layout = ('spatial' if slide['spatial'] else 'singlecell'), 'slide'
                if fragments and not a.no_fragments and not done('atac'):
                    import atac_to_viewer
                    pk = next((x for x in slide.get('assays') or [] if x['type'] == 'peaks'), None)
                    if pk and pk.get('features'):
                        peak_names = json.loads((out / pk['features']).read_text())['names']
                    else:
                        peak_names = slide['genes'] if pk else None
                    atac_to_viewer.build(out, fragments, annotation=annotation, gtf=a.gtf, assembly=a.assembly or genome or (slide.get('species') or ''),
                                         peaks=peak_names, peak_label=pk['label'] if pk else 'Peaks', workers=a.fragment_workers,
                                         only=a.atac_chroms.split(',') if a.atac_chroms else None, progress=progress, notice=notice, lo=80, hi=99)
                    mark('atac')
                elif fragments and a.no_fragments:
                    log('  fragment files found; accessibility tracks skipped (--no-fragments)')
        for d in (work, ckpt):  # finished: the checkpoints are no longer needed
            shutil.rmtree(d, ignore_errors=True)
        total = sum(f.stat().st_size for f in out.iterdir() if f.is_file())
        manifest.update(status='done', kind=kind, layout=layout, finished=int(time.time()), bytes=total,
                        title=a.title, seconds=round(time.time() - t0))
        (out / 'vizhub.json').write_text(json.dumps(manifest, indent=1))
        progress(100, f'Done: {total / 1e6:.0f} MB in {out}')
    except BaseException as e:
        manifest.update(status='failed', error=str(e)[-2000:], finished=int(time.time()))
        (out / 'vizhub.json').write_text(json.dumps(manifest, indent=1))
        raise


if __name__ == '__main__':
    main()
