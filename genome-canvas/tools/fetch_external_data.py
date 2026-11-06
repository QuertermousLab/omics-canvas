#!/usr/bin/env python3
"""Download the public data Genome Canvas's LocusCompare, LD and liftover use, and add it to genomecanvas.config.json.
(GREAT's data: ../great/tools/fetch_great_data.py.)

    cd genome-canvas
    python3 tools/fetch_external_data.py --configure                     # everything, into data/
    python3 tools/fetch_external_data.py genes chains gwas --configure   # some steps
    python3 tools/fetch_external_data.py --gtex-dir /big/disk/GTEx gtex-v8 gtex-v10 --tissues artery_coronary

Steps (each skips what is already there, so a run can be repeated or resumed):

  genes     GENCODE v39 basic genes (the annotation of the eQTL Catalogue's GTEx) -> data/eQTL/genes.gencode_v39.hg38.tsv
  chains    UCSC liftOver chains hg19<->hg38, mm10<->mm39 -> data/liftover/chains/
  gwas      CAD GWAS, Aragam et al. 2022 (GWAS Catalog GCST90132314, harmonised, GRCh38) as a GWAS track
            -> data/GWAS/CAD_Aragam2022.hg38.gwas.gz (via gwas_catalog_to_track.py; ~1.3 GB download)
  ld        1000 Genomes phase 3 (GRCh37) VCFs + PLINK files and the sample panel -> data/1000G/ (~15 GB; plink2)
  gtex-v8   GTEx v8 all pairs from the eQTL Catalogue -> <gtex-dir>/GTEx_v8_eQTL_all_pairs/ (~3.5 GB per tissue)
  gtex-v10  GTEx v10 all pairs, eQTL Catalogue release 8 beta (parquet, converted) -> <gtex-dir>/GTEx_v10_eQTL_all_pairs/
  gtex-v11  GTEx v11 significant pairs from the GTEx portal (3.3 GB tar) -> <gtex-dir>/GTEx_Analysis_v11_eQTL/,
            converted to <gtex-dir>/GTEx_v11_signif/

The GTEx steps take the three arteries unless --tissues or --all-tissues is given (v11 converts every tissue in its tar;
--tissues limits the ones offered). gtex-v10 and gtex-v11 need pyarrow: run with the hub's Python
(../hub/venv/bin/python). --configure adds the entries that are missing from genomecanvas.config.json
(ldReferenceHg19, locusCompare.genes, locusCompare.eqtl sources) and keeps everything already set; restart Genome
Canvas afterwards. Needs curl, bgzip and tabix (and plink2 for ld).
"""

import argparse
import gzip
import json
import re
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
APP = TOOLS.parent
sys.path.insert(0, str(TOOLS))

GENCODE = 'https://ftp.ebi.ac.uk/pub/databases/gencode/Gencode_human/release_39/gencode.v39.basic.annotation.gtf.gz'
UCSC_CHAINS = {
    'hg19ToHg38.over.chain.gz': 'https://hgdownload.soe.ucsc.edu/goldenPath/hg19/liftOver/hg19ToHg38.over.chain.gz',
    'hg38ToHg19.over.chain.gz': 'https://hgdownload.soe.ucsc.edu/goldenPath/hg38/liftOver/hg38ToHg19.over.chain.gz',
    'mm10ToMm39.over.chain.gz': 'https://hgdownload.soe.ucsc.edu/goldenPath/mm10/liftOver/mm10ToMm39.over.chain.gz',
    'mm39ToMm10.over.chain.gz': 'https://hgdownload.soe.ucsc.edu/goldenPath/mm39/liftOver/mm39ToMm10.over.chain.gz',
}
ARAGAM = ('https://ftp.ebi.ac.uk/pub/databases/gwas/summary_statistics/GCST90132001-GCST90133000/GCST90132314/'
          'harmonised/GCST90132314.h.tsv.gz')
KG = 'http://ftp.1000genomes.ebi.ac.uk/vol1/ftp/release/20130502/'
KG_VCF = 'ALL.chr{chrom}.phase3_shapeit2_mvncall_integrated_v5b.20130502.genotypes.vcf.gz'
KG_PANEL = 'integrated_call_samples_v3.20130502.ALL.panel'
GTEX_V11 = 'https://storage.googleapis.com/adult-gtex/bulk-qtl/v11/single-tissue-cis-qtl/GTEx_Analysis_v11_eQTL.tar'
ARTERIES = ['artery_coronary', 'artery_aorta', 'artery_tibial']
STEPS = ['genes', 'chains', 'gwas', 'ld', 'gtex-v8', 'gtex-v10', 'gtex-v11']


def log(message):
    print(message, flush=True)


def download(url, target: Path):
    """curl into target.part (resumed on a rerun), renamed when complete."""
    if target.is_file():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + '.part')
    log(f'downloading {url}')
    quiet = [] if sys.stderr.isatty() else ['-s']  # no progress meter in logs
    result = subprocess.run(['curl', '-fSL', *quiet, '--retry', '5', '--retry-delay', '20', '-C', '-', '-o', str(part), url])
    if result.returncode not in (0, 33):  # 33: the part is already complete
        sys.exit(f'Could not download {url} (curl exit {result.returncode}); run again to resume')
    part.replace(target)
    return target


def step_genes(a):
    out = a.data / 'eQTL' / 'genes.gencode_v39.hg38.tsv'
    if out.is_file():
        return log(f'genes: {out} exists')
    gtf = download(GENCODE, a.data / 'eQTL' / 'downloads' / Path(GENCODE).name)
    part, n = out.with_name(out.name + '.part'), 0
    with gzip.open(gtf, 'rt') as src, open(part, 'w') as dst:
        dst.write('gene_id\tsymbol\tchrom\tstart\tend\tstrand\ttype\n')
        for line in src:
            f = line.rstrip('\n').split('\t')
            if len(f) < 9 or f[2] != 'gene':
                continue
            attr = lambda key: re.search(key + r' "([^"]+)"', f[8]).group(1)
            dst.write('\t'.join([attr('gene_id').split('.')[0], attr('gene_name'), f[0], f[3], f[4], f[6],
                                 attr('gene_type')]) + '\n')
            n += 1
    part.replace(out)
    log(f'genes: {n:,} genes -> {out}')


def step_chains(a):
    for name, url in UCSC_CHAINS.items():
        download(url, a.data / 'liftover' / 'chains' / name)
    log(f'chains: {a.data / "liftover" / "chains"}')


def step_gwas(a):
    out = a.data / 'GWAS' / 'CAD_Aragam2022.hg38.gwas.gz'
    if out.is_file():
        return log(f'gwas: {out} exists')
    source = download(ARAGAM, a.data / 'GWAS' / 'downloads' / Path(ARAGAM).name)
    subprocess.run([sys.executable, str(TOOLS / 'gwas_catalog_to_track.py'), str(source), str(out)], check=True)


def step_ld(a):
    d = a.data / '1000G'
    download(KG + KG_PANEL, d / KG_PANEL)
    for chrom in a.chromosomes:
        vcf = download(KG + KG_VCF.format(chrom=chrom), d / KG_VCF.format(chrom=chrom))
        download(KG + KG_VCF.format(chrom=chrom) + '.tbi', Path(str(vcf) + '.tbi'))
        prefix = d / KG_VCF.format(chrom=chrom)[:-len('.vcf.gz')]
        if not Path(str(prefix) + '.bed').is_file():
            log(f'ld: chr{chrom} PLINK files')
            # the variant ids stay the VCF's (rsids): the server finds the lead in the VCF and asks PLINK for it
            subprocess.run(['plink2', '--vcf', str(vcf), '--max-alleles', '2', '--rm-dup', 'force-first',
                            '--make-bed', '--out', str(prefix)], check=True, stdout=subprocess.DEVNULL)
    log(f'ld: {d}')


def gtex(a, release, folder):
    keys = ['--all'] if a.all_tissues else ['--tissues', *(a.tissues or ARTERIES)]
    subprocess.run([sys.executable, str(TOOLS / 'fetch_gtex_eqtl.py'), '--release', release, '--out',
                    str(a.gtex_dir / folder), '--jobs', str(a.jobs), *keys], check=True)


def step_gtex_v8(a):
    gtex(a, 'v8', 'GTEx_v8_eQTL_all_pairs')


def step_gtex_v10(a):
    gtex(a, 'v10', 'GTEx_v10_eQTL_all_pairs')


def step_gtex_v11(a):
    folder = a.gtex_dir / 'GTEx_Analysis_v11_eQTL'
    if not any(folder.glob('*.v11.eQTLs.signif_pairs.parquet')):
        tar = download(GTEX_V11, a.gtex_dir / 'downloads' / Path(GTEX_V11).name)
        log(f'gtex-v11: unpacking {tar.name}')
        with tarfile.open(tar) as t:
            for member in t.getmembers():
                name = Path(member.name).name
                if member.isfile() and re.fullmatch(r'[A-Za-z0-9_]+\.v11\.(eGenes\.txt\.gz|eQTLs\.signif_pairs\.parquet)', name):
                    folder.mkdir(parents=True, exist_ok=True)
                    with t.extractfile(member) as src, open(folder / name, 'wb') as dst:
                        shutil.copyfileobj(src, dst, 1 << 20)
        if not any(folder.glob('*.v11.eQTLs.signif_pairs.parquet')):
            sys.exit(f'{tar} holds no *.v11.eQTLs.signif_pairs.parquet')
    subprocess.run([sys.executable, str(TOOLS / 'gtex_parquet_to_eqtl.py'), str(folder), str(a.gtex_dir / 'GTEx_v11_signif')],
                   check=True)


def configure(a, config_path: Path):
    config = json.loads(config_path.read_text()) if config_path.is_file() else {}
    def rel(path):  # inside genome-canvas/: relative, as the example configuration writes them
        try:
            return str(path.relative_to(APP))
        except ValueError:
            return str(path)

    added = []
    kg = a.data / '1000G'
    if 'ldReferenceHg19' not in config and (kg / KG_PANEL).is_file():
        config['ldReferenceHg19'] = {
            'label': '1000 Genomes Phase 3 ALL', 'maxWindow': 2000000,
            'bfileTemplate': rel(kg / KG_VCF[:-len('.vcf.gz')]), 'vcfTemplate': rel(kg / KG_VCF),
            'panel': rel(kg / KG_PANEL)}
        added.append('ldReferenceHg19')
    lc = config.setdefault('locusCompare', {})
    genes = a.data / 'eQTL' / 'genes.gencode_v39.hg38.tsv'
    if 'genes' not in lc and genes.is_file():
        lc['genes'] = rel(genes)
        added.append('locusCompare.genes')
    eqtl = lc.setdefault('eqtl', [])
    have = {s.get('id') for s in eqtl}
    offered = a.tissues or ARTERIES
    squash = lambda name: re.sub(r'[^a-z0-9]', '', name.lower())
    v11 = a.gtex_dir / 'GTEx_v11_signif' / 'manifest.json'
    v11_keys = [t['key'] for t in json.loads(v11.read_text())['tissues']] if v11.is_file() else []
    # v11's tissue keys are GTEx's (Artery_Coronary): the v8 tissues offered, matched by label
    import fetch_gtex_eqtl
    labels = {squash(label) for _, key, label, _ in fetch_gtex_eqtl.GTEX if key in offered}
    v11_offered = [k for k in v11_keys if squash(k) in labels]
    sources = [
        ('gtex_v8', a.gtex_dir / 'GTEx_v8_eQTL_all_pairs', dict(
            label='GTEx v8 · all pairs (eQTL Catalogue)', catalogue='gtex_v8', remote=True, offerDownloaded=True,
            **({} if a.all_tissues else {'tissues': offered}))),
        ('gtex_v10', a.gtex_dir / 'GTEx_v10_eQTL_all_pairs', dict(label='GTEx v10 · all pairs (eQTL Catalogue beta)')),
        ('gtex_v11', a.gtex_dir / 'GTEx_v11_signif', dict(
            label='GTEx v11 · significant pairs', **({} if a.all_tissues else {'tissues': v11_offered}))),
    ]
    for sid, folder, extra in sources:
        if sid not in have and (folder / 'manifest.json').is_file():
            eqtl.append(dict(id=sid, dir=rel(folder), **extra))
            added.append(f'locusCompare.eqtl {sid}')
    if not added:
        return log(f'configure: nothing to add to {config_path}')
    if config_path.is_file():
        shutil.copy2(config_path, config_path.with_name(config_path.name + '.bak'))
    config_path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + '\n')
    log(f'configure: added {", ".join(added)} to {config_path}; restart Genome Canvas')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('steps', nargs='*', metavar='step', help=f'some of {", ".join(STEPS)} (default: all)')
    ap.add_argument('--data', type=Path, default=APP / 'data', help='data folder (default genome-canvas/data)')
    ap.add_argument('--gtex-dir', type=Path, help='folder for the GTEx folders (default <data>/eQTL)')
    ap.add_argument('--tissues', nargs='*', default=[], help='GTEx tissues (default: the three arteries)')
    ap.add_argument('--all-tissues', action='store_true', help='every GTEx tissue (~170-200 GB per release)')
    ap.add_argument('--chromosomes', nargs='*', default=[str(c) for c in range(1, 23)],
                    choices=[str(c) for c in range(1, 23)], help='1000 Genomes autosomes (default 1-22)')
    ap.add_argument('--jobs', type=int, default=3, help='GTEx files downloaded at once')
    ap.add_argument('--configure', action='store_true', help='add what is missing to genomecanvas.config.json')
    ap.add_argument('--config', type=Path, default=APP / 'genomecanvas.config.json')
    a = ap.parse_args()
    a.data = a.data.expanduser().resolve()
    a.gtex_dir = (a.gtex_dir.expanduser() if a.gtex_dir else a.data / 'eQTL').resolve()
    steps = a.steps or STEPS
    unknown = [s for s in steps if s not in STEPS]
    if unknown:
        sys.exit(f'Unknown steps {", ".join(unknown)}: choose from {", ".join(STEPS)}')
    need = ['curl', 'bgzip', 'tabix'] + (['plink2'] if 'ld' in steps else [])
    missing = [p for p in need if not shutil.which(p)]
    if missing:
        sys.exit(f'Needs {", ".join(missing)} on PATH')
    if {'gtex-v10', 'gtex-v11'} & set(steps):
        try:
            import pyarrow  # noqa: F401
        except ImportError:
            sys.exit("gtex-v10 / gtex-v11 need pyarrow: run with the hub's Python (../hub/venv/bin/python)")
    for step in STEPS:
        if step in steps:
            globals()['step_' + step.replace('-', '_')](a)
    if a.configure:
        configure(a, a.config)


if __name__ == '__main__':
    main()
