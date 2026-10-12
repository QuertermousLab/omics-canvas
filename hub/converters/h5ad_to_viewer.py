#!/usr/bin/env python3
"""Convert an AnnData .h5ad (e.g. a CELLxGENE Discover dataset) into the ATHENA viewer format.

Writes the same files as xenium_to_viewer.py (slide.json, cells.bin, cell_ids.txt, expr.bin,
expr.idx; no image or boundaries), so the deck.gl viewer shows it unchanged:

  * with --spatial, obsm["spatial"] gives each cell's position (e.g. Slide-seq beads) and the
    dataset belongs in the spatial viewer (Drive ATHENA/spatial/view/<project>/<id>/);
  * without it, the dataset has no tissue coordinates ("spatial": false in slide.json) and the
    single-cell viewer opens it on its first embedding (Drive ATHENA/singlecell/view/...).

Expression comes from raw integer counts (.raw.X, else .X) and is log-normalized in the browser,
like Seurat's LogNormalize. For scATAC datasets the matrix is gene activity (--feature "Gene activity").

Usage:
  python h5ad_to_viewer.py IN.h5ad OUT_DIR --id aorta --title "Aorta" --spatial --pixel-size 0.65 \
      --spot-diameter 10 --technology Slide-seqV2 --source-url https://cellxgene.cziscience.com/...
  python h5ad_to_viewer.py IN.h5ad OUT_DIR --id smc-rna --title "SMC RNA" --technology "10x 3' v3" \
      --embedding "X_fle=Force-directed layout" --default Cluster_type
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import scipy.sparse as sp

sys.path.insert(0, str(Path(__file__).resolve().parent))
from xenium_to_viewer import Columns, default_label  # noqa: E402  (shared annotation encoding)

EMBEDDING_LABELS = {'X_umap': 'UMAP', 'X_tsne': 't-SNE', 'X_fle': 'Force-directed layout', 'X_draw_graph_fa': 'Force-directed layout'}

# CELLxGENE schema columns worth showing, with readable names; every other obs column is the authors'
CXG_LABELS = {
    'cell_type': 'Cell type (Cell Ontology)', 'tissue': 'Tissue', 'disease': 'Disease', 'sex': 'Sex',
    'development_stage': 'Age', 'donor_id': 'Donor', 'assay': 'Assay', 'self_reported_ethnicity': 'Ethnicity',
}
AUTHOR_LABELS = {
    'author_cell_type': 'Cell type (authors)', 'Cluster_type': 'Cell type (authors)',
    'Cluster_type_full': 'Cell type (authors)', 'predicted.id': 'Cell type (label transfer from RNA)',
    'Weeks_on_HFD': 'Weeks on high-fat diet', 'Genotype': 'Genotype', 'Sample': 'Sample',
    'pseudotime': 'Pseudotime', 'pseudotime_scaled': 'Pseudotime (scaled)', 'bin_30': 'Pseudotime bin (of 30)',
    'nCount_RNA': 'UMI counts', 'nFeature_RNA': 'Genes detected', 'percent.mt': '% mitochondrial reads',
    'percent.rp': '% ribosomal reads', 'spot_class': 'RCTD spot class', 'second_type': 'RCTD second cell type',
    'nCount_peaks': 'Fragments in peaks', 'nFeature_peaks': 'Peaks detected', 'TSS.enrichment': 'TSS enrichment',
    'nucleosome_signal': 'Nucleosome signal', 'pct_reads_in_peaks': '% reads in peaks',
    'numReads': 'Reads', 'pct_rrna': '% rRNA reads', 'nCount_SCT': 'UMI counts (SCT)', 'nFeature_SCT': 'Genes detected (SCT)',
    'min_score': 'RCTD min score', 'singlet_score': 'RCTD singlet score', 'total': 'Fragments', 'TSS_fragments': 'TSS fragments',
}
SKIP = ('_ontology_term_id', 'uuid', 'observation_joinid', 'is_primary_data', 'donor_living_at_sample_collection',
        'mapped_reference_annotation', 'suspension_', 'sample_derivation', 'sample_preservation', 'tissue_type',
        'sequencing_platform', 'orig.ident', 'log10_', 'TSS.percentile', 'nCount_ACTIVITY', 'nFeature_ACTIVITY')
DEFAULT_PREFERENCE = ['author_cell_type', 'Cluster_type_full', 'Cluster_type', 'predicted.id', 'cell_type']


def gene_symbols(var):
    """Display names for the genes: feature_name (CELLxGENE) or gene_symbols, made unique."""
    for col in ('feature_name', 'gene_symbols', 'gene_name'):
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


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('h5ad', type=Path)
    ap.add_argument('out', type=Path, help='output dataset folder (created)')
    ap.add_argument('--id', required=True)
    ap.add_argument('--title')
    ap.add_argument('--technology', help='shown in the viewer, e.g. "Slide-seqV2"')
    ap.add_argument('--feature', default='Gene expression', help='what the matrix measures, e.g. "Gene activity"')
    ap.add_argument('--spatial', action='store_true', help='use obsm["spatial"] as cell positions')
    ap.add_argument('--pixel-size', type=float, default=1.0, help='µm per obsm["spatial"] unit (Slide-seqV2: 0.65)')
    ap.add_argument('--spot-diameter', type=float, default=10.0, help='µm, drawn size of each bead/spot')
    ap.add_argument('--embedding', action='append', default=[], help='"obsm_key=Label" (default: X_umap, X_fle, X_tsne)')
    ap.add_argument('--label', action='append', default=[], help='"column=Display name" (repeatable)')
    ap.add_argument('--default', help='annotation shown first')
    ap.add_argument('--source-url', help='page of the dataset at its source (e.g. CELLxGENE explorer)')
    ap.add_argument('--source-name', default='CELLxGENE Discover')
    a = ap.parse_args()

    import anndata as ad
    print(f'reading {a.h5ad}', flush=True)
    adata = ad.read_h5ad(a.h5ad)
    n = adata.n_obs
    ids = adata.obs_names.astype(str).to_numpy()

    # expression: raw integer counts, genes x cells ---------------------------------
    if adata.raw is not None:
        counts, var = adata.raw.X, adata.raw.var
        if 'feature_name' not in var and 'feature_name' in adata.var:
            var = var.join(adata.var[['feature_name']], how='left')
    else:
        counts, var = adata.X, adata.var
    counts = sp.csr_matrix(counts)
    if counts.nnz and not np.all(np.mod(counts.data[:1_000_000], 1) == 0):
        sys.exit('the matrix is not integer counts (no .raw.X?); the viewer needs raw counts')
    total = np.asarray(counts.sum(axis=1)).ravel().astype(np.float32)  # before capping: normalization stays exact
    over = int((counts.data > 65535).sum())
    if over:  # stored as uint16; a handful of extreme values (e.g. MALAT1) are capped
        print(f'  capping {over} counts above 65535 (max {counts.data.max():.0f})', flush=True)
        counts.data = np.minimum(counts.data, 65535)
    by_gene = counts.T.tocsr()  # genes x cells, cell indices sorted per gene
    by_gene.sort_indices()
    del counts
    keep = np.flatnonzero(np.diff(by_gene.indptr) > 0)  # drop genes with no counts at all
    names = gene_symbols(var)[keep]

    idx = np.zeros((len(keep), 2), np.float64)
    a.out.mkdir(parents=True, exist_ok=True)
    with open(a.out / 'expr.bin', 'wb') as f:
        off = 0
        for k, g in enumerate(keep):
            s, e = by_gene.indptr[g], by_gene.indptr[g + 1]
            block = by_gene.indices[s:e].astype(np.uint32).tobytes() + by_gene.data[s:e].astype(np.uint16).tobytes()
            block += b'\0' * ((-len(block)) % 4)
            f.write(block)
            idx[k] = (off, e - s)
            off += len(block)
    idx.tofile(a.out / 'expr.idx')
    print(f'expression: {len(keep)} genes, {by_gene.nnz} non-zero, {off / 1e6:.0f} MB', flush=True)
    del by_gene

    # positions ---------------------------------------------------------------------
    embeddings_wanted = dict(spec.split('=', 1) for spec in a.embedding) if a.embedding else \
        {k: v for k, v in EMBEDDING_LABELS.items() if k in adata.obsm}
    if a.spatial:
        xy = np.asarray(adata.obsm['spatial'], dtype=np.float64)[:, :2]
        margin = 2 * a.spot_diameter / a.pixel_size
        xy = xy - xy.min(axis=0) + margin
        width, height = (xy.max(axis=0) + margin).tolist()
        radius = np.full(n, a.spot_diameter / 2 / a.pixel_size, np.float32)
    else:
        first = next(iter(embeddings_wanted), None)
        if not first:
            sys.exit('no embedding to show (pass --embedding or --spatial)')
        xy = np.asarray(adata.obsm[first], dtype=np.float64)[:, :2]
        width, height = float(np.nanmax(xy[:, 0])), float(np.nanmax(xy[:, 1]))
        radius = np.ones(n, np.float32)
    pos = np.empty(n * 2, np.float32)
    pos[0::2], pos[1::2] = xy[:, 0], xy[:, 1]

    # annotations -------------------------------------------------------------------
    labels = {**AUTHOR_LABELS, **dict(spec.split('=', 1) for spec in a.label)}
    cols = Columns(n)
    cols.raw(pos)
    cols.add('total_counts', total, 'Total counts', hidden=True)
    obs = adata.obs
    order = [c for c in CXG_LABELS if c in obs] + [c for c in obs.columns if c not in CXG_LABELS]
    for col in order:
        if any(s in col for s in SKIP) or obs[col].isna().all():
            continue
        group = 'CELLxGENE' if col in CXG_LABELS else 'Authors'
        before = len(cols.specs)
        cols.add(col, obs[col], CXG_LABELS.get(col) or labels.get(col) or default_label(col), group=group)
        # the authors' colors (scanpy keeps them in uns["<column>_colors"], in category order)
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

    (a.out / 'cells.bin').write_bytes(cols.bytes())
    (a.out / 'cell_ids.txt').write_text('\n'.join(ids) + '\n')

    cats = [s['key'] for s in cols.specs if s['type'] == 'category']
    default = a.default if a.default in cats else next((c for c in DEFAULT_PREFERENCE if c in cats), cats[0] if cats else None)
    uns = adata.uns
    organism = uns.get('organism') or (obs['organism'].iloc[0] if 'organism' in obs else None)
    citation = uns.get('citation')
    slide = dict(
        version=1, id=a.id, title=a.title or str(uns.get('title') or a.id), technology=a.technology,
        species=str(organism) if organism is not None else None, spatial=bool(a.spatial), feature=a.feature,
        nCells=n, pixelSize=a.pixel_size, width=float(width), height=float(height),
        genes=names.tolist(), obs=cols.specs, defaultObs=default, radiusOffset=radius_off,
        embeddings=embeddings, regions=[], boundaries=None, image=None,
        normalization='log1p(counts / total_counts * 10000)',
        source=dict(name=a.source_name, url=a.source_url, citation=str(citation) if citation else None) if a.source_url else None,
    )
    (a.out / 'slide.json').write_text(json.dumps(slide, separators=(',', ':')))
    total_mb = sum(f.stat().st_size for f in a.out.iterdir()) / 1e6
    print(f'done: {a.out} ({n} cells, {total_mb:.0f} MB)')


if __name__ == '__main__':
    main()
