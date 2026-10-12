"""Chromatin accessibility tracks for a converted slide (the viewer's "Chromatin accessibility" panel).

Generalizes ATHENA's scripts/singlecell/fragments_to_viewer.py (same atac.json / atac.bin / atac.idx / genes/
format, documented there) to what Seurat/Signac and MuData objects carry:
  * several fragment files, each with its own cell -> barcode mapping (Signac Fragment@cells);
  * gene models from the object's gene annotation (Signac @annotation: exon rows per transcript), or a GTF;
  * a peak row from the object's own peak set (peaks/<chrom>.json, one group).
"""
import gzip
import json
import pickle
import re
import subprocess
import zlib
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

CHUNK_BINS = 1000
MAIN_CHROM = re.compile(r'(chr)?([0-9]+|X|Y)')


def chromosomes(files):
    seen = []
    for f in files:
        out = subprocess.run(['tabix', '-l', str(f)], check=True, capture_output=True, text=True).stdout.split()
        seen += [c for c in out if MAIN_CHROM.fullmatch(c) and c not in seen]
    return seen


def convert_chromosome(args):
    """Insertion counts per (bin, cell) for one chromosome over all fragment files (ATHENA's encoding)."""
    sources, chrom, n, bin_size = args
    keys, per_cell, max_pos = [], np.zeros(n, np.int64), 0
    for frag, mapping in sources:
        m = pd.read_csv(mapping, sep='\t', header=None, names=['bc', 'cell'], dtype={'bc': str, 'cell': np.int64})
        index = pd.Index(m['bc'])
        cells_of = m['cell'].to_numpy()
        proc = subprocess.Popen(['tabix', str(frag), chrom], stdout=subprocess.PIPE)
        for df in pd.read_csv(proc.stdout, sep='\t', header=None, usecols=[1, 2, 3], names=['s', 'e', 'bc'],
                              dtype={'s': np.int64, 'e': np.int64, 'bc': str}, chunksize=20_000_000):
            hit = index.get_indexer(df['bc'])
            ok = hit >= 0
            cell = cells_of[hit[ok]]
            s, e = df['s'].to_numpy()[ok], df['e'].to_numpy()[ok] - 1  # Tn5 cut sites: start and end-1
            if e.size:
                max_pos = max(max_pos, int(e.max()) + 1)
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
        comp = zlib.compressobj(6, zlib.DEFLATED, 31)  # gzip container (the browser's DecompressionStream)
        blocks.append(comp.compress(raw) + comp.flush())
    return chrom, max(max_pos, n_bins * bin_size), per_cell, blocks


def models_from_annotation(df):
    """Signac annotation rows (seqnames, start, end, strand, gene_name, gene_biotype, type, tx_id; 1-based)
    -> per chromosome [symbol, start, end, strand, type letter, exons] using each gene's longest transcript."""
    df = df[df['type'] == 'exon'] if 'type' in df and (df['type'] == 'exon').any() else df
    if 'gene_biotype' in df:
        df = df[df['gene_biotype'].isin(['protein_coding', 'lncRNA'])]
    df = df.dropna(subset=['gene_name', 'tx_id'])
    spans = df.groupby(['gene_name', 'tx_id'], sort=False).agg(s=('start', 'min'), e=('end', 'max')).reset_index()
    spans['len'] = spans['e'] - spans['s']
    best = spans.sort_values('len', ascending=False).drop_duplicates('gene_name')
    ex = df[df['tx_id'].isin(set(best['tx_id']))].sort_values(['tx_id', 'start'])
    by_chrom = defaultdict(list)
    for (gene, tx), g in ex.groupby(['gene_name', 'tx_id'], sort=False):
        exons = list(zip((g['start'] - 1).tolist(), g['end'].tolist()))
        strand = str(g['strand'].iloc[0])
        letter = 'p' if 'gene_biotype' not in g or g['gene_biotype'].iloc[0] == 'protein_coding' else 'l'
        by_chrom[str(g['seqnames'].iloc[0])].append([str(gene), exons[0][0], max(e for _, e in exons),
                                                    strand if strand in '+-' else '+', letter, exons])
    for v in by_chrom.values():
        v.sort(key=lambda r: r[1])
    return by_chrom


def models_from_gtf(gtf):
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from fragments_to_viewer import gene_models  # ATHENA's GTF reader
    return gene_models(gtf)


PEAK = re.compile(r'^(chr[\w.]+|[0-9XYM]+)[:_-](\d+)[-_](\d+)$')


def write_peaks(ds: Path, names, label):
    by_chrom = defaultdict(list)
    for n in names:
        m = PEAK.match(n)
        if m:
            s, e = int(m.group(2)), int(m.group(3))
            by_chrom[m.group(1)].append([s, e, 0, (e - s) // 2, 10.0])
    if not by_chrom:
        return None
    out = ds / 'peaks'
    out.mkdir(exist_ok=True)
    total = 0
    for chrom, rows in by_chrom.items():
        rows.sort()
        (out / f'{chrom}.json').write_text(json.dumps(dict(groups=[label], peaks=rows), separators=(',', ':')))
        total += len(rows)
    return dict(dir='peaks', annotation='__peaks__', groups=[label], caller=f'peak set of the {label} assay', count=total, minNlog10q=0)


READ_MB_PER_S = 6  # tabix + parsing of one fragment file on THOR's NFS, measured


def estimate_hours(gb, workers, only=None):
    """Rough wall time: chromosomes run in parallel; the largest (chr1, ~8.5% of the genome) bounds it."""
    if only:
        return gb * 0.04 * len(only) / READ_MB_PER_S * 1000 / 3600
    return max(gb * 0.085, gb / max(1, workers)) * 1000 / READ_MB_PER_S / 3600


def build(ds: Path, fragments, *, annotation=None, gtf=None, assembly='', peaks=None, peak_label='Peaks',
          bin_size=100, workers=16, only=None, progress=lambda p, m: None, notice=lambda m: None, lo=80, hi=99):
    """fragments: [(path, {cell index: barcode})]; writes atac.* + genes/ (+ peaks/) and slide.json "atac".
    Every finished chromosome is kept in .atac-work/ so an interrupted build resumes where it stopped."""
    slide = json.loads((ds / 'slide.json').read_text())
    n = slide['nCells']
    work = ds / '.atac-work'
    work.mkdir(exist_ok=True)
    sources = []
    for k, (path, barcodes) in enumerate(fragments):
        mp = work / f'map{k}.tsv'
        pd.DataFrame({'bc': list(barcodes.values()), 'cell': list(barcodes.keys())}).to_csv(mp, sep='\t', header=False, index=False)
        sources.append((str(path), str(mp)))
    chroms = chromosomes([p for p, _ in fragments])
    if only:
        chroms = [c for c in chroms if c in only]
    size = sum(Path(p).stat().st_size for p, _ in fragments)
    cache = lambda c: work / f'chrom_{c}.pkl'
    results = []
    for c in chroms:
        if cache(c).exists():
            try:
                results.append(pickle.loads(cache(c).read_bytes()))
            except Exception:
                cache(c).unlink()
    todo = [c for c in chroms if not cache(c).exists()]
    hours = estimate_hours(size / 1e9 * len(todo) / max(1, len(chroms)), workers, only)
    if results:
        notice(f'Resuming the accessibility tracks: {len(results)} of {len(chroms)} chromosomes were already done.')
    if hours >= 0.5:
        notice(f'Long step: the accessibility tracks read {size / 1e9:.0f} GB of fragments, roughly '
               f'{hours:.1f} h. Each finished chromosome is saved: if THOR or the hub restarts, the conversion '
               f'continues from there by itself. You can close this page meanwhile.')
    progress(lo, f'Reading {len(fragments)} fragment file(s), {size / 1e9:.0f} GB, {len(todo)} of {len(chroms)} chromosomes to do')
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(convert_chromosome, (sources, c, n, bin_size)) for c in todo]
        for f in as_completed(futs):
            r = f.result()
            tmp = cache(r[0]).with_suffix('.tmp')
            tmp.write_bytes(pickle.dumps(r, protocol=5))
            tmp.replace(cache(r[0]))  # atomic: a half-written checkpoint is never read
            results.append(r)
            k = len(results)
            progress(lo + (hi - lo - 2) * k / len(chroms), f'Accessibility: {k} of {len(chroms)} chromosomes ({r[0]})')
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

    models = models_from_annotation(annotation) if annotation is not None else models_from_gtf(gtf) if gtf else {}
    genes_dir = ds / 'genes'
    genes_dir.mkdir(exist_ok=True)
    index = {}
    for chrom, rows in models.items():
        if chrom not in order:
            continue
        (genes_dir / f'{chrom}.json').write_text(json.dumps(rows, separators=(',', ':')))
        for name, s, e, strand, *_ in rows:
            index.setdefault(name, [chrom, s, e, strand])
    (genes_dir / 'index.json').write_text(json.dumps(index, separators=(',', ':')))
    atac = dict(file='atac', binSize=bin_size, chunkBins=CHUNK_BINS, assembly=assembly, nCells=n, genes='genes',
                chromosomes=table, insertions=float(totals.sum()))
    if peaks:
        pk = write_peaks(ds, peaks, peak_label)
        if pk:
            atac['peaks'] = pk
    (ds / 'atac.json').write_text(json.dumps(atac, separators=(',', ':')))
    slide['atac'] = dict(file='atac.json', assembly=assembly, binSize=bin_size)
    (ds / 'slide.json').write_text(json.dumps(slide, separators=(',', ':')))
    for p in work.iterdir():
        p.unlink()
    work.rmdir()
    print(f'accessibility: {totals.sum():,.0f} insertions, atac.bin {off / 1e9:.2f} GB, {len(index)} genes', flush=True)
