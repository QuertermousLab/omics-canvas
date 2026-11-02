#!/usr/bin/env python3
"""Turn GWAS Catalog harmonised summary statistics (*.h.tsv.gz, GRCh38) into a Genome Canvas GWAS track.

    python3 tools/gwas_catalog_to_track.py GCST90132314.h.tsv.gz data/CAD_Aragam2022.hg38.gwas.gz

Writes the columns Genome Canvas and LocusCompare read (chromosome, position, rsid, pvalue, neg_log_pvalue, ref,
alt, beta, stderr_beta, alt_allele_freq, hg19_locus), sorted as the input (the harmonised files are),
BGZF-compressed with a Tabix index. `ref` is the harmonised other allele and `alt` the effect allele, so `beta` is
the effect of `alt`. `hg19_locus` (chrom:position on GRCh37, from the study's own `markername`) lets Genome Canvas
compute LD on the track from its GRCh37 1000 Genomes panel. Rows without a position or p-value are dropped. Never overwrites an existing output. Standard library only
(bgzip and tabix are external programs).
"""

import argparse
import gzip
import math
import re
import subprocess
import sys
from pathlib import Path

OUT_COLUMNS = ['chromosome', 'position', 'rsid', 'pvalue', 'neg_log_pvalue', 'ref', 'alt', 'beta', 'stderr_beta',
               'alt_allele_freq', 'hg19_locus']


def neg_log10(p, z=None):
    if p > 0:
        return -math.log10(p)
    return 320.0  # underflow in the source: as large as a double allows


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('source', type=Path)
    ap.add_argument('output', type=Path, help='…/name.hg38.gwas.gz')
    a = ap.parse_args()
    if a.output.exists():
        sys.exit(f'{a.output} exists: remove it first')
    if not a.output.name.endswith('.gwas.gz'):
        sys.exit('The output name ends with .gwas.gz (Genome Canvas reads it as a GWAS track)')
    part = a.output.with_name(a.output.name + '.part')
    kept = dropped = 0
    with gzip.open(a.source, 'rt') as src, open(part, 'wb') as raw:
        bgzip = subprocess.Popen(['bgzip', '-c'], stdin=subprocess.PIPE, stdout=raw)
        header = src.readline().rstrip('\n').split('\t')
        col = {name: i for i, name in enumerate(header)}
        need = ['chromosome', 'base_pair_location', 'p_value', 'effect_allele', 'other_allele']
        missing = [n for n in need if n not in col]
        if missing:
            sys.exit(f'Not a harmonised GWAS Catalog file: no {", ".join(missing)}')
        get = lambda f, name: f[col[name]] if name in col and col[name] < len(f) else ''
        bgzip.stdin.write(('\t'.join(OUT_COLUMNS) + '\n').encode())
        lines = []
        for line in src:
            f = line.rstrip('\n').split('\t')
            try:
                pos = int(get(f, 'base_pair_location'))
                p = float(get(f, 'p_value'))
            except ValueError:
                dropped += 1
                continue
            chrom = get(f, 'chromosome')
            chrom = chrom if chrom.startswith('chr') else 'chr' + chrom
            rsid = get(f, 'rsid')
            rsid = rsid if rsid.startswith('rs') else (get(f, 'variant_id') if get(f, 'variant_id').startswith('rs') else '.')
            na = lambda v: v if v not in ('', 'NA', 'nan') else '.'
            marker = re.match(r'(?:chr)?([0-9XY]+)[:_](\d+)', get(f, 'markername'))
            lines.append('\t'.join([chrom, str(pos), rsid, get(f, 'p_value'), f'{neg_log10(p):.4g}',
                                    get(f, 'other_allele').upper(), get(f, 'effect_allele').upper(),
                                    na(get(f, 'beta')), na(get(f, 'standard_error')), na(get(f, 'effect_allele_frequency')),
                                    f'{marker[1]}:{marker[2]}' if marker else '.']))
            kept += 1
            if len(lines) >= 100000:
                bgzip.stdin.write(('\n'.join(lines) + '\n').encode())
                lines = []
        if lines:
            bgzip.stdin.write(('\n'.join(lines) + '\n').encode())
        bgzip.stdin.close()
        if bgzip.wait():
            sys.exit('bgzip failed')
    subprocess.run(['tabix', '-f', '-S', '1', '-s', '1', '-b', '2', '-e', '2', str(part)], check=True)
    Path(str(part) + '.tbi').rename(str(a.output) + '.tbi')
    part.rename(a.output)
    print(f'{a.output}: {kept:,} variants ({dropped:,} without a position or p-value dropped)')


if __name__ == '__main__':
    main()
