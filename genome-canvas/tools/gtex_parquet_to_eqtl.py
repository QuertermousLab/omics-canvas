#!/usr/bin/env python3
"""Turn GTEx eQTL parquet files (v10 / v11 `*.eQTLs.signif_pairs.parquet`) into tabix files LocusCompare reads.

    hub/venv/bin/python genome-canvas/tools/gtex_parquet_to_eqtl.py \\
        ~/General_Use_Data/GTEx_Analysis_v11_eQTL genome-canvas/data/eQTL/GTEx_v11_signif

Each tissue becomes `<Tissue>.tsv.gz` (+ .tbi) with the eQTL Catalogue's column names (gene_id, chromosome,
position, ref, alt, pvalue, beta, se, rsid), sorted by position, and `manifest.json` lists them. GTEx's significant
pairs hold only variant-gene pairs below each gene's threshold, so LocusCompare marks the source "significant
pairs only". rsids come from the eGenes table where GTEx gives one (its lead variant), otherwise ".".
Needs pyarrow (e.g. the hub's virtual environment); bgzip and tabix are external programs.
"""

import argparse
import gzip
import json
import subprocess
import sys
from pathlib import Path

try:
    import pyarrow.parquet as pq
except ImportError:
    sys.exit('Needs pyarrow: run it with the hub\'s Python (hub/venv/bin/python)')

COLUMNS = ['gene_id', 'chromosome', 'position', 'ref', 'alt', 'pvalue', 'beta', 'se', 'rsid']


def tissue_label(name):
    return name.replace('_', ' ').replace(' - ', ' - ')


def convert(parquet: Path, egenes: Path, out: Path):
    rsids = {}
    if egenes.is_file():
        with gzip.open(egenes, 'rt') as f:
            head = f.readline().rstrip('\n').split('\t')
            iv, ir = head.index('variant_id'), next((i for i, h in enumerate(head) if h.startswith('rs_id')), None)
            for line in f:
                p = line.rstrip('\n').split('\t')
                if ir is not None and p[ir].startswith('rs'):
                    rsids[p[iv]] = p[ir]
    t = pq.read_table(parquet, columns=['phenotype_id', 'variant_id', 'pval_nominal', 'slope', 'slope_se'])
    gene = [g.split('.')[0] for g in t.column('phenotype_id').to_pylist()]
    var = t.column('variant_id').to_pylist()
    p, b, se = (t.column(c).to_pylist() for c in ('pval_nominal', 'slope', 'slope_se'))
    rows = []
    for i, v in enumerate(var):
        parts = v.split('_')
        if len(parts) < 4:
            continue
        rows.append((parts[0], int(parts[1]), parts[2], parts[3], gene[i], p[i], b[i], se[i], rsids.get(v, '.')))
    order = {f'chr{c}': k for k, c in enumerate([str(x) for x in range(1, 23)] + ['X', 'Y', 'M'])}
    rows.sort(key=lambda r: (order.get(r[0], 99), r[1]))
    part = out.with_name(out.name + '.part')
    with open(part, 'wb') as raw:
        z = subprocess.Popen(['bgzip', '-c'], stdin=subprocess.PIPE, stdout=raw)
        z.stdin.write(('\t'.join(COLUMNS) + '\n').encode())
        chunk = []
        for c, pos, ref, alt, g, pv, beta, s, rs in rows:
            chunk.append(f'{g}\t{c[3:]}\t{pos}\t{ref}\t{alt}\t{pv:.6g}\t{beta:.6g}\t{s:.6g}\t{rs}')
            if len(chunk) >= 200000:
                z.stdin.write(('\n'.join(chunk) + '\n').encode())
                chunk = []
        if chunk:
            z.stdin.write(('\n'.join(chunk) + '\n').encode())
        z.stdin.close()
        if z.wait():
            raise RuntimeError('bgzip failed')
    subprocess.run(['tabix', '-f', '-S', '1', '-s', '2', '-b', '3', '-e', '3', str(part)], check=True)
    Path(str(part) + '.tbi').replace(str(out) + '.tbi')
    part.replace(out)
    return len(rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('source', type=Path, help='folder of <Tissue>.v11.eQTLs.signif_pairs.parquet (+ eGenes.txt.gz)')
    ap.add_argument('out', type=Path)
    ap.add_argument('--version', default='v11')
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    tissues = []
    for parquet in sorted(a.source.glob(f'*.{a.version}.eQTLs.signif_pairs.parquet')):
        name = parquet.name.split(f'.{a.version}.')[0]
        out = a.out / f'{name}.tsv.gz'
        if not out.is_file() or out.stat().st_mtime < parquet.stat().st_mtime:
            n = convert(parquet, a.source / f'{name}.{a.version}.eGenes.txt.gz', out)
            print(f'{name}: {n:,} pairs', flush=True)
        tissues.append(dict(key=name, label=tissue_label(name), file=out.name))
    manifest = {'source': f'GTEx {a.version} significant variant-gene pairs ({a.source})', 'build': 'hg38',
                'significantOnly': True, 'tissues': tissues}
    (a.out / 'manifest.json').write_text(json.dumps(manifest, indent=1) + '\n')


if __name__ == '__main__':
    main()
