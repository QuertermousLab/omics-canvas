#!/usr/bin/env python3
"""Add chromatin accessibility (an scATAC fragment file) to a dataset written by h5ad_to_viewer.py.

The viewer draws coverage tracks around a gene for any grouping of the cells (the annotation chosen in
the side panel, its visible categories, a lasso selection) and can color cells by the accessibility of a
region. That needs per-cell data, so this stores Tn5 insertion counts per cell in fixed genomic bins:

  atac.json   binSize, chunkBins, assembly, chromosomes [{name, length, chunk, chunks}], nCells
  atac.bin    float32[nCells] total insertions per cell (for normalization), then one gzip block per chunk
  atac.idx    float64 [byte offset, length] per chunk, chromosomes in atac.json order
  genes/      index.json {symbol: [chrom, start, end, strand]} and <chrom>.json gene models (canonical transcripts)

A chunk covers chunkBins consecutive bins. Inside its gzip block (little-endian):
  uint32 entries[chunkBins]   number of cells with insertions in each bin
  uint32 cellDelta[total]     per bin, cell indices as differences from the previous one (first one absolute)
  uint8  count[total]         insertions of that cell in that bin (capped at 255)
Cells are numbered as in cell_ids.txt; barcodes missing from it are dropped. Coordinates are 0-based.

Usage:
  python fragments_to_viewer.py FRAGMENTS.tsv.bgz DATASET_DIR --gtf gencode.vM33.primary_assembly.basic.annotation.gtf.gz \
      --assembly GRCm39 [--bin 100] [--workers 24]
"""
import argparse
import gzip
import json
import re
import subprocess
import sys
import zlib
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

CHUNK_BINS = 1000


def chromosomes(fragments):
    out = subprocess.run(['tabix', '-l', str(fragments)], check=True, capture_output=True, text=True).stdout.split()
    return [c for c in out if re.fullmatch(r'chr([0-9]+|X|Y)', c)]


def convert_chromosome(args):
    fragments, chrom, barcodes_path, bin_size = args
    barcodes = pd.Index(Path(barcodes_path).read_text().split('\n')[:-1])
    n = len(barcodes)
    proc = subprocess.Popen(['tabix', str(fragments), chrom], stdout=subprocess.PIPE)
    keys, per_cell, max_pos = [], np.zeros(n, np.int64), 0
    for df in pd.read_csv(proc.stdout, sep='\t', header=None, usecols=[1, 2, 3], names=['s', 'e', 'bc'],
                          dtype={'s': np.int64, 'e': np.int64, 'bc': str}, chunksize=20_000_000):
        cell = barcodes.get_indexer(df['bc'])
        ok = cell >= 0
        cell = cell[ok].astype(np.int64)
        s, e = df['s'].to_numpy()[ok], df['e'].to_numpy()[ok] - 1  # Tn5 cut sites: start and end-1
        max_pos = max(max_pos, int(e.max()) + 1 if e.size else 0)
        for pos in (s, e):
            keys.append((pos // bin_size) * n + cell)
        per_cell += 2 * np.bincount(cell, minlength=n)
    proc.wait()
    if not keys:
        return chrom, 0, per_cell, []
    k, counts = np.unique(np.concatenate(keys), return_counts=True)
    del keys
    bins, cells = k // n, (k % n).astype(np.uint32)
    counts = np.minimum(counts, 255).astype(np.uint8)
    n_bins = int(bins.max()) + 1
    n_chunks = -(-n_bins // CHUNK_BINS)
    starts = np.searchsorted(bins, np.arange(n_chunks + 1) * CHUNK_BINS)
    blocks = []
    for c in range(n_chunks):
        a, b = starts[c], starts[c + 1]
        cb, cc, cn = bins[a:b] - c * CHUNK_BINS, cells[a:b], counts[a:b]
        per_bin = np.bincount(cb, minlength=CHUNK_BINS).astype(np.uint32)
        delta = cc.astype(np.int64).copy()
        if delta.size:
            first = np.ones(delta.size, bool)
            first[1:] = cb[1:] != cb[:-1]
            delta[1:] -= cc[:-1].astype(np.int64)
            delta[first] = cc[first]
        raw = per_bin.tobytes() + delta.astype(np.uint32).tobytes() + cn.tobytes()
        comp = zlib.compressobj(6, zlib.DEFLATED, 31)  # wbits 31 = gzip container (DecompressionStream)
        blocks.append(comp.compress(raw) + comp.flush())
    length = max(max_pos, n_bins * bin_size)
    print(f'  {chrom}: {len(k):,} cell-bins, {n_chunks} chunks, {sum(map(len, blocks)) / 1e6:.0f} MB', flush=True)
    return chrom, length, per_cell, blocks


def gene_models(gtf, keep_types=('protein_coding', 'lncRNA')):
    """Canonical transcript per gene (Ensembl_canonical tag, else the longest) with its exons."""
    attr = re.compile(r'(\w+) "([^"]*)"')
    genes, tx_exons, tx_info, canonical = {}, defaultdict(list), {}, {}
    with gzip.open(gtf, 'rt') as f:
        for line in f:
            if line.startswith('#'):
                continue
            chrom, _, kind, start, end, _, strand, _, attrs = line.rstrip('\n').split('\t')
            if kind not in ('gene', 'transcript', 'exon'):
                continue
            a = dict(attr.findall(attrs))
            if a.get('gene_type') not in keep_types:
                continue
            gid = a['gene_id']
            if kind == 'gene':
                genes[gid] = dict(name=a['gene_name'], chrom=chrom, start=int(start) - 1, end=int(end), strand=strand,
                                  type=a['gene_type'])
            elif kind == 'transcript':
                tid = a['transcript_id']
                tx_info[tid] = (gid, int(end) - int(start))
                if 'tag "Ensembl_canonical"' in attrs:
                    canonical[gid] = tid
            else:
                tx_exons[a['transcript_id']].append((int(start) - 1, int(end)))
    longest = {}
    for tid, (gid, span) in tx_info.items():
        if gid not in longest or span > tx_info[longest[gid]][1]:
            longest[gid] = tid
    by_chrom = defaultdict(list)
    for gid, g in genes.items():
        tid = canonical.get(gid) or longest.get(gid)
        exons = sorted(tx_exons.get(tid, [(g['start'], g['end'])]))
        by_chrom[g['chrom']].append([g['name'], g['start'], g['end'], g['strand'], g['type'][0], exons])
    for v in by_chrom.values():
        v.sort(key=lambda r: r[1])
    return by_chrom


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('fragments', type=Path, help='bgzipped, tabix-indexed fragment file')
    ap.add_argument('dataset', type=Path, help='dataset folder written by h5ad_to_viewer.py (has cell_ids.txt)')
    ap.add_argument('--gtf', type=Path, required=True, help='GENCODE GTF for the gene track (same assembly)')
    ap.add_argument('--assembly', required=True, help='e.g. GRCm39 (shown in the viewer)')
    ap.add_argument('--bin', type=int, default=100, help='bin size in bp (default 100)')
    ap.add_argument('--workers', type=int, default=24)
    ap.add_argument('--only', help='comma-separated chromosomes, for a quick test')
    a = ap.parse_args()

    ds = a.dataset
    slide = json.loads((ds / 'slide.json').read_text())
    n = slide['nCells']
    chroms = chromosomes(a.fragments)
    if a.only:
        chroms = [c for c in chroms if c in a.only.split(',')]
    print(f'{len(chroms)} chromosomes, {n} cells, {a.bin} bp bins', flush=True)

    with ProcessPoolExecutor(max_workers=a.workers) as ex:
        results = list(ex.map(convert_chromosome, [(a.fragments, c, ds / 'cell_ids.txt', a.bin) for c in chroms]))
    order = {c: i for i, c in enumerate(chroms)}
    results.sort(key=lambda r: order[r[0]])

    totals = np.zeros(n, np.float64)
    for _, _, per_cell, _ in results:
        totals += per_cell
    table, idx, chunk = [], [], 0
    with open(ds / 'atac.bin', 'wb') as f:
        f.write(totals.astype(np.float32).tobytes())
        off = n * 4
        for chrom, length, _, blocks in results:
            if not blocks:
                continue
            table.append(dict(name=chrom, length=int(length), chunk=chunk, chunks=len(blocks)))
            for blk in blocks:
                f.write(blk)
                idx.append((off, len(blk)))
                off += len(blk)
            chunk += len(blocks)
    np.asarray(idx, np.float64).tofile(ds / 'atac.idx')

    genes_dir = ds / 'genes'
    genes_dir.mkdir(exist_ok=True)
    models = gene_models(a.gtf)
    index = {}
    for chrom, rows in models.items():
        if chrom not in order:
            continue
        (genes_dir / f'{chrom}.json').write_text(json.dumps(rows, separators=(',', ':')))
        for name, s, e, strand, *_ in rows:
            index.setdefault(name, [chrom, s, e, strand])
    (genes_dir / 'index.json').write_text(json.dumps(index, separators=(',', ':')))

    atac = dict(file='atac', binSize=a.bin, chunkBins=CHUNK_BINS, assembly=a.assembly, nCells=n, genes='genes',
                chromosomes=table, insertions=float(totals.sum()))
    (ds / 'atac.json').write_text(json.dumps(atac, separators=(',', ':')))
    slide['atac'] = dict(file='atac.json', assembly=a.assembly, binSize=a.bin)
    (ds / 'slide.json').write_text(json.dumps(slide, separators=(',', ':')))
    print(f'done: {totals.sum():,.0f} insertions in {n} cells; atac.bin {off / 1e9:.1f} GB, {len(index)} genes')


if __name__ == '__main__':
    main()
