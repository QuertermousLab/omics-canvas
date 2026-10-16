"""CRISPR screen results table -> ATHENA CRISPR explorer files (one screen folder).

Generalizes ATHENA's igvf_to_viewer.py (which is tied to the lab's IGVF releases) to any results table
with one row per (target, readout gene) test: SCEPTRE discovery results (grna_target, response_id,
log_2_fold_change, p_value), IGVF SCEPTRE tables (intended_target_name, gene_symbol, sceptre_*), or
generic DE output (target, gene, log2fc, p_value[, padj]). Column names are matched by
detect.CRISPR_COLUMNS. Missing adjusted p values are computed (Benjamini-Hochberg over tested rows).

  enhancer screens   tests.json  {"columns": [...], "rows": [[chrom, start, end, gene, geneId, log2fc,
                                  -log10 p, -log10 adj p, significant, set, guides, element], ...]}
  Perturb-seq        targets.json, de.bin/.idx, bygene.bin/.idx (16-byte records, see igvf_to_viewer.py)
"""
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

from detect import CRISPR_COLUMNS, find_column, guess_screen_kind

ENSEMBL_ID = re.compile(r'^ENS[A-Z]*G\d{6,}(\.\d+)?$')
SYMBOLS = Path(__file__).resolve().parents[1] / 'resources' / 'gene_symbols.tsv.gz'  # Ensembl gene id -> symbol (GENCODE)


def ensembl_to_symbols(gene: pd.Series):
    """Readout genes given as Ensembl ids (SCEPTRE's response_id) -> (symbol labels, ids without version), or None when
    they are not ids. A symbol two ids share is kept for the first and written "SYMBOL (ENSG…)" for the others;
    ids without a symbol stay ids. Targets are named by symbol, so this is what finds each target's knockdown."""
    text = gene.astype(str)
    if text.str.match(ENSEMBL_ID).mean() < 0.5:
        return None
    ids = text.str.replace(r'\.\d+$', '', regex=True)
    table = pd.read_csv(SYMBOLS, sep='\t', comment='#', header=None, names=['id', 'symbol'], dtype=str).set_index('id')['symbol']
    pairs = pd.DataFrame({'id': ids.unique()})
    pairs['symbol'] = pairs['id'].map(table)
    shared = pairs['symbol'].notna() & pairs.duplicated('symbol', keep='first')
    label = pairs['symbol'].where(pairs['symbol'].notna(), pairs['id'])
    label[shared] = pairs['symbol'][shared] + ' (' + pairs['id'][shared] + ')'
    print(f'  readout genes are Ensembl ids: {int(pairs["symbol"].notna().sum()):,} of {len(pairs):,} named by symbol (GENCODE)', flush=True)
    return ids.map(dict(zip(pairs['id'], label))), ids

LOG10_MAX = 300.0


def nlog10(p):
    p = np.asarray(p, dtype=np.float64)
    with np.errstate(divide='ignore', invalid='ignore'):
        return np.minimum(-np.log10(np.clip(np.nan_to_num(p, nan=1.0), 0, 1)), LOG10_MAX).astype(np.float32)


def bh(p):
    p = np.asarray(p, dtype=np.float64)
    q = np.full_like(p, np.nan)
    ok = np.isfinite(p)
    pv = p[ok]
    if not pv.size:
        return q
    order = np.argsort(pv)
    ranked = pv[order] * len(pv) / np.arange(1, len(pv) + 1)
    ranked = np.minimum.accumulate(ranked[::-1])[::-1]
    out = np.empty_like(pv)
    out[order] = np.minimum(ranked, 1)
    q[ok] = out
    return q


def write_json(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, separators=(',', ':'), ensure_ascii=False, default=lambda o: o.item() if hasattr(o, 'item') else str(o)))


def read_table(path: Path):
    if path.name.endswith('.parquet'):
        return pd.read_parquet(path)
    sep = ',' if '.csv' in path.name else '\t'
    df = pd.read_csv(path, sep=sep, low_memory=False)
    if df.shape[1] == 1 and sep == '\t':
        df = pd.read_csv(path, sep=',', low_memory=False)
    return df


COORD = re.compile(r'^(chr[\w]+)[:_-](\d+)[-_](\d+)')


def element_coords(df, target_col):
    """chrom, start, end per row from targeting_* / intended_target_* columns, or parsed from the name."""
    for c, s, e in (('targeting_chr', 'targeting_start', 'targeting_end'),
                    ('intended_target_chr', 'intended_target_start', 'intended_target_end'),
                    ('chr', 'start', 'end'), ('chrom', 'start', 'end')):
        if c in df.columns and s in df.columns and e in df.columns:
            return df[c].astype(str), pd.to_numeric(df[s], errors='coerce').fillna(0).astype(int), pd.to_numeric(df[e], errors='coerce').fillna(0).astype(int)
    m = df[target_col].astype(str).str.extract(COORD)
    chrom = m[0].fillna('')
    return chrom, pd.to_numeric(m[1], errors='coerce').fillna(0).astype(int), pd.to_numeric(m[2], errors='coerce').fillna(0).astype(int)


def guides_count(df):
    for c in ('guide_id(s)', 'guide_ids', 'grna_id', 'guides'):
        if c in df.columns:
            return df[c].astype(str).str.count(',') + 1
    for c in ('n_guides', 'n_grnas'):
        if c in df.columns:
            return pd.to_numeric(df[c], errors='coerce').fillna(1).astype(int)
    return pd.Series(1, index=df.index)


def convert_file(src: Path, out: Path, a, progress, source_label=None):
    progress(10, f'Reading {Path(source_label or src).name}')
    df = read_table(src)
    progress(25, f'{len(df):,} rows, {df.shape[1]} columns')
    cols = {role: find_column(list(df.columns), role) for role in CRISPR_COLUMNS}
    missing = [r for r in ('target', 'gene') if not cols[r]]
    if missing or not (cols['p'] or cols['padj']):
        raise SystemExit(f'not a CRISPR results table: no column for {", ".join(missing) or "p value"} '
                         f'(columns: {", ".join(map(str, df.columns[:30]))})')
    print(f'  columns: {cols}', flush=True)
    if 'pass_qc' in df.columns:  # SCEPTRE: pairs failing QC were not tested
        df = df[df['pass_qc'].astype(str).str.upper().isin(['TRUE', '1'])]
    if 'type' in df.columns and df['type'].astype(str).str.contains('targeting').any():
        df = df[~df['type'].astype(str).str.contains('non_targeting|safe_targeting|positive_control', regex=True)]
    p = pd.to_numeric(df[cols['p']], errors='coerce') if cols['p'] else None
    padj = pd.to_numeric(df[cols['padj']], errors='coerce') if cols['padj'] else None
    if p is not None:  # SCEPTRE reports untested pairs with p = 1
        df = df[p.notna() & (p < 1)]
        p = p.loc[df.index]
    else:
        df = df[padj.notna()]
    if padj is None:
        padj = pd.Series(bh(p.to_numpy()), index=df.index)
        print('  adjusted p values computed (Benjamini-Hochberg)', flush=True)
    else:
        padj = padj.loc[df.index]
    if p is None:
        p = padj
    lfc = pd.to_numeric(df[cols['log2fc']], errors='coerce').fillna(0) if cols['log2fc'] else pd.Series(0.0, index=df.index)
    gene = df[cols['gene']]
    if 'gene_id' in df.columns and cols['gene'] != 'gene_id':  # a few readouts have no symbol: use the id
        gene = gene.where(gene.notna(), df['gene_id'])
    mapped = ensembl_to_symbols(gene)
    if mapped is not None:
        gene, ids = mapped
        df = df.assign(gene_id=ids)
    df = df.assign(_t=df[cols['target']].astype(object).fillna('NA').astype(str),
                   _g=gene.astype(object).fillna('NA').astype(str), _lfc=lfc.astype(float),
                   _p=p.astype(float), _q=padj.astype(float))
    df = df.assign(_gid=df['gene_id'].astype(str) if 'gene_id' in df.columns else '')
    kind = a.screen_kind if getattr(a, 'screen_kind', 'auto') != 'auto' else guess_screen_kind(df, cols)
    fdr = getattr(a, 'fdr', 0.1)
    print(f'  screen kind: {kind}; significance FDR < {fdr}', flush=True)
    out.mkdir(parents=True, exist_ok=True)
    progress(40, f'Writing {kind} screen')
    stats = enhancer(df, out, fdr) if kind == 'enhancer' else perturbseq(df, out, fdr, progress)
    species = getattr(a, 'species', None) or guess_species_genes(df['_g'])
    meta = dict(title=a.title or Path(source_label or src).name, kind=kind, species=species,
                description='Converted by the THOR visualization hub.',
                method='SCEPTRE' if any(c.startswith('sceptre') for c in df.columns) or 'grna_target' in df.columns else None,
                sources=[dict(accession=Path(source_label or src).name, url=None, path=str(source_label or src))],
                stats=stats, slides=[], fdr=fdr)
    write_json(out / 'project.json', meta)
    progress(95, 'CRISPR screen written')


def guess_species_genes(genes):
    s = [g for g in genes.drop_duplicates().head(3000) if re.match(r'^[A-Za-z][A-Za-z0-9-]+$', g)]
    if not s:
        return None
    return 'Human' if sum(g.isupper() for g in s) / len(s) > 0.6 else 'Mouse'


def enhancer(df, out: Path, fdr):
    chrom, start, end = element_coords(df, '_t')
    sets = df['set'].astype(str) if 'set' in df.columns else (df['grna_group'].astype(str) if 'grna_group' in df.columns else pd.Series('', index=df.index))
    gene_id = df['gene_id'].astype(str) if 'gene_id' in df.columns else df['_g']
    guides = guides_count(df)
    rows = [[c, int(s), int(e), g, gi, round(float(l), 4), round(float(lp), 2), round(float(lq), 2), bool(q < fdr), st, int(gd), t]
            for c, s, e, g, gi, l, lp, lq, q, st, gd, t in zip(chrom, start, end, df['_g'], gene_id, df['_lfc'], nlog10(df['_p']),
                                                              nlog10(df['_q']), df['_q'].fillna(1), sets, guides, df['_t'])]
    rows.sort(key=lambda r: (r[0], r[1], r[11], r[3]))
    cols = ['chrom', 'start', 'end', 'gene', 'geneId', 'log2fc', 'nlog10p', 'nlog10padj', 'significant', 'set', 'guides', 'element']
    write_json(out / 'tests.json', dict(columns=cols, rows=rows))
    n_sig = sum(r[8] for r in rows)
    print(f'  {len(rows)} tests, {n_sig} significant, {len({r[11] for r in rows})} elements', flush=True)
    return dict(tests=len(rows), significant=int(n_sig))


def perturbseq(g, out: Path, fdr, progress):
    genes = sorted(g['_g'].unique())
    gene_ix = {s: i for i, s in enumerate(genes)}
    id_of = dict(zip(g['_g'], g['_gid'])) if '_gid' in g.columns else {}
    gene_ids = [id_of.get(x, '') for x in genes]
    names = sorted(g['_t'].unique())
    g = g.assign(t=g['_t'].map({n: i for i, n in enumerate(names)}).astype(np.int64),
                 gi=g['_g'].map(gene_ix).astype(np.int64), lp=nlog10(g['_p']), lq=nlog10(g['_q']))
    sig = g['_q'] < fdr
    chrom, start, end = element_coords(g, '_t')
    g = g.assign(_chrom=chrom.to_numpy(), _start=start.to_numpy(), _end=end.to_numpy(), _guides=guides_count(g).to_numpy())
    first = g.drop_duplicates('_t').set_index('_t')
    lower_genes = {s.lower(): s for s in genes}
    targets = []
    by_t = g.groupby('t')
    for i, n in enumerate(names):
        rows = by_t.get_group(i)
        s = rows[sig.loc[rows.index]]
        f = first.loc[n]
        # knockdown = the target's own gene among the readouts (TSS CRISPRi)
        own = rows[rows['_g'].str.lower() == n.lower()]
        if own.empty and n.lower() not in lower_genes:
            base = re.split(r'[_|.-]', n)[0]
            own = rows[rows['_g'].str.lower() == base.lower()]
        k = own.iloc[0] if len(own) else None
        targets.append(dict(
            name=n, geneId=str(f.get('Intended_target_gene_id', '')) if 'Intended_target_gene_id' in first.columns
            else (str(k['_gid']) if k is not None and '_gid' in k else ''),
            chrom=str(f['_chrom'] or ''), start=int(f['_start']), end=int(f['_end']), guides=int(f['_guides']),
            knockdown=None if k is None else dict(log2fc=round(float(k['_lfc']), 3), nlog10padj=round(float(nlog10([k['_q']])[0]), 1),
                                                  significant=bool(k['_q'] < fdr)),
            tested=int(len(rows)), up=int((s['_lfc'] > 0).sum()), down=int((s['_lfc'] < 0).sum()),
        ))

    def blocks(order_key, other_key, n_groups, name):
        rows = g.sort_values([order_key, 'lp'], ascending=[True, False])
        recs = np.zeros(len(rows), dtype=[('i', '<u4'), ('lfc', '<f4'), ('lp', '<f4'), ('lq', '<f4')])
        recs['i'] = rows[other_key].to_numpy().astype(np.uint32)
        recs['lfc'] = rows['_lfc'].to_numpy().astype(np.float32)
        recs['lp'], recs['lq'] = rows['lp'].to_numpy(), rows['lq'].to_numpy()
        counts = np.bincount(rows[order_key].to_numpy(), minlength=n_groups)
        offsets = np.concatenate([[0], np.cumsum(counts)[:-1]]) * 16
        recs.tofile(out / f'{name}.bin')
        np.column_stack([offsets, counts]).astype(np.float64).tofile(out / f'{name}.idx')
        return len(recs)

    progress(60, 'Writing per-target results')
    n = blocks('t', 'gi', len(names), 'de')
    progress(80, 'Writing per-gene results')
    blocks('gi', 't', len(genes), 'bygene')
    extra = dict(geneIds=gene_ids) if any(gene_ids) else {}
    write_json(out / 'targets.json', dict(genes=genes, **extra, targets=targets, threshold=f'FDR < {fdr}'))
    print(f'  {len(names)} targets, {len(genes)} readout genes, {n:,} tested pairs, {int(sig.sum()):,} at FDR < {fdr}', flush=True)
    return dict(targets=len(names), genes=len(genes), tested=n, significant=int(sig.sum()))
