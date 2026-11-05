#!/usr/bin/env python3
"""Download and build the data of Genome Canvas's GREAT service (great.py): genes, regulatory domains and ontologies.

    cd genome-canvas
    python3 tools/fetch_great_data.py                    # hg38 hg19 mm10 mm9 into data/GREAT/
    python3 tools/fetch_great_data.py hg38 --update      # download the ontologies again (new GO/HPO/MGI releases)

GREAT 4.0.4's own files (from the GREAT documentation, great-help.atlassian.net):
  GREATv4.genes.<assembly>.tsv          the gene set: one canonical TSS per gene (Ensembl 90; mm9: Ensembl 67)
  GREATv4.curatedRegDoms.<assembly>.txt the literature-curated regulatory domains (SHH, HOXD, beta-globin)
UCSC: <assembly>.chrom.sizes and the assembly gaps (gap table), the "antigap" genome of the binomial test.
Ontologies, current releases (GREAT 4.0.4 itself used 2019 releases, which are no longer published):
  GO Molecular Function / Biological Process / Cellular Component   go-basic.obo + goa_human.gaf / mgi.gaf
  Mouse Phenotype, Mouse Phenotype Single KO   mp.obo + MGI_PhenoGenoMP.rpt / MGI_GenePheno.rpt (single-gene genotypes)
  Human Phenotype                              hp.obo + HPO genes_to_phenotype.txt
  Ensembl Genes                                one term per gene
Mouse annotations reach human genes (and human phenotypes mouse genes) through MGI's mouse-human homology classes;
human gene symbols are matched through HGNC (current, previous and alias symbols, Ensembl and Entrez ids).

Output, per assembly: data/GREAT/<assembly>/{genes.tsv, curated.tsv, chrom.sizes, gaps.bed, <ontology>.json.gz,
manifest.json}. As in GREAT, annotations propagate to parent terms (is_a; GO also part_of) and a parent term whose
genes are exactly those of one of its children is not tested. Standard library only; needs curl.
"""

import argparse
import gzip
import json
import re
import shutil
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

APP = Path(__file__).resolve().parent.parent
ASSEMBLIES = ['hg38', 'hg19', 'mm10', 'mm9']
HUMAN = {'hg38', 'hg19'}
GREAT_HELP = 'https://great-help.atlassian.net/wiki/download/attachments'
GREAT_GENES = GREAT_HELP + '/655445/GREATv4.genes.{assembly}.tsv'
GREAT_CURATED = GREAT_HELP + '/655443/GREATv4.curatedRegDoms.{assembly}.txt'
UCSC = 'https://hgdownload.soe.ucsc.edu/goldenPath/{assembly}/'
MGI = 'https://www.informatics.jax.org/downloads/reports/'
SOURCES = {
    'go-basic.obo': 'http://purl.obolibrary.org/obo/go/go-basic.obo',
    'goa_human.gaf.gz': 'http://current.geneontology.org/annotations/goa_human.gaf.gz',
    'mgi.gaf.gz': 'http://current.geneontology.org/annotations/mgi.gaf.gz',
    'hp.obo': 'http://purl.obolibrary.org/obo/hp.obo',
    'genes_to_phenotype.txt':
        'https://github.com/obophenotype/human-phenotype-ontology/releases/latest/download/genes_to_phenotype.txt',
    'mp.obo': 'http://purl.obolibrary.org/obo/mp.obo',
    'MGI_PhenoGenoMP.rpt': MGI + 'MGI_PhenoGenoMP.rpt',
    'MGI_GenePheno.rpt': MGI + 'MGI_GenePheno.rpt',
    'HOM_MouseHumanSequence.rpt': MGI + 'HOM_MouseHumanSequence.rpt',
    'MRK_ENSEMBL.rpt': MGI + 'MRK_ENSEMBL.rpt',
    'hgnc_complete_set.txt': 'https://storage.googleapis.com/public-download-files/hgnc/tsv/tsv/hgnc_complete_set.txt',
}
# The ontologies of GREAT 4.0.4, in its order: key (rGREAT's ONTOLOGY_KEYS value), name, category
ONTOLOGIES = [
    ('GOMolecularFunction', 'GO Molecular Function', 'GO'),
    ('GOBiologicalProcess', 'GO Biological Process', 'GO'),
    ('GOCellularComponent', 'GO Cellular Component', 'GO'),
    ('MGIPhenotype', 'Mouse Phenotype', 'Phenotype'),
    ('MGIPhenotypeSingleKO', 'Mouse Phenotype Single KO', 'Phenotype'),
    ('HumanPhenotypeOntology', 'Human Phenotype', 'Phenotype'),
    ('EnsemblGenes', 'Ensembl Genes', 'Genes'),
]
GO_NAMESPACES = {'GOMolecularFunction': 'molecular_function', 'GOBiologicalProcess': 'biological_process',
                 'GOCellularComponent': 'cellular_component'}
MAIN_CHROMOSOME = re.compile(r'chr([0-9]+|X|Y|M)')


def log(message):
    print(message, flush=True)


def download(url, target, fresh=False):
    """curl into target.part, renamed when complete; reused within a run (or with --keep-downloads) unless fresh."""
    if target.is_file() and not fresh:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + '.part')
    if part.exists():
        part.unlink()
    log('downloading ' + url)
    quiet = [] if sys.stderr.isatty() else ['-s']
    result = subprocess.run(['curl', '-fSL', *quiet, '--retry', '5', '--retry-delay', '10', '-o', str(part), url])
    if result.returncode != 0:
        sys.exit('Could not download {} (curl exit {})'.format(url, result.returncode))
    part.replace(target)
    return target


def open_text(path):
    return gzip.open(path, 'rt', encoding='utf-8', errors='replace') if str(path).endswith('.gz') \
        else open(path, encoding='utf-8', errors='replace')


# ---------------------------------------------------------------- ontologies (OBO)

def read_obo(path, relations=('is_a',)):
    """{term: {'name', 'namespace', 'parents'}}, plus alt_id -> primary id; obsolete terms are left out."""
    terms, alternatives, version = {}, {}, ''
    current = None
    with open_text(path) as handle:
        for line in handle:
            line = line.rstrip('\n')
            if line.startswith('data-version:') and not version:
                version = line.split(':', 1)[1].strip()
            if line.startswith('['):
                current = {'parents': set(), 'alt': []} if line == '[Term]' else None
                continue
            if current is None or ':' not in line:
                continue
            tag, value = line.split(':', 1)
            value = value.strip()
            if tag == 'id':
                current['id'] = value
                terms[value] = current
            elif tag == 'name':
                current['name'] = value
            elif tag == 'namespace':
                current['namespace'] = value
            elif tag == 'alt_id':
                current['alt'].append(value)
            elif tag == 'is_obsolete' and value == 'true':
                current['obsolete'] = True
            elif tag == 'is_a':
                current['parents'].add(value.split()[0])
            elif tag == 'relationship' and value.split()[0] in relations:
                current['parents'].add(value.split()[1])
    for term_id, term in list(terms.items()):
        if term.get('obsolete'):
            del terms[term_id]
            continue
        for alt in term['alt']:
            alternatives[alt] = term_id
    for term in terms.values():
        term['parents'] = {p for p in term['parents'] if p in terms
                           and terms[p].get('namespace') == term.get('namespace')}
    return terms, alternatives, version


def propagate(terms, direct):
    """direct: {term: set(genes)} -> every term's genes including its descendants' (true-path rule)."""
    ancestors = {}

    def up(term_id):
        if term_id not in ancestors:
            ancestors[term_id] = set()  # guards cycles
            found = {term_id}
            for parent in terms[term_id]['parents']:
                found |= up(parent)
            ancestors[term_id] = found
        return ancestors[term_id]

    sys.setrecursionlimit(100000)
    full = defaultdict(set)
    for term_id, genes in direct.items():
        if term_id in terms:
            for ancestor in up(term_id):
                full[ancestor] |= genes
    return full


def drop_redundant(terms, full):
    """GREAT does not test a parent term whose genes are identical to one of its children's."""
    redundant = set()
    for term_id, genes in full.items():
        for parent in terms[term_id]['parents']:
            if parent in full and full[parent] == genes:
                redundant.add(parent)
    return {t: g for t, g in full.items() if t not in redundant}


# ---------------------------------------------------------------- gene identifiers

class HumanGenes:
    """Maps HGNC symbols (current, previous, alias), Entrez, UniProt and HGNC ids to GREAT's human genes."""

    def __init__(self, hgnc_path, great_genes):
        self.by_ensembl = {g['id']: i for i, g in enumerate(great_genes)}
        self.by_symbol = defaultdict(set)
        for i, g in enumerate(great_genes):
            self.by_symbol[g['symbol']].add(i)
        self.records = {}
        self.index = {'symbol': defaultdict(set), 'previous': defaultdict(set), 'entrez': defaultdict(set),
                      'uniprot': defaultdict(set)}
        with open_text(hgnc_path) as handle:
            header = handle.readline().rstrip('\n').split('\t')
            col = {name: i for i, name in enumerate(header)}
            for line in handle:
                f = line.rstrip('\n').split('\t')
                field = lambda name: f[col[name]].strip('"') if col.get(name, 99) < len(f) else ''
                hgnc = field('hgnc_id')
                symbols = [field('symbol')] + [s for s in re.split(r'[|,]', field('prev_symbol')) if s] + \
                          [s for s in re.split(r'[|,]', field('alias_symbol')) if s]
                self.records[hgnc] = {'ensembl': field('ensembl_gene_id'), 'symbols': symbols}
                self.index['symbol'][field('symbol')].add(hgnc)
                for s in symbols[1:]:
                    self.index['previous'][s].add(hgnc)
                if field('entrez_id'):
                    self.index['entrez'][field('entrez_id')].add(hgnc)
                for u in re.split(r'[|,]', field('uniprot_ids')):
                    if u:
                        self.index['uniprot'][u].add(hgnc)
        self.cache = {}

    def genes_of_hgnc(self, hgnc):
        """GREAT genes of one HGNC record: by Ensembl id, else by any of its symbols."""
        if hgnc in self.cache:
            return self.cache[hgnc]
        record = self.records.get(hgnc)
        found = set()
        if record:
            if record['ensembl'] in self.by_ensembl:
                found = {self.by_ensembl[record['ensembl']]}
            else:
                for symbol in record['symbols']:
                    if self.by_symbol.get(symbol):
                        found = set(self.by_symbol[symbol])
                        break
        self.cache[hgnc] = found
        return found

    def lookup(self, symbol=None, entrez=None, uniprot=None):
        records = set()
        if entrez:
            records = self.index['entrez'].get(entrez, set())
        if not records and uniprot:
            records = self.index['uniprot'].get(uniprot, set())
        if not records and symbol:
            records = self.index['symbol'].get(symbol) or self.index['previous'].get(symbol, set())
        found = set()
        for hgnc in records:
            found |= self.genes_of_hgnc(hgnc)
        if not found and symbol and symbol in self.by_symbol:
            found = set(self.by_symbol[symbol])
        return found


class MouseGenes:
    """Maps MGI marker ids (and symbols) to GREAT's mouse genes through MGI's Ensembl table."""

    def __init__(self, mrk_ensembl_path, great_genes):
        self.by_ensembl = {g['id']: i for i, g in enumerate(great_genes)}
        self.by_symbol = defaultdict(set)
        for i, g in enumerate(great_genes):
            self.by_symbol[g['symbol']].add(i)
        self.by_mgi = defaultdict(set)
        with open_text(mrk_ensembl_path) as handle:
            for line in handle:
                f = line.rstrip('\n').split('\t')
                if len(f) < 6 or not f[0].startswith('MGI:'):
                    continue
                genes = {self.by_ensembl[e] for e in f[5].split() if e in self.by_ensembl}
                if not genes and f[1] in self.by_symbol:
                    genes = set(self.by_symbol[f[1]])
                self.by_mgi[f[0]] |= genes

    def lookup(self, mgi=None, symbol=None):
        found = self.by_mgi.get(mgi, set()) if mgi else set()
        if not found and symbol:
            found = self.by_symbol.get(symbol, set())
        return found


def homology(path):
    """MGI mouse-human homology classes: [(set of MGI ids, set of HGNC ids, set of human Entrez ids)]."""
    classes = defaultdict(lambda: (set(), set(), set()))
    with open_text(path) as handle:
        header = handle.readline().rstrip('\n').split('\t')
        col = {name: i for i, name in enumerate(header)}
        for line in handle:
            f = line.rstrip('\n').split('\t')
            if len(f) < len(header) - 3:
                continue
            key = f[col['DB Class Key']]
            organism = f[col['NCBI Taxon ID']]
            if organism == '10090' and f[col['Mouse MGI ID']]:
                classes[key][0].add(f[col['Mouse MGI ID']])
            elif organism == '9606':
                if f[col['HGNC ID']]:
                    classes[key][1].add(f[col['HGNC ID']])
                if f[col['EntrezGene ID']]:
                    classes[key][2].add(f[col['EntrezGene ID']])
    return list(classes.values())


# ---------------------------------------------------------------- annotation sources

def read_gaf(path, terms, alternatives, namespace, resolve):
    """{GO term: set(gene index)} for one GO namespace; NOT annotations are skipped."""
    direct = defaultdict(set)
    with open_text(path) as handle:
        for line in handle:
            if line.startswith('!'):
                continue
            f = line.rstrip('\n').split('\t')
            if len(f) < 15 or 'NOT' in f[3].split('|'):
                continue
            term = alternatives.get(f[4], f[4])
            if term not in terms or terms[term].get('namespace') != namespace:
                continue
            for gene in resolve(f):
                direct[term].add(gene)
    return direct


def mgi_phenotypes(path, marker_column, alternatives, single):
    """{MP term: set(MGI id)} from an MGI genotype-phenotype report; single: genotypes of one gene only."""
    direct = defaultdict(set)
    with open_text(path) as handle:
        for line in handle:
            f = line.rstrip('\n').split('\t')
            if len(f) <= marker_column:
                continue
            term = next((x for x in f if x.startswith('MP:')), '')
            markers = [m for m in re.split(r'[|,\s]+', f[marker_column]) if m.startswith('MGI:')]
            if not term or not markers or (single and len(markers) != 1):
                continue
            direct[alternatives.get(term, term)].update(markers)
    return direct


def human_phenotypes(path, alternatives):
    """{HP term: set(Entrez id)} from HPO's genes_to_phenotype.txt."""
    direct = defaultdict(set)
    with open_text(path) as handle:
        header = handle.readline().rstrip('\n').split('\t')
        col = {name: i for i, name in enumerate(header)}
        gene, term = col.get('ncbi_gene_id', 0), col.get('hpo_id', 2)
        for line in handle:
            f = line.rstrip('\n').split('\t')
            if len(f) > max(gene, term) and f[term].startswith('HP:'):
                direct[alternatives.get(f[term], f[term])].add(f[gene])
    return direct


# ---------------------------------------------------------------- per assembly

def read_great_genes(path):
    genes = []
    with open_text(path) as handle:
        for line in handle:
            if line.startswith('#') or not line.strip():
                continue
            f = line.rstrip('\n').split('\t')
            genes.append({'id': f[0], 'chrom': f[1], 'tss': int(f[2]), 'strand': f[3], 'symbol': f[4] if len(f) > 4 else f[0]})
    return genes


def write_json(path, payload):
    part = path.with_name(path.name + '.part')
    opener = gzip.open if path.name.endswith('.gz') else open
    with opener(part, 'wt', encoding='utf-8') as handle:
        json.dump(payload, handle, separators=(',', ':'))
    part.replace(path)


def build_assembly(assembly, data, downloads, sources):
    out = data / assembly
    out.mkdir(parents=True, exist_ok=True)
    raw = downloads / assembly
    genes_file = download(GREAT_GENES.format(assembly=assembly), raw / 'GREATv4.genes.{}.tsv'.format(assembly))
    curated_file = download(GREAT_CURATED.format(assembly=assembly),
                            raw / 'GREATv4.curatedRegDoms.{}.txt'.format(assembly))
    sizes_file = download(UCSC.format(assembly=assembly) + 'bigZips/{}.chrom.sizes'.format(assembly),
                          raw / '{}.chrom.sizes'.format(assembly))
    genes = read_great_genes(genes_file)
    chromosomes = sorted({g['chrom'] for g in genes})
    sizes = {}
    with open(sizes_file) as handle:
        for line in handle:
            f = line.split()
            if len(f) >= 2 and f[0] in chromosomes and MAIN_CHROMOSOME.fullmatch(f[0]):
                sizes[f[0]] = int(f[1])
    gaps = []
    if assembly == 'mm9':  # mm9 keeps one gap table per chromosome
        tables = [download(UCSC.format(assembly=assembly) + 'database/{}_gap.txt.gz'.format(c),
                           raw / '{}_gap.txt.gz'.format(c)) for c in sizes]
    else:
        tables = [download(UCSC.format(assembly=assembly) + 'database/gap.txt.gz', raw / 'gap.txt.gz')]
    for table in tables:
        with open_text(table) as handle:
            for line in handle:
                f = line.split('\t')
                if len(f) > 3 and f[1] in sizes:
                    gaps.append((f[1], int(f[2]), int(f[3])))
    gaps.sort(key=lambda g: (chromosomes.index(g[0]), g[1]))

    with open(out / 'genes.tsv', 'w') as handle:
        handle.write('#id\tchrom\ttss\tstrand\tsymbol\n')
        for g in genes:
            handle.write('{id}\t{chrom}\t{tss}\t{strand}\t{symbol}\n'.format(**g))
    with open(out / 'chrom.sizes', 'w') as handle:
        handle.writelines('{}\t{}\n'.format(c, s) for c, s in sizes.items())
    with open(out / 'gaps.bed', 'w') as handle:
        handle.writelines('{}\t{}\t{}\n'.format(*g) for g in gaps)
    with open(curated_file) as src, open(out / 'curated.tsv', 'w') as dst:
        dst.write('#chrom\tstart\tend\tgene_id\tnote\n')
        note = ''
        for line in src:
            if line.startswith('#'):
                note = line.lstrip('# ').strip()
            elif line.strip():
                f = line.rstrip('\n').split('\t')
                dst.write('\t'.join(f[:4] + [note]) + '\n')

    human = assembly in HUMAN
    if human:
        ids = HumanGenes(sources['hgnc_complete_set.txt'], genes)
        mouse_ids = None
    else:
        ids = MouseGenes(sources['MRK_ENSEMBL.rpt'], genes)
    classes = homology(sources['HOM_MouseHumanSequence.rpt'])
    built = []

    go_terms, go_alt, go_version = read_obo(sources['go-basic.obo'], relations=('part_of',))
    for key, name, category in ONTOLOGIES[:3]:
        if human:
            resolve = lambda f: ids.lookup(symbol=f[2], uniprot=f[1] if f[0] == 'UniProtKB' else None)
            gaf = sources['goa_human.gaf.gz']
        else:
            resolve = lambda f: ids.lookup(mgi=f[1] if f[1].startswith('MGI:') else 'MGI:' + f[1], symbol=f[2])
            gaf = sources['mgi.gaf.gz']
        direct = read_gaf(gaf, go_terms, go_alt, GO_NAMESPACES[key], resolve)
        built.append(save_ontology(out, key, name, category, go_terms, direct, go_version, len(genes)))

    mp_terms, mp_alt, mp_version = read_obo(sources['mp.obo'])
    if human:
        mouse_to_human = defaultdict(set)
        for mgis, hgncs, _ in classes:
            targets = set()
            for hgnc in hgncs:
                targets |= ids.genes_of_hgnc(hgnc)
            for mgi in mgis:
                mouse_to_human[mgi] |= targets
        to_genes = lambda mgi: mouse_to_human.get(mgi, set())
    else:
        to_genes = lambda mgi: ids.lookup(mgi=mgi)
    for (key, name, category), report, column, single in (
            (ONTOLOGIES[3], 'MGI_PhenoGenoMP.rpt', 5, False), (ONTOLOGIES[4], 'MGI_GenePheno.rpt', 6, True)):
        markers = mgi_phenotypes(sources[report], column, mp_alt, single)
        direct = {t: set().union(*[to_genes(m) for m in ms]) for t, ms in markers.items()}
        built.append(save_ontology(out, key, name, category, mp_terms, direct, mp_version, len(genes)))

    hp_terms, hp_alt, hp_version = read_obo(sources['hp.obo'])
    entrez_terms = human_phenotypes(sources['genes_to_phenotype.txt'], hp_alt)
    if human:
        to_genes = lambda entrez: ids.lookup(entrez=entrez)
    else:
        human_to_mouse = defaultdict(set)
        for mgis, _, entrez_ids in classes:
            targets = set()
            for mgi in mgis:
                targets |= ids.lookup(mgi=mgi)
            for entrez in entrez_ids:
                human_to_mouse[entrez] |= targets
        to_genes = lambda entrez: human_to_mouse.get(entrez, set())
    direct = {t: set().union(*[to_genes(e) for e in es]) for t, es in entrez_terms.items()}
    key, name, category = ONTOLOGIES[5]
    built.append(save_ontology(out, key, name, category, hp_terms, direct, hp_version, len(genes)))

    key, name, category = ONTOLOGIES[6]
    payload = {'key': key, 'name': name, 'category': category, 'version': 'GREATv4.genes.' + assembly,
               'terms': [[g['id'], g['symbol']] for g in genes], 'genes': [[i] for i in range(len(genes))]}
    write_json(out / (key + '.json.gz'), payload)
    built.append({'key': key, 'name': name, 'category': category, 'terms': len(genes), 'genes': len(genes),
                  'associations': len(genes), 'version': payload['version']})

    manifest = {'assembly': assembly, 'built': time.strftime('%Y-%m-%d'), 'genes': len(genes),
                'chromosomes': len(sizes), 'nonGapBases': sum(sizes.values()) - sum(e - s for _, s, e in gaps),
                'ontologies': built, 'sources': dict(SOURCES, genes=GREAT_GENES.format(assembly=assembly),
                                                    curated=GREAT_CURATED.format(assembly=assembly))}
    write_json(out / 'manifest.json', manifest)
    log('{}: {:,} genes, {}'.format(assembly, len(genes), ', '.join(
        '{} {:,} terms'.format(o['name'], o['terms']) for o in built)))


def save_ontology(out, key, name, category, terms, direct, version, gene_count):
    full = drop_redundant(terms, propagate(terms, {t: g for t, g in direct.items() if g}))
    ordered = sorted(full, key=lambda t: t)
    payload = {'key': key, 'name': name, 'category': category, 'version': version,
               'terms': [[t, terms[t].get('name', t)] for t in ordered],
               'genes': [sorted(full[t]) for t in ordered]}
    write_json(out / (key + '.json.gz'), payload)
    annotated = set().union(*full.values()) if full else set()
    return {'key': key, 'name': name, 'category': category, 'terms': len(ordered), 'genes': len(annotated),
            'associations': sum(len(g) for g in full.values()), 'version': version}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('assemblies', nargs='*', metavar='assembly', help='some of {} (default: all)'.format(
        ', '.join(ASSEMBLIES)))
    ap.add_argument('--data', type=Path, default=APP / 'data' / 'GREAT', help='output folder (default data/GREAT)')
    ap.add_argument('--update', action='store_true', help='download the ontology files again (new releases)')
    ap.add_argument('--keep-downloads', action='store_true', help='keep <data>/downloads (~250 MB) after building')
    a = ap.parse_args()
    unknown = [x for x in a.assemblies if x not in ASSEMBLIES]
    if unknown:
        sys.exit('Unknown assemblies {}: choose from {}'.format(', '.join(unknown), ', '.join(ASSEMBLIES)))
    downloads = a.data / 'downloads'
    sources = {name: download(url, downloads / name, fresh=a.update) for name, url in SOURCES.items()}
    for assembly in a.assemblies or ASSEMBLIES:
        build_assembly(assembly, a.data, downloads, sources)
    if not a.keep_downloads:
        shutil.rmtree(str(downloads), ignore_errors=True)  # only the built files are read by the server
        log('removed ' + str(downloads))


if __name__ == '__main__':
    main()
