#!/usr/bin/env python3
"""Download GTEx v8 cis-eQTL summary statistics (every tested variant-gene pair, hg38) from the eQTL Catalogue.

LocusCompare (server.py, /api/locuscompare) reads one region of a tissue with tabix. Local copies are fast and
spare the EBI servers (they ask for downloads rather than many remote tabix queries); tissues not downloaded are
read remotely. Files are ~3.5 GB each (~170 GB for all 49 tissues): pick tissues with --tissues.

    python3 tools/fetch_gtex_eqtl.py --out data/eQTL/GTEx_v8 --tissues artery_coronary artery_aorta artery_tibial
    python3 tools/fetch_gtex_eqtl.py --out data/eQTL/GTEx_v8 --all

Downloads resume (curl -C -), are written as .part and renamed once the whole file passes gzip -t (a corrupt one
is downloaded again from the start), then checked with tabix.
Standard library only (curl, gzip and tabix are external programs).
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


def fetch(url, target: Path, attempts=20, fresh_starts=3):
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
        if part.is_file() and (not size or part.stat().st_size == size) and (url.endswith('.tbi') or intact(part)):
            part.rename(target)
            return
        print(f'{target.name}: incomplete or corrupt, downloading it again', flush=True)
        part.unlink(missing_ok=True)
    raise RuntimeError(f'Could not download {url}')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--out', type=Path, required=True, help='folder for the files (and manifest.json)')
    ap.add_argument('--tissues', nargs='*', default=[], help='tissue keys, e.g. artery_coronary (see --list)')
    ap.add_argument('--all', action='store_true', help='every tissue, cardiovascular ones first')
    ap.add_argument('--jobs', type=int, default=4, help='files downloaded at once (default 4; EBI serves each slowly)')
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
    a.out.mkdir(parents=True, exist_ok=True)

    def one(key):
        qtd, _, label, n = by_key[key]
        url = BASE.format(id=qtd)
        data = a.out / f'{qtd}.all.tsv.gz'
        index = a.out / f'{qtd}.all.tsv.gz.tbi'
        if not index.is_file():
            fetch(url + '.tbi', index)
        if not data.is_file():
            print(f'{label}: downloading {url}', flush=True)
            fetch(url, data)
        check = subprocess.run(['tabix', str(data), '1:1000000-1001000'], capture_output=True, text=True)
        if check.returncode != 0:
            raise RuntimeError(f'{data} does not read with tabix: {check.stderr.strip()}')
        print(f'{label}: ready', flush=True)
        write_manifest(a.out)

    with concurrent.futures.ThreadPoolExecutor(max(1, a.jobs)) as pool:
        for f in [pool.submit(one, k) for k in keys]:
            f.result()


MANIFEST_LOCK = threading.Lock()


def write_manifest(out: Path):
    manifest = {'source': 'eQTL Catalogue release 7, GTEx v8 (QTS000015), gene expression, all variant-gene pairs',
                'build': 'hg38', 'tissues': [dict(id=q, key=k, label=l, samples=n, file=f'{q}.all.tsv.gz')
                                             for q, k, l, n in GTEX if (out / f'{q}.all.tsv.gz').is_file()]}
    with MANIFEST_LOCK:
        tmp = out / 'manifest.json.part'
        tmp.write_text(json.dumps(manifest, indent=1) + '\n')
        tmp.replace(out / 'manifest.json')


if __name__ == '__main__':
    main()
