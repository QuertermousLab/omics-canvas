#!/usr/bin/env python3
"""Convert the lab's released IGVF CRISPRi screen results (SCEPTRE tables) for the CRISPR screen explorer.

Each screen becomes one folder (Drive ATHENA/crispr/view/<screen>/, read through /api/data/crispr/…):

  project.json   title, description, kind ("enhancer" or "perturbseq"), species, assembly, IGVF sources, …
  enhancer screens (TAP-seq on candidate enhancers):
    tests.json   {"columns": [...], "rows": [[element chrom, start, end, gene, gene_id, log2fc, -log10 p,
                 -log10 adj p, significant (FDR < 0.1), set, guides], ...]}
    + locus-view arcs (enhancer → target-gene TSS, like scE2G): <locus view>/crispr/<screen>.{significant,all}.bedpe.gz
  Perturb-seq screens (CRISPRi on TSSs, scRNA-seq readout):
    targets.json {"genes": [readout symbols], "targets": [{name, geneId, chrom, start, end, guides, knockdown:
                 {log2fc, padj, significant}, tested, up, down}]}
    de.bin/.idx  per target: rows of uint32 gene index, float32 log2fc, -log10 p, -log10 adj p (16 bytes);
                 idx = float64 [byte offset, rows] per target, in targets.json order
    bygene.bin/.idx  the same rows grouped by readout gene (uint32 target index first), for "who changes gene X"

Only tested pairs are kept (SCEPTRE p < 1). Usage (THOR, ~/baldar/app/athena/crispr):
  venv/bin/python igvf_to_viewer.py --source source --out data/view --locus ../locus/data/view
"""
import argparse
import gzip
import json
from pathlib import Path

import numpy as np
import pandas as pd

IGVF = 'https://data.igvf.org'
LOG10_MAX = 300.0  # SCEPTRE reports p down to 1e-250; clip infinities
FDR = 0.1  # significance: SCEPTRE adjusted p (Benjamini-Hochberg FDR) below this, for every screen

SCREENS = [
    dict(id='hcasmc-enhancer-tapseq', kind='enhancer', species='Human', assembly='GRCh38', cell='HCASMC-hTERT',
         title='HCASMC enhancer CRISPRi · TAP-seq',
         description='CRISPRi of candidate enhancers at CAD GWAS loci in immortalized human coronary artery smooth muscle '
                     'cells (HCASMC-hTERT), targeted single-cell readout (TAP-seq) of nearby genes; SCEPTRE element–gene tests.',
         sets=[('IGVFDS8572NQGC', 'IGVFFI1168JUYR', 'CAD loci'), ('IGVFDS5581QHNK', 'IGVFFI3434YAPX', '9p21 and SMAD3')],
         citation='Ramste M et al. Enhancer-targeting CRISPR screens at coronary artery disease loci suggest shared mechanisms of disease risk. medRxiv 2025'),
    dict(id='hcasmc-tss-perturbseq', kind='perturbseq', species='Human', assembly='GRCh38', cell='HCASMC',
         title='HCASMC TSS CRISPRi · Perturb-seq (56 genes)',
         description='CRISPRi at the TSS of 56 CAD and SMC regulator genes in HCASMC, whole-transcriptome single-cell readout '
                     '(Parse); SCEPTRE knockdown (local) and transcriptome-wide (global) tests.',
         set='IGVFDS6546QFJY', local='IGVFFI0202JQNH', globl='IGVFFI6376HTIF'),
    dict(id='hcasmc-genome-scale-perturbseq', kind='perturbseq', species='Human', assembly='GRCh38', cell='HCASMC',
         title='HCASMC TSS CRISPRi · Perturb-seq (971 genes)',
         description='CRISPRi at the TSS of 971 genes in HCASMC, single-cell readout (Parse); SCEPTRE knockdown and '
                     'global tests on 4,148 readout genes.',
         set='IGVFDS6248NDUW', local='IGVFFI7253BTIQ', globl='IGVFFI0206LUDV'),
    dict(id='mouse-invivo-perturbseq', kind='perturbseq', species='Mouse', assembly='GRCm39', cell='aortic SMC, in vivo',
         title='Mouse in vivo CRISPRi · Perturb-seq (19 genes)',
         description='AAV-delivered guide library in SMC-specific dCas9-KRAB mice (Myh11-CreERT2, ApoE−/−), aortic SMC '
                     'single-cell readout; SCEPTRE knockdown and global tests.',
         set='IGVFDS8039PPZU', local='IGVFFI9608IGMY', globl='IGVFFI9159XDOS'),
]


def nlog10(p):
    p = np.asarray(p, dtype=np.float64)
    with np.errstate(divide='ignore'):
        return np.minimum(-np.log10(np.clip(p, 0, 1)), LOG10_MAX).astype(np.float32)


def source_links(sets):
    return [dict(accession=s, url=f'{IGVF}/analysis-sets/{s}/') for s in sets]


def write_json(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, separators=(',', ':'), ensure_ascii=False))


# ----------------------------------------------------------------- enhancer screens

def enhancer(cfg, src: Path, out: Path, locus: Path | None, tss):
    frames = []
    for set_id, file_id, label in cfg['sets']:
        d = pd.read_csv(src / f'{file_id}.tsv.gz', sep='\t')
        d['set'] = label
        frames.append(d)
    d = pd.concat(frames, ignore_index=True)
    d = d[d['type'] == 'element_targeting']
    guides = d['guide_id(s)'].astype(str).str.count(',') + 1
    rows = [[r.targeting_chr, int(r.targeting_start), int(r.targeting_end), r.gene_symbol, r.gene_id,
             round(float(r.sceptre_log2_fc), 4), round(float(p), 2), round(float(q), 2), bool(r.sceptre_adj_p_value < FDR), r.set, int(g)]
            for r, p, q, g in zip(d.itertuples(), nlog10(d['sceptre_p_value']), nlog10(d['sceptre_adj_p_value']), guides)]
    rows.sort(key=lambda r: (r[0], r[1], r[3]))
    cols = ['chrom', 'start', 'end', 'gene', 'geneId', 'log2fc', 'nlog10p', 'nlog10padj', 'significant', 'set', 'guides']
    write_json(out / 'tests.json', dict(columns=cols, rows=rows))
    print(f'  {len(rows)} tests, {sum(r[8] for r in rows)} significant, {len({tuple(r[:3]) for r in rows})} elements')

    if locus:  # arcs for the locus view: enhancer -> TSS of the tested gene
        missing = set()
        for which, keep in (('significant', lambda r: r[8]), ('all', lambda r: True)):
            arcs = []
            for r in rows:
                if not keep(r):
                    continue
                t = tss.get(r[3])
                if not t or t[0] != r[0]:
                    missing.add(r[3])
                    continue
                name = f"{r[3]} log2FC {r[5]:+.2f} · FDR {10 ** -r[7]:.1e}" if r[8] else f"{r[3]} log2FC {r[5]:+.2f} (n.s.)"
                # score = effect size, so arc height reads as knockdown strength (as scE2G uses its score)
                arcs.append((r[0], r[1], r[2], r[0], t[1], t[1] + 1, name, f'{abs(r[5]):.4f}', '.', '.'))
            arcs.sort(key=lambda a: (a[0], a[1], a[4]))
            dest = locus / 'crispr' / f"{cfg['id']}.{which}.bedpe.gz"
            dest.parent.mkdir(parents=True, exist_ok=True)
            with gzip.open(dest, 'wt') as f:
                for a in arcs:
                    f.write('\t'.join(map(str, a)) + '\n')
            print(f'  locus arcs ({which}): {len(arcs)} → {dest}')
        if missing:
            print(f'  no hg38 TSS for {len(missing)} genes (no arc): {sorted(missing)[:10]}')
    return dict(tests=len(rows), significant=int(sum(r[8] for r in rows)))


# ----------------------------------------------------------------- Perturb-seq screens

def perturbseq(cfg, src: Path, out: Path):
    local = pd.read_csv(src / f"{cfg['local']}.tsv.gz", sep='\t')
    local.columns = [c.strip().replace(' ', '') for c in local.columns]
    use = ['intended_target_name', 'Intended_target_gene_id', 'guide_id(s)', 'targeting_chr', 'targeting_start',
           'targeting_end', 'gene_id', 'gene_symbol', 'sceptre_log2_fc', 'sceptre_p_value', 'sceptre_adj_p_value']
    parts = []
    for c in pd.read_csv(src / f"{cfg['globl']}.tsv.gz", sep='\t', usecols=use, chunksize=5_000_000):
        parts.append(c[c['sceptre_p_value'] < 1])
    g = pd.concat(parts, ignore_index=True)
    del parts

    # a few readout genes have no symbol: show their Ensembl id instead
    g['gene_symbol'] = g['gene_symbol'].astype(object).where(g['gene_symbol'].notna(), g['gene_id']).map(str)
    genes = sorted(g['gene_symbol'].unique())
    gene_ix = {s: i for i, s in enumerate(genes)}
    first = g.drop_duplicates('intended_target_name').set_index('intended_target_name')
    kd = local.set_index('intended_target_name')
    names = sorted(set(first.index) | set(kd.index))

    g['t'] = g['intended_target_name'].map({n: i for i, n in enumerate(names)}).astype(np.int64)
    g['gi'] = g['gene_symbol'].map(gene_ix).astype(np.int64)
    g['lp'], g['lq'] = nlog10(g['sceptre_p_value']), nlog10(g['sceptre_adj_p_value'])
    sig = g['sceptre_adj_p_value'] < FDR

    targets = []
    for i, n in enumerate(names):
        f = first.loc[n] if n in first.index else None
        k = kd.loc[n] if n in kd.index else None
        if isinstance(k, pd.DataFrame):
            k = k.iloc[0]
        rows = g[g['t'] == i]
        s = rows[sig.loc[rows.index]]
        chrom = (f['targeting_chr'] if f is not None else k.get('targeting_chr', k.get('intended_target_chr')))
        start = f['targeting_start'] if f is not None else k.get('targeting_start', k.get('intended_target_start'))
        end = f['targeting_end'] if f is not None else k.get('targeting_end', k.get('intended_target_end'))
        guide_col = 'guide_id(s)'
        guides = (f[guide_col] if f is not None else k[guide_col]) if (f is not None or k is not None) else ''
        targets.append(dict(
            name=n, geneId=str(f['Intended_target_gene_id']) if f is not None else str(k.get('Intended_target', '')),
            chrom=str(chrom), start=int(start), end=int(end), guides=str(guides).strip('"').count(',') + 1,
            knockdown=None if k is None else dict(log2fc=round(float(k['sceptre_log2_fc']), 3),
                                                  nlog10padj=round(float(nlog10([k['sceptre_adj_p_value']])[0]), 1),
                                                  significant=bool(k['sceptre_adj_p_value'] < FDR)),
            tested=int(len(rows)), up=int((s['sceptre_log2_fc'] > 0).sum()), down=int((s['sceptre_log2_fc'] < 0).sum()),
        ))

    def blocks(order_key, other_key, n_groups, name):
        """Rows grouped by order_key (strongest p first), each record naming the other side."""
        rows = g.sort_values([order_key, 'lp'], ascending=[True, False])
        recs = np.zeros(len(rows), dtype=[('i', '<u4'), ('lfc', '<f4'), ('lp', '<f4'), ('lq', '<f4')])
        recs['i'] = rows[other_key].to_numpy().astype(np.uint32)
        recs['lfc'] = rows['sceptre_log2_fc'].to_numpy().astype(np.float32)
        recs['lp'], recs['lq'] = rows['lp'].to_numpy(), rows['lq'].to_numpy()
        counts = np.bincount(rows[order_key].to_numpy(), minlength=n_groups)
        offsets = np.concatenate([[0], np.cumsum(counts)[:-1]]) * 16
        recs.tofile(out / f'{name}.bin')
        np.column_stack([offsets, counts]).astype(np.float64).tofile(out / f'{name}.idx')
        return len(recs)

    out.mkdir(parents=True, exist_ok=True)
    n = blocks('t', 'gi', len(names), 'de')
    blocks('gi', 't', len(genes), 'bygene')
    write_json(out / 'targets.json', dict(genes=genes, targets=targets, threshold=f'FDR < {FDR}'))
    print(f'  {len(names)} targets, {len(genes)} readout genes, {n:,} tested pairs, {int(sig.sum()):,} at FDR < {FDR}')
    return dict(targets=len(names), genes=len(genes), tested=n, significant=int(sig.sum()))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--source', type=Path, required=True, help='folder with the IGVF <file accession>.tsv.gz files')
    ap.add_argument('--out', type=Path, required=True, help='view folder (= Drive ATHENA/crispr/view)')
    ap.add_argument('--locus', type=Path, help='locus view folder (= Drive ATHENA/locus/view), for the enhancer arcs')
    ap.add_argument('--only', help='screen id')
    a = ap.parse_args()

    tss = {}
    if a.locus and (a.locus / 'genes.hg38.json').exists():
        for sym, (chrom, start, end, strand) in json.loads((a.locus / 'genes.hg38.json').read_text()).items():
            tss[sym] = (chrom, int(start) - 1 if strand == '+' else int(end) - 1)
    for cfg in SCREENS:
        if a.only and cfg['id'] != a.only:
            continue
        print(f"== {cfg['id']}")
        out = a.out / cfg['id']
        sets = [s for s, _, _ in cfg['sets']] if cfg['kind'] == 'enhancer' else [cfg['set']]
        stats = enhancer(cfg, a.source, out, a.locus, tss) if cfg['kind'] == 'enhancer' else perturbseq(cfg, a.source, out)
        meta = {k: v for k, v in cfg.items() if k in ('title', 'description', 'kind', 'species', 'assembly', 'cell', 'citation')}
        write_json(out / 'project.json', dict(**meta, sources=source_links(sets), stats=stats, slides=[],
                                               method='SCEPTRE (calibrated CRISPR screen tests)'))


if __name__ == '__main__':
    main()
