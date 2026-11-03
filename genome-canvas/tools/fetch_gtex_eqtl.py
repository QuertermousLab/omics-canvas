#!/usr/bin/env python3
"""Download GTEx cis-eQTL summary statistics (every tested variant-gene pair, hg38) from the eQTL Catalogue.

LocusCompare (server.py, /api/locuscompare) reads one region of a tissue with tabix. Local copies are fast and
spare the EBI servers (they ask for downloads rather than many remote tabix queries); tissues not downloaded are
read remotely. Files are ~3.5 GB each (~170 GB for all 49 tissues): pick tissues with --tissues.

    python3 tools/fetch_gtex_eqtl.py --out data/eQTL/GTEx_v8 --tissues artery_coronary artery_aorta artery_tibial
    python3 tools/fetch_gtex_eqtl.py --out data/eQTL/GTEx_v8 --all
    hub/venv/bin/python genome-canvas/tools/fetch_gtex_eqtl.py --release v10 --out data/eQTL/GTEx_v10 --tissues artery_coronary

--release v8 (default): GTEx v8, eQTL Catalogue release 7, tabix files that are also read remotely.
--release v10: GTEx v10, eQTL Catalogue release 8 beta, published as parquet only: each tissue's parquet is kept and
converted to the v8 layout (same columns, sorted by position, BGZF + tabix) as <dataset>.all.tsv.gz; manifest.json
lists the converted tissues (LocusCompare reads such a folder as a local source). Needs pyarrow (the hub's Python).

Downloads resume (curl -C -), are written as .part and renamed once the whole file passes gzip -t (a corrupt one
is downloaded again from the start), then checked with tabix.
Standard library only for v8 (curl, gzip, bgzip and tabix are external programs).
"""

import argparse
import concurrent.futures
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

BASE = 'http://ftp.ebi.ac.uk/pub/databases/spot/eQTL/sumstats/QTS000015/{id}/{id}.all.tsv.gz'
BASE_V10 = 'http://ftp.ebi.ac.uk/pub/databases/spot/eQTL/r8_beta/sumstats/QTS000015/{id}/{id}.all.parquet'

# eQTL Catalogue release 7, study QTS000015 (GTEx v8), gene expression: (dataset id, tissue, label, samples)
GTEX = [
    ('QTD000116', 'adipose_subcutaneous', 'Adipose - Subcutaneous', 581),
    ('QTD000121', 'adipose_visceral', 'Adipose - Visceral (Omentum)', 469),
    ('QTD000126', 'adrenal_gland', 'Adrenal Gland', 233),
    ('QTD000131', 'artery_aorta', 'Artery - Aorta', 387),
    ('QTD000136', 'artery_coronary', 'Artery - Coronary', 213),
    ('QTD000141', 'artery_tibial', 'Artery - Tibial', 584),
    ('QTD000146', 'brain_amygdala', 'Brain - Amygdala', 129),
    ('QTD000151', 'brain_anterior_cingulate_cortex', 'Brain - Anterior cingulate cortex (BA24)', 147),
    ('QTD000156', 'brain_caudate', 'Brain - Caudate (basal ganglia)', 194),
    ('QTD000161', 'brain_cerebellar_hemisphere', 'Brain - Cerebellar Hemisphere', 175),
    ('QTD000166', 'brain_cerebellum', 'Brain - Cerebellum', 209),
    ('QTD000171', 'brain_cortex', 'Brain - Cortex', 205),
    ('QTD000176', 'brain_frontal_cortex', 'Brain - Frontal Cortex (BA9)', 175),
    ('QTD000181', 'brain_hippocampus', 'Brain - Hippocampus', 165),
    ('QTD000186', 'brain_hypothalamus', 'Brain - Hypothalamus', 170),
    ('QTD000191', 'brain_nucleus_accumbens', 'Brain - Nucleus accumbens (basal ganglia)', 202),
    ('QTD000196', 'brain_putamen', 'Brain - Putamen (basal ganglia)', 170),
    ('QTD000201', 'brain_spinal_cord', 'Brain - Spinal cord (cervical c-1)', 126),
    ('QTD000206', 'brain_substantia_nigra', 'Brain - Substantia nigra', 114),
    ('QTD000211', 'breast', 'Breast - Mammary Tissue', 394),
    ('QTD000216', 'fibroblast', 'Cells - Cultured fibroblasts', 483),
    ('QTD000221', 'LCL', 'Cells - EBV-transformed lymphocytes', 147),
    ('QTD000226', 'colon_sigmoid', 'Colon - Sigmoid', 318),
    ('QTD000231', 'colon_transverse', 'Colon - Transverse', 368),
    ('QTD000236', 'esophagus_gej', 'Esophagus - Gastroesophageal Junction', 330),
    ('QTD000241', 'esophagus_mucosa', 'Esophagus - Mucosa', 493),
    ('QTD000246', 'esophagus_muscularis', 'Esophagus - Muscularis', 461),
    ('QTD000251', 'heart_atrial_appendage', 'Heart - Atrial Appendage', 372),
    ('QTD000256', 'heart_left_ventricle', 'Heart - Left Ventricle', 382),
    ('QTD000261', 'kidney_cortex', 'Kidney - Cortex', 73),
    ('QTD000266', 'liver', 'Liver', 208),
    ('QTD000271', 'lung', 'Lung', 510),
    ('QTD000276', 'minor_salivary_gland', 'Minor Salivary Gland', 144),
    ('QTD000281', 'muscle', 'Muscle - Skeletal', 702),
    ('QTD000286', 'nerve_tibial', 'Nerve - Tibial', 532),
    ('QTD000291', 'ovary', 'Ovary', 167),
    ('QTD000296', 'pancreas', 'Pancreas', 305),
    ('QTD000301', 'pituitary', 'Pituitary', 237),
    ('QTD000306', 'prostate', 'Prostate', 218),
    ('QTD000311', 'skin_not_sun_exposed', 'Skin - Not Sun Exposed (Suprapubic)', 517),
    ('QTD000316', 'skin_sun_exposed', 'Skin - Sun Exposed (Lower leg)', 602),
    ('QTD000321', 'small_intestine', 'Small Intestine - Terminal Ileum', 174),
    ('QTD000326', 'spleen', 'Spleen', 227),
    ('QTD000331', 'stomach', 'Stomach', 324),
    ('QTD000336', 'testis', 'Testis', 322),
    ('QTD000341', 'thyroid', 'Thyroid', 574),
    ('QTD000346', 'uterus', 'Uterus', 129),
    ('QTD000351', 'vagina', 'Vagina', 141),
    ('QTD000356', 'blood', 'Whole Blood', 670),
]
# release 8 keeps the dataset ids for GTEx v10 (data_tables/dataset_metadata_r8.tsv): samples per tissue
SAMPLES_V10 = {
    'QTD000116': 711, 'QTD000121': 584, 'QTD000126': 295, 'QTD000131': 472, 'QTD000136': 268, 'QTD000141': 689,
    'QTD000146': 180, 'QTD000151': 233, 'QTD000156': 298, 'QTD000161': 276, 'QTD000166': 263, 'QTD000171': 268,
    'QTD000176': 268, 'QTD000181': 254, 'QTD000186': 256, 'QTD000191': 284, 'QTD000196': 253, 'QTD000201': 203,
    'QTD000206': 183, 'QTD000211': 511, 'QTD000216': 649, 'QTD000221': 326, 'QTD000226': 418, 'QTD000231': 477,
    'QTD000236': 403, 'QTD000241': 614, 'QTD000246': 561, 'QTD000251': 459, 'QTD000256': 449, 'QTD000261': 103,
    'QTD000266': 261, 'QTD000271': 601, 'QTD000276': 180, 'QTD000281': 816, 'QTD000286': 667, 'QTD000291': 191,
    'QTD000296': 362, 'QTD000301': 311, 'QTD000306': 281, 'QTD000311': 649, 'QTD000316': 751, 'QTD000321': 206,
    'QTD000326': 277, 'QTD000331': 406, 'QTD000336': 413, 'QTD000341': 681, 'QTD000346': 152, 'QTD000351': 170,
    'QTD000356': 800,
}
RELEASES = {
    'v8': dict(base=BASE, source='eQTL Catalogue release 7, GTEx v8 (QTS000015), gene expression, all variant-gene pairs'),
    'v10': dict(base=BASE_V10, source='eQTL Catalogue release 8 beta, GTEx v10 (QTS000015), gene expression, '
                                      'all variant-gene pairs (converted from parquet)'),
}
COLUMNS = ['molecular_trait_id', 'chromosome', 'position', 'ref', 'alt', 'variant', 'ma_samples', 'maf', 'pvalue',
           'beta', 'se', 'type', 'ac', 'an', 'r2', 'molecular_trait_object_id', 'gene_id', 'median_tpm', 'rsid']

# downloaded first with --all: the tissues of cardiovascular studies
FIRST = ['artery_coronary', 'artery_aorta', 'artery_tibial', 'heart_left_ventricle', 'heart_atrial_appendage',
         'liver', 'adipose_subcutaneous', 'adipose_visceral', 'blood', 'fibroblast']


def remote_size(url):
    head = subprocess.run(['curl', '-fsSIL', url], capture_output=True, text=True)
    sizes = [line.split(':', 1)[1].strip() for line in head.stdout.splitlines()
             if line.lower().startswith('content-length:')]
    return int(sizes[-1]) if sizes and sizes[-1].isdigit() else None


def intact(path: Path):
    """The whole file decompresses (a resumed download can splice in bad bytes that tabix alone misses)."""
    return subprocess.run(['gzip', '-t', str(path)], capture_output=True).returncode == 0


def fetch(url, target: Path, attempts=20, fresh_starts=3, check=intact):
    part = target.with_name(target.name + '.part')
    size = remote_size(url)
    for start in range(fresh_starts):
        for attempt in range(attempts):
            if size and part.is_file() and part.stat().st_size >= size:
                break  # complete: the server answers a resume past the end with an error
            result = subprocess.run(['curl', '-fsSL', '--retry', '5', '--retry-delay', '20', '-C', '-',
                                     '-o', str(part), url])
            if result.returncode in (0, 33):  # 33: range not satisfiable, the part is already complete
                break
            time.sleep(min(300, 30 * (attempt + 1)))
        if part.is_file() and (not size or part.stat().st_size == size) and (check is None or check(part)):
            part.rename(target)
            return
        print(f'{target.name}: incomplete or corrupt, downloading it again', flush=True)
        part.unlink(missing_ok=True)
    raise RuntimeError(f'Could not download {url}')


def parquet_to_tabix(parquet: Path, out: Path):
    """A release 8 parquet (chromosomes in blocks, gene by gene within each) as a v8-style tabix file."""
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.csv as pcsv
    import pyarrow.parquet as pq
    f = pq.ParquetFile(parquet)
    names = f.schema_arrow.names
    missing = [c for c in COLUMNS if c not in names]
    if missing:
        raise RuntimeError(f'{parquet.name}: no {", ".join(missing)}')
    # [chromosome, [row groups holding it]] in file order: tabix needs each chromosome in one block, and a row
    # group may hold the end of one chromosome and the start of the next
    blocks, rows = [], 0
    for i in range(f.num_row_groups):
        for chrom in pc.unique(f.read_row_group(i, columns=['chromosome']).column(0)).to_pylist():
            if blocks and blocks[-1][0] == chrom:
                blocks[-1][1].append(i)
            elif any(b[0] == chrom for b in blocks):
                raise RuntimeError(f'{parquet.name}: chromosome {chrom} is not in one block')
            else:
                blocks.append([chrom, [i]])
    part = out.with_name(out.name + '.part')
    with open(part, 'wb') as raw:
        z = subprocess.Popen(['bgzip', '-c'], stdin=subprocess.PIPE, stdout=raw)
        z.stdin.write(('\t'.join(COLUMNS) + '\n').encode())
        for chrom, groups in blocks:
            t = f.read_row_groups(groups, columns=COLUMNS)
            t = t.filter(pc.equal(t.column('chromosome'), chrom)).sort_by([('position', 'ascending')])
            t = pa.table([pc.fill_null(pc.cast(t.column(c), pa.string()), 'NA') for c in COLUMNS], names=COLUMNS)
            sink = pa.BufferOutputStream()
            pcsv.write_csv(t, sink, pcsv.WriteOptions(include_header=False, delimiter='\t', quoting_style='none'))
            z.stdin.write(sink.getvalue().to_pybytes())
            rows += t.num_rows
            del t, sink
        z.stdin.close()
        if z.wait():
            raise RuntimeError('bgzip failed')
    if rows != f.metadata.num_rows:
        raise RuntimeError(f'{parquet.name}: wrote {rows} of {f.metadata.num_rows} rows')
    subprocess.run(['tabix', '-f', '-S', '1', '-s', '2', '-b', '3', '-e', '3', str(part)], check=True)
    Path(str(part) + '.tbi').replace(str(out) + '.tbi')
    part.replace(out)
    return rows


def readable_parquet(path: Path):
    try:
        import pyarrow.parquet as pq
        pq.ParquetFile(path)  # the footer (written last) is intact; the conversion reads every page
        return True
    except Exception:
        return False


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--out', type=Path, required=True, help='folder for the files (and manifest.json)')
    ap.add_argument('--tissues', nargs='*', default=[], help='tissue keys, e.g. artery_coronary (see --list)')
    ap.add_argument('--all', action='store_true', help='every tissue, cardiovascular ones first')
    ap.add_argument('--jobs', type=int, default=4, help='files downloaded at once (default 4; EBI serves each slowly)')
    ap.add_argument('--release', choices=sorted(RELEASES), default='v8', help='GTEx release (default v8)')
    ap.add_argument('--list', action='store_true', help='list the tissues and exit')
    a = ap.parse_args()
    if a.list:
        for qtd, key, label, n in GTEX:
            print(f'{key:34s} {qtd}  n={n:<4d} {label}')
        return
    by_key = {t[1]: t for t in GTEX}
    keys = (FIRST + [t[1] for t in GTEX if t[1] not in FIRST]) if a.all else a.tissues
    unknown = [k for k in keys if k not in by_key]
    if unknown or not keys:
        sys.exit(f'Unknown tissues: {", ".join(unknown)} (see --list)' if unknown else 'Give --tissues or --all')
    if a.release == 'v10':
        try:
            import pyarrow  # noqa: F401
        except ImportError:
            sys.exit("--release v10 needs pyarrow: run it with the hub's Python (hub/venv/bin/python)")
    a.out.mkdir(parents=True, exist_ok=True)

    def one(key):
        qtd, _, label, n = by_key[key]
        url = RELEASES[a.release]['base'].format(id=qtd)
        data = a.out / f'{qtd}.all.tsv.gz'
        index = a.out / f'{qtd}.all.tsv.gz.tbi'
        if a.release == 'v10':
            if not (data.is_file() and index.is_file()):
                parquet = a.out / f'{qtd}.all.parquet'
                if not parquet.is_file():
                    print(f'{label}: downloading {url}', flush=True)
                    fetch(url, parquet, check=readable_parquet)
                print(f'{label}: converting {parquet.name}', flush=True)
                print(f'{label}: {parquet_to_tabix(parquet, data):,} pairs', flush=True)
        else:
            if not index.is_file():
                fetch(url + '.tbi', index, check=None)
            if not data.is_file():
                print(f'{label}: downloading {url}', flush=True)
                fetch(url, data)
        check = subprocess.run(['tabix', str(data), '1:1000000-1001000'], capture_output=True, text=True)
        if check.returncode != 0:
            raise RuntimeError(f'{data} does not read with tabix: {check.stderr.strip()}')
        print(f'{label}: ready', flush=True)
        write_manifest(a.out, a.release)

    with concurrent.futures.ThreadPoolExecutor(max(1, a.jobs)) as pool:
        for f in [pool.submit(one, k) for k in keys]:
            f.result()


MANIFEST_LOCK = threading.Lock()


def write_manifest(out: Path, release='v8'):
    samples = (lambda q, n: SAMPLES_V10.get(q)) if release == 'v10' else (lambda q, n: n)
    manifest = {'source': RELEASES[release]['source'], 'release': release,
                'build': 'hg38', 'tissues': [dict(id=q, key=k, label=l, samples=samples(q, n), file=f'{q}.all.tsv.gz')
                                             for q, k, l, n in GTEX if (out / f'{q}.all.tsv.gz').is_file()]}
    with MANIFEST_LOCK:
        tmp = out / 'manifest.json.part'
        tmp.write_text(json.dumps(manifest, indent=1) + '\n')
        tmp.replace(out / 'manifest.json')


if __name__ == '__main__':
    main()
