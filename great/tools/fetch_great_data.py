#!/usr/bin/env python3
"""Download and build the data of the GREAT server (great.py): genes, regulatory domains and ontologies.

    cd great
    python3 tools/fetch_great_data.py                    # GREAT 4.0.4 (hg38 hg19 mm10 mm9) and 3.0.0 (hg19 mm10 mm9)
    python3 tools/fetch_great_data.py hg38 --update      # download the ontologies again (new GO/HPO/MGI releases)
    python3 tools/fetch_great_data.py --great 3          # only GREAT 3.0.0

GREAT 4.0.4 (data/<assembly>/), GREAT's own files (from the GREAT documentation, great-help.atlassian.net):
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

GREAT 3.0.0 (data/v3/<assembly>/; hg19, mm10, mm9; GREAT 3.0.0's danRer7 is not built): its own gene sets,
<assembly>.great3.0.genes.txt (UCSC Known Genes' canonical TSSs, Ensembl 73 ids), the same curated domains (GREAT
kept them from version to version) and its ontologies, from current releases (its 2011-2014 ones are gone):
  GO, Mouse Phenotype, Human Phenotype          as above
  Disease Ontology                              doid.obo + the Alliance's human gene-disease associations
                                                (is_implicated_in, is_marker_for)
  MSigDB Oncogenic Signatures, Immunologic Signatures, Cancer Neighborhood, Pathway, Perturbation, Predicted Promoter
  Motifs, miRNA Motifs                          MSigDB C6, C7 ImmuneSigDB, C4 CGN, C2 CP, C2 CGP, C3 TFT legacy,
                                                C3 MIR legacy (msigdb_v<release>.xml)
  PANTHER Pathway                               PANTHER SequenceAssociationPathway
  MGI Expression: Detected                      the Alliance's MGI (GXD) expression + EMAPA: one term per Theiler stage
                                                and anatomical structure, part of / is a its parents at that stage
  InterPro                                      UniProt (reviewed) InterPro references, InterPro names and hierarchy
  TreeFam                                       TreeFam 9 (EBI archive): UniProt accession -> family, its names
  HGNC Gene Families                            HGNC gene groups and their hierarchy (human assemblies)
  Ensembl Genes                                 one term per gene
GREAT 3.0.0's Placenta Disorders (dbPTB and PESNPdb are no longer published) and BioCyc Pathway (licensed) are not
built. Human-only data reach mouse genes, and mouse-only data human genes, through MGI's homology classes.

Output, per assembly: {genes.tsv, curated.tsv, chrom.sizes, gaps.bed, <ontology>.json.gz, manifest.json}. As in
GREAT, annotations propagate to parent terms (is_a; GO also part_of) and a parent term whose genes are exactly those
of one of its children is not tested. Standard library only; needs curl.
"""

import argparse
import csv
import gzip
import html
import io
import json
import re
import shutil
import subprocess
import sys
import tarfile
import time
import zipfile
from collections import defaultdict
from pathlib import Path

APP = Path(__file__).resolve().parent.parent
ASSEMBLIES = ['hg38', 'hg19', 'mm10', 'mm9']
V3_ASSEMBLIES = ['hg19', 'mm10', 'mm9']
HUMAN = {'hg38', 'hg19'}
GREAT_HELP = 'https://great-help.atlassian.net/wiki/download/attachments'
GREAT_GENES = GREAT_HELP + '/655445/GREATv4.genes.{assembly}.tsv'
GREAT3_GENES = GREAT_HELP + '/655445/{assembly}.great3.0.genes.txt'
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
HGNC_GROUPS = 'https://storage.googleapis.com/public-download-files/hgnc/csv/csv/genefamily_db_tables/'
UNIPROT = ('https://rest.uniprot.org/uniprotkb/stream?format=tsv&query=organism_id:{taxon}'
           '&fields=accession,reviewed,gene_primary,{xref},xref_geneid,xref_interpro')
INTERPRO = 'https://ftp.ebi.ac.uk/pub/databases/interpro/current_release/'
TREEFAM = 'https://ftp.ebi.ac.uk/pub/archive/treefam/download/'
ALLIANCE = 'https://fms.alliancegenome.org/download/'
MSIGDB = 'https://data.broadinstitute.org/gsea-msigdb/msigdb/release/'
PANTHER = 'http://data.pantherdb.org/ftp/pathway/current_release/'
V3_SOURCES = {  # besides SOURCES; MSigDB's and PANTHER's file names carry their release (latest_name)
    'doid.obo': 'http://purl.obolibrary.org/obo/doid.obo',
    'DISEASE-ALLIANCE_HUMAN.tsv.gz': ALLIANCE + 'DISEASE-ALLIANCE_HUMAN.tsv.gz',
    'EXPRESSION-ALLIANCE_MGI.tsv.gz': ALLIANCE + 'EXPRESSION-ALLIANCE_MGI.tsv.gz',
    'emapa.obo': 'http://purl.obolibrary.org/obo/emapa.obo',
    'uniprot_human.tsv': UNIPROT.format(taxon=9606, xref='xref_hgnc'),
    'uniprot_mouse.tsv': UNIPROT.format(taxon=10090, xref='xref_mgi'),
    'interpro_entry.list': INTERPRO + 'entry.list',
    'interpro_ParentChildTreeFile.txt': INTERPRO + 'ParentChildTreeFile.txt',
    'uniprotACC2treefam.txt.gz': TREEFAM + 'uniprotACC2treefam.txt.gz',
    'treefam9_families.json': TREEFAM + 'treefam9_families.json',
    'treefam9_family_annotation.tab.tar': TREEFAM + 'treefam9_family_annotation.tab.tar',
    'hgnc_family.csv': HGNC_GROUPS + 'family.csv',
    'hgnc_hierarchy.csv': HGNC_GROUPS + 'hierarchy.csv',
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
# The ontologies of GREAT 3.0.0, in its order: key, name, category for human, for mouse (None: not for that species).
# Not built: Placenta Disorders (after MSigDB Cancer Neighborhood) and BioCyc Pathway (after PANTHER Pathway).
V3_ONTOLOGIES = [
    ('GOMolecularFunction', 'GO Molecular Function', 'GO', 'GO'),
    ('GOBiologicalProcess', 'GO Biological Process', 'GO', 'GO'),
    ('GOCellularComponent', 'GO Cellular Component', 'GO', 'GO'),
    ('MGIPhenotype', 'Mouse Phenotype', 'Phenotype Data and Human Disease', 'Phenotype Data'),
    ('HumanPhenotypeOntology', 'Human Phenotype', 'Phenotype Data and Human Disease', 'Phenotype Data'),
    ('DiseaseOntology', 'Disease Ontology', 'Phenotype Data and Human Disease', 'Phenotype Data'),
    ('MSigDBOncogenicSignatures', 'MSigDB Oncogenic Signatures', 'Phenotype Data and Human Disease', 'Phenotype Data'),
    ('MSigDBImmunologicSignatures', 'MSigDB Immunologic Signatures', 'Phenotype Data and Human Disease',
     'Phenotype Data'),
    ('MSigCancerNeighborhood', 'MSigDB Cancer Neighborhood', 'Phenotype Data and Human Disease', None),
    ('PantherPathway', 'PANTHER Pathway', 'Pathway Data', 'Pathway Data'),
    ('MSigPathway', 'MSigDB Pathway', 'Pathway Data', 'Pathway Data'),
    ('MGIExpressionDetected', 'MGI Expression: Detected', 'Gene Expression', 'Gene Expression'),
    ('MSigPerturbation', 'MSigDB Perturbation', 'Gene Expression', 'Gene Expression'),
    ('MSigPredictedPromoterMotifs', 'MSigDB Predicted Promoter Motifs', 'Regulatory Motifs', 'Regulatory Motifs'),
    ('MSigMicroRNAMotifs', 'MSigDB miRNA Motifs', 'Regulatory Motifs', 'Regulatory Motifs'),
    ('InterPro', 'InterPro', 'Gene Families', 'Gene Families'),
    ('TreeFam', 'TreeFam', 'Gene Families', 'Gene Families'),
    ('HGNCGeneFamilies', 'HGNC Gene Families', 'Gene Families', None),
    ('EnsemblGenes', 'Ensembl Genes', 'Gene Families', 'Gene Families'),
]
V3_UNAVAILABLE = ['Placenta Disorders', 'BioCyc Pathway']
# MSigDB collections (CATEGORY_CODE[:SUB_CATEGORY_CODE] prefixes) of GREAT 3.0.0's MSigDB ontologies
MSIGDB_COLLECTIONS = {
    'MSigDBOncogenicSignatures': ('C6',), 'MSigDBImmunologicSignatures': ('C7:IMMUNESIGDB',),
    'MSigCancerNeighborhood': ('C4:CGN',), 'MSigPathway': ('C2:CP',), 'MSigPerturbation': ('C2:CGP',),
    'MSigPredictedPromoterMotifs': ('C3:TFT:TFT_LEGACY',), 'MSigMicroRNAMotifs': ('C3:MIR:MIR_LEGACY',),
}
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


def latest_name(url, pattern):
    """The newest name matching pattern (one group) in a web folder listing, by its numbers: MSigDB's and PANTHER's
    releases."""
    result = subprocess.run(['curl', '-fsSL', '--retry', '5', '--retry-delay', '10', url], stdout=subprocess.PIPE,
                            universal_newlines=True)
    names = set(re.findall(pattern, result.stdout)) if result.returncode == 0 else set()
    if not names:
        sys.exit('Could not find {} in {}'.format(pattern, url))
    return max(names, key=lambda name: [int(n) for n in re.findall(r'[0-9]+', name)])


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


class GeneMap:
    """One assembly's genes reached from HGNC, Entrez and MGI ids; ids of the other species through MGI's homology
    classes (mouse annotations reach human genes, human annotations mouse genes)."""

    def __init__(self, assembly, genes, sources):
        self.human = assembly in HUMAN
        self.ids = HumanGenes(sources['hgnc_complete_set.txt'], genes) if self.human \
            else MouseGenes(sources['MRK_ENSEMBL.rpt'], genes)
        self.mouse_to_human = defaultdict(set)
        self.hgnc_to_mouse, self.entrez_to_mouse = defaultdict(set), defaultdict(set)
        for mgis, hgncs, entrez_ids in homology(sources['HOM_MouseHumanSequence.rpt']):
            targets = set()
            if self.human:
                for hgnc in hgncs:
                    targets |= self.ids.genes_of_hgnc(hgnc)
                for mgi in mgis:
                    self.mouse_to_human[mgi] |= targets
            else:
                for mgi in mgis:
                    targets |= self.ids.lookup(mgi=mgi)
                for hgnc in hgncs:
                    self.hgnc_to_mouse[hgnc] |= targets
                for entrez in entrez_ids:
                    self.entrez_to_mouse[entrez] |= targets

    def mgi(self, mgi):
        return self.mouse_to_human.get(mgi, set()) if self.human else self.ids.lookup(mgi=mgi)

    def entrez(self, entrez):
        return self.ids.lookup(entrez=entrez) if self.human else self.entrez_to_mouse.get(entrez, set())

    def hgnc(self, hgnc):
        return self.ids.genes_of_hgnc(hgnc) if self.human else self.hgnc_to_mouse.get(hgnc, set())

    def resolve(self, direct, to_genes):
        """{term: set(source ids)} -> {term: set(gene index)}."""
        return {t: set().union(*[to_genes(x) for x in xs]) for t, xs in direct.items()}


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


def flat_terms(names):
    """Terms without a hierarchy ({id: name}), as read_obo gives them."""
    return {t: {'name': n, 'parents': set()} for t, n in names.items()}


def alliance_diseases(path, alternatives):
    """{DOID: set(HGNC id)}: the Alliance's human gene-disease associations made for the gene itself (not through
    orthologs, not negated)."""
    direct = defaultdict(set)
    col = None
    with open_text(path) as handle:
        for line in handle:
            if line.startswith('#') or not line.strip():
                continue
            f = line.rstrip('\n').split('\t')
            if col is None:
                col = {name: i for i, name in enumerate(f)}
                continue
            if len(f) < len(col) - 6 or f[col['AssociationType']] not in ('is_implicated_in', 'is_marker_for'):
                continue
            gene, term = f[col['DBObjectID']], f[col['DOID']]
            if gene.startswith('HGNC:') and term.startswith('DOID:'):
                direct[alternatives.get(term, term)].add(gene)
    return direct


def msigdb_sets(path):
    """{collection code ('C2:CP:REACTOME', 'C6', ...): [(name, description, [Entrez ids])]} from MSigDB's XML,
    read line by line (one GENESET per line; the file is not well-formed XML)."""
    attribute = re.compile(r'([A-Z_]+)="([^"]*)"')
    sets = defaultdict(list)
    with zipfile.ZipFile(str(path)) as archive:
        with archive.open(archive.namelist()[0]) as raw:
            for line in io.TextIOWrapper(raw, encoding='utf-8', errors='replace'):
                if '<GENESET ' not in line:
                    continue
                a = dict(attribute.findall(line))
                code = a.get('CATEGORY_CODE', '') + (':' + a['SUB_CATEGORY_CODE'] if a.get('SUB_CATEGORY_CODE') else '')
                text = re.sub(r'<[^>]+>', '', html.unescape(html.unescape(a.get('DESCRIPTION_BRIEF', ''))))
                text = re.sub(r'\s+', ' ', text).strip()
                sets[code].append((a['STANDARD_NAME'], text or a['STANDARD_NAME'],
                                   [e for e in a.get('MEMBERS_EZID', '').split(',') if e]))
    return sets


def panther_pathways(path):
    """({pathway: name}, {pathway: set('HGNC:..' and 'MGI:..' ids)}) from PANTHER's SequenceAssociationPathway."""
    names, direct = {}, defaultdict(set)
    with open_text(path) as handle:
        for line in handle:
            f = line.rstrip('\n').split('\t')
            if len(f) < 5 or not f[0].startswith('P'):
                continue
            names[f[0]] = f[1]
            for part in f[4].split('|')[1:]:
                if part.startswith('HGNC='):
                    direct[f[0]].add('HGNC:' + part.split('=')[-1])
                elif part.startswith('MGI='):
                    direct[f[0]].add('MGI:' + part.split('=')[-1])
    return names, direct


def mgi_expression(expression_path, emapa_path):
    """MGI Expression: Detected. Terms 'TS<nn>:EMAPA:<id>' named 'TS<nn>_<structure>', one per Theiler stage and
    EMAPA structure, whose parents are the structure's is_a / part_of parents at the same stage; their MGI genes from
    the Alliance's MGI expression file (GXD results where the gene was detected). Returns (terms, direct, version)."""
    anatomy, current = {}, None
    with open_text(emapa_path) as handle:
        for line in handle:
            line = line.rstrip('\n')
            if line.startswith('['):
                current = {'parents': set(), 'stages': [1, 28]} if line == '[Term]' else None
                continue
            if current is None or ':' not in line:
                continue
            tag, value = line.split(':', 1)
            value = value.strip()
            if tag == 'id':
                anatomy[value] = current
            elif tag == 'name':
                current['name'] = value
            elif tag == 'is_a':
                current['parents'].add(value.split()[0])
            elif tag == 'relationship':
                parts = value.split()
                if parts[0] == 'part_of':
                    current['parents'].add(parts[1])
                elif parts[0] in ('starts_at', 'ends_at') and parts[1].startswith('TS:'):
                    current['stages'][0 if parts[0] == 'starts_at' else 1] = int(parts[1][3:])

    def term_id(stage, structure):
        return 'TS{:02d}:{}'.format(stage, structure)

    terms = {}

    def add(stage, structure):
        key = term_id(stage, structure)
        if key not in terms:
            item = anatomy.get(structure, {})
            terms[key] = {'name': 'TS{:02d}_{}'.format(stage, item.get('name', structure)), 'parents': set()}
            for parent in item.get('parents', ()):
                if parent in anatomy and parent != 'EMAPA:0':
                    low, high = anatomy[parent]['stages']
                    if low <= stage <= high:
                        terms[key]['parents'].add(term_id(stage, parent))
                        add(stage, parent)
        return key

    direct, version, col = defaultdict(set), '', None
    with open_text(expression_path) as handle:
        for line in handle:
            if line.startswith('#'):
                if 'Alliance Database Version:' in line:
                    version = 'Alliance ' + line.split(':', 1)[1].strip()
                continue
            f = line.rstrip('\n').split('\t')
            if col is None:
                col = {name: i for i, name in enumerate(f)}
                continue
            if len(f) <= col['AnatomyTermID']:
                continue
            gene, stage, structure = f[col['GeneID']], f[col['StageTerm']], f[col['AnatomyTermID']]
            if gene.startswith('MGI:') and re.fullmatch(r'TS[0-9]{2}', stage) and structure.startswith('EMAPA:'):
                direct[add(int(stage[2:]), structure)].add(gene)
    return terms, direct, version


def uniprot_entries(path):
    """[(accession, reviewed, symbol, [HGNC or MGI ids], [Entrez ids], [InterPro ids])] from a UniProt TSV."""
    out = []
    with open_text(path) as handle:
        handle.readline()
        for line in handle:
            f = line.rstrip('\n').split('\t')
            if len(f) < 6:
                continue
            ids = lambda text: [x for x in text.split(';') if x.strip()]
            out.append((f[0], f[1] == 'reviewed', f[2].split(';')[0].strip(), ids(f[3]), ids(f[4]), ids(f[5])))
    return out


def interpro_terms(entry_list, tree):
    """InterPro entries {id: {'name', 'parents'}} with the hierarchy of ParentChildTreeFile.txt."""
    terms = {}
    with open_text(entry_list) as handle:
        handle.readline()
        for line in handle:
            f = line.rstrip('\n').split('\t')
            if len(f) >= 3 and f[0].startswith('IPR'):
                terms[f[0]] = {'name': f[2], 'parents': set()}
    stack = []
    with open_text(tree) as handle:
        for line in handle:
            match = re.match(r'((?:--)*)(IPR[0-9]+)', line)
            if not match:
                continue
            depth = len(match.group(1)) // 2
            del stack[depth:]
            if stack and match.group(2) in terms:
                terms[match.group(2)]['parents'].add(stack[-1])
            stack.append(match.group(2))
    return terms


def treefam_names(families_json, annotation_tar):
    """{TreeFam family: name}: the curated description (treefam9_families.json), else the family annotation's
    description, symbol or HGNC genes, else the family id."""
    names = {}
    with tarfile.open(str(annotation_tar)) as archive:
        member = next(m for m in archive.getmembers() if m.isfile())
        handle = io.TextIOWrapper(archive.extractfile(member), encoding='utf-8', errors='replace')
        col = {name: i for i, name in enumerate(handle.readline().rstrip('\n').split('\t'))}
        for line in handle:
            f = line.rstrip('\n').split('\t')
            if len(f) < len(col):
                continue
            known = lambda text: text.strip() if text.strip() not in ('', 'NaN') else ''
            genes = ', '.join(g.split('->')[0] for g in f[col['hgnc']].split() if known(g.split('->')[0]))
            names[f[0]] = known(f[col['fam_description']]) or known(f[col['fam_symbol']]) or genes or f[0]
    for family in json.loads(Path(families_json).read_text()):
        names[family['modelName']] = family.get('description') or family.get('hgncSymbol') or names.get(
            family['modelName'], family['modelName'])
    return names


def hgnc_groups(hgnc_path, family_path, hierarchy_path):
    """HGNC gene groups: ({'HGNCGroup:<id>': {'name', 'parents'}}, {group: set(HGNC id)})."""
    terms = {}
    with open(str(family_path), encoding='utf-8', newline='') as handle:
        for row in csv.DictReader(handle):
            terms['HGNCGroup:' + row['id']] = {'name': row['name'], 'parents': set()}
    with open(str(hierarchy_path), encoding='utf-8', newline='') as handle:
        for row in csv.DictReader(handle):
            child = 'HGNCGroup:' + row['child_fam_id']
            if child in terms:
                terms[child]['parents'].add('HGNCGroup:' + row['parent_fam_id'])
    direct = defaultdict(set)
    with open_text(hgnc_path) as handle:
        header = handle.readline().rstrip('\n').split('\t')
        col = {name: i for i, name in enumerate(header)}
        for line in handle:
            f = line.rstrip('\n').split('\t')
            if len(f) > col['gene_group_id']:
                for group in re.split(r'[|,]', f[col['gene_group_id']].strip('"')):
                    if group.strip():
                        direct['HGNCGroup:' + group.strip()].add(f[col['hgnc_id']].strip('"'))
    return terms, direct


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


def write_genome(assembly, out, raw, genes, curated_file):
    """genes.tsv, chrom.sizes, gaps.bed and curated.tsv of one assembly; returns (sizes, gaps)."""
    out.mkdir(parents=True, exist_ok=True)
    sizes_file = download(UCSC.format(assembly=assembly) + 'bigZips/{}.chrom.sizes'.format(assembly),
                          raw / '{}.chrom.sizes'.format(assembly))
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
    return sizes, gaps


def go_ontology(out, key, name, category, genemap, sources, go, gene_count):
    """One GO namespace from GOA (human) or MGI's GAF (mouse); go = read_obo of go-basic.obo with part_of."""
    ids = genemap.ids
    if genemap.human:
        resolve = lambda f: ids.lookup(symbol=f[2], uniprot=f[1] if f[0] == 'UniProtKB' else None)
        gaf = sources['goa_human.gaf.gz']
    else:
        resolve = lambda f: ids.lookup(mgi=f[1] if f[1].startswith('MGI:') else 'MGI:' + f[1], symbol=f[2])
        gaf = sources['mgi.gaf.gz']
    go_terms, go_alt, go_version = go
    direct = read_gaf(gaf, go_terms, go_alt, GO_NAMESPACES[key], resolve)
    return save_ontology(out, key, name, category, go_terms, direct, go_version, gene_count)


def mouse_phenotype(out, key, name, category, genemap, sources, mp, gene_count, single=False):
    """Mouse Phenotype (every genotype) or Mouse Phenotype Single KO (genotypes of one gene)."""
    mp_terms, mp_alt, mp_version = mp
    report, column = ('MGI_GenePheno.rpt', 6) if single else ('MGI_PhenoGenoMP.rpt', 5)
    markers = mgi_phenotypes(sources[report], column, mp_alt, single)
    return save_ontology(out, key, name, category, mp_terms, genemap.resolve(markers, genemap.mgi), mp_version,
                         gene_count)


def human_phenotype(out, key, name, category, genemap, sources, gene_count):
    hp_terms, hp_alt, hp_version = read_obo(sources['hp.obo'])
    entrez_terms = human_phenotypes(sources['genes_to_phenotype.txt'], hp_alt)
    return save_ontology(out, key, name, category, hp_terms, genemap.resolve(entrez_terms, genemap.entrez), hp_version,
                         gene_count)


def ensembl_genes(out, key, name, category, genes, version):
    payload = {'key': key, 'name': name, 'category': category, 'version': version,
               'terms': [[g['id'], g['symbol']] for g in genes], 'genes': [[i] for i in range(len(genes))]}
    write_json(out / (key + '.json.gz'), payload)
    return {'key': key, 'name': name, 'category': category, 'terms': len(genes), 'genes': len(genes),
            'associations': len(genes), 'version': version}


def build_assembly(assembly, data, downloads, sources):
    """GREAT 4.0.4: data/<assembly>/."""
    out = data / assembly
    raw = downloads / assembly
    genes_file = download(GREAT_GENES.format(assembly=assembly), raw / 'GREATv4.genes.{}.tsv'.format(assembly))
    curated_file = download(GREAT_CURATED.format(assembly=assembly),
                            raw / 'GREATv4.curatedRegDoms.{}.txt'.format(assembly))
    genes = read_great_genes(genes_file)
    sizes, gaps = write_genome(assembly, out, raw, genes, curated_file)
    genemap = GeneMap(assembly, genes, sources)
    built = []
    go = read_obo(sources['go-basic.obo'], relations=('part_of',))
    for key, name, category in ONTOLOGIES[:3]:
        built.append(go_ontology(out, key, name, category, genemap, sources, go, len(genes)))
    mp = read_obo(sources['mp.obo'])
    built.append(mouse_phenotype(out, *ONTOLOGIES[3], genemap, sources, mp, len(genes)))
    built.append(mouse_phenotype(out, *ONTOLOGIES[4], genemap, sources, mp, len(genes), single=True))
    built.append(human_phenotype(out, *ONTOLOGIES[5], genemap, sources, len(genes)))
    built.append(ensembl_genes(out, *ONTOLOGIES[6], genes, 'GREATv4.genes.' + assembly))

    manifest = {'assembly': assembly, 'built': time.strftime('%Y-%m-%d'), 'genes': len(genes),
                'chromosomes': len(sizes), 'nonGapBases': sum(sizes.values()) - sum(e - s for _, s, e in gaps),
                'ontologies': built, 'sources': dict(SOURCES, genes=GREAT_GENES.format(assembly=assembly),
                                                    curated=GREAT_CURATED.format(assembly=assembly))}
    write_json(out / 'manifest.json', manifest)
    log('{}: {:,} genes, {}'.format(assembly, len(genes), ', '.join(
        '{} {:,} terms'.format(o['name'], o['terms']) for o in built)))


class V3Sources:
    """GREAT 3.0.0's annotation sources, read once for every assembly."""

    def __init__(self, sources, v3):
        self.sources, self.v3 = sources, v3
        self.cache = {}

    def get(self, name, read):
        if name not in self.cache:
            self.cache[name] = read()
        return self.cache[name]

    def msigdb(self, key):
        sets = self.get('msigdb', lambda: msigdb_sets(self.v3['msigdb']))
        names, direct = {}, {}
        for code, items in sets.items():
            if any(code == p or code.startswith(p + ':') for p in MSIGDB_COLLECTIONS[key]):
                for name, description, entrez in items:
                    names[name] = description
                    direct[name] = set(entrez)
        return flat_terms(names), direct, self.v3['msigdbRelease']

    def uniprot(self, human):
        name = 'uniprot_human.tsv' if human else 'uniprot_mouse.tsv'
        return self.get(name, lambda: uniprot_entries(self.v3[name]))


def build_v3(assembly, data, downloads, sources, v3):
    """GREAT 3.0.0: data/v3/<assembly>/."""
    out = data / 'v3' / assembly
    raw = downloads / assembly
    genes_file = download(GREAT3_GENES.format(assembly=assembly), raw / '{}.great3.0.genes.txt'.format(assembly))
    curated_file = download(GREAT_CURATED.format(assembly=assembly),
                            raw / 'GREATv4.curatedRegDoms.{}.txt'.format(assembly))
    genes = read_great_genes(genes_file)
    sizes, gaps = write_genome(assembly, out, raw, genes, curated_file)
    genemap = GeneMap(assembly, genes, sources)
    human, ids, count = genemap.human, genemap.ids, len(genes)

    def protein_genes(entry):
        """GREAT genes of one UniProt entry of the assembly's species."""
        accession, _, symbol, xrefs, entrez_ids, _ = entry
        if human:
            found = set().union(*[ids.genes_of_hgnc(h) for h in xrefs]) if xrefs else set()
            for entrez in entrez_ids if not found else ():
                found |= ids.lookup(entrez=entrez)
            return found or (ids.lookup(symbol=symbol) if symbol else set())
        found = set().union(*[ids.lookup(mgi=m) for m in xrefs]) if xrefs else set()
        return found or (ids.lookup(symbol=symbol) if symbol else set())

    def interpro():
        terms = v3.get('interpro', lambda: interpro_terms(v3.v3['interpro_entry.list'],
                                                          v3.v3['interpro_ParentChildTreeFile.txt']))
        direct = defaultdict(set)
        for entry in v3.uniprot(human):
            if entry[1]:
                genes_of = protein_genes(entry)
                for term in entry[5]:
                    direct[term] |= genes_of
        return terms, direct, 'downloaded ' + time.strftime('%Y-%m-%d')

    def treefam():
        names = v3.get('treefam', lambda: treefam_names(v3.v3['treefam9_families.json'],
                                                        v3.v3['treefam9_family_annotation.tab.tar']))
        by_accession = {}
        for entry in v3.uniprot(human):
            by_accession[entry[0]] = entry
        direct = defaultdict(set)
        with open_text(v3.v3['uniprotACC2treefam.txt.gz']) as handle:
            for line in handle:
                f = line.split()
                if len(f) == 2 and f[0] in by_accession and f[1] in names:
                    direct[f[1]] |= protein_genes(by_accession[f[0]])
        return flat_terms(names), direct, '9 (2013, EBI archive)'

    def disease():
        do_terms, do_alt, do_version = v3.get('doid', lambda: read_obo(v3.v3['doid.obo']))
        hgnc_terms = v3.get('diseases', lambda: alliance_diseases(v3.v3['DISEASE-ALLIANCE_HUMAN.tsv.gz'], do_alt))
        return do_terms, genemap.resolve(hgnc_terms, genemap.hgnc), do_version

    def panther():
        names, direct = v3.get('panther', lambda: panther_pathways(v3.v3['panther']))
        prefix = 'HGNC:' if human else 'MGI:'
        to_genes = lambda x: (genemap.hgnc(x) if human else genemap.mgi(x)) if x.startswith(prefix) else set()
        return flat_terms(names), genemap.resolve(direct, to_genes), v3.v3['pantherRelease']

    def expression():
        terms, direct, version = v3.get('expression', lambda: mgi_expression(
            v3.v3['EXPRESSION-ALLIANCE_MGI.tsv.gz'], v3.v3['emapa.obo']))
        return terms, genemap.resolve(direct, genemap.mgi), version

    def gene_groups():
        terms, direct = v3.get('hgnc_groups', lambda: hgnc_groups(
            sources['hgnc_complete_set.txt'], v3.v3['hgnc_family.csv'], v3.v3['hgnc_hierarchy.csv']))
        return terms, genemap.resolve(direct, genemap.hgnc), 'downloaded ' + time.strftime('%Y-%m-%d')

    def msigdb(key):
        terms, direct, version = v3.msigdb(key)
        return terms, genemap.resolve(direct, genemap.entrez), version

    built = []
    for key, name, human_category, mouse_category in V3_ONTOLOGIES:
        category = human_category if human else mouse_category
        if category is None:
            continue
        if key in GO_NAMESPACES:
            go = v3.get('go', lambda: read_obo(sources['go-basic.obo'], relations=('part_of',)))
            built.append(go_ontology(out, key, name, category, genemap, sources, go, count))
        elif key == 'MGIPhenotype':
            built.append(mouse_phenotype(out, key, name, category, genemap, sources,
                                         v3.get('mp', lambda: read_obo(sources['mp.obo'])), count))
        elif key == 'HumanPhenotypeOntology':
            built.append(human_phenotype(out, key, name, category, genemap, sources, count))
        elif key == 'EnsemblGenes':
            built.append(ensembl_genes(out, key, name, category, genes, '{}.great3.0.genes'.format(assembly)))
        else:
            build = {'DiseaseOntology': disease, 'PantherPathway': panther, 'MGIExpressionDetected': expression,
                     'InterPro': interpro, 'TreeFam': treefam, 'HGNCGeneFamilies': gene_groups}.get(key)
            terms, direct, version = build() if build else msigdb(key)
            built.append(save_ontology(out, key, name, category, terms, direct, version, count))

    manifest = {'assembly': assembly, 'great': '3.0.0', 'built': time.strftime('%Y-%m-%d'), 'genes': len(genes),
                'chromosomes': len(sizes), 'nonGapBases': sum(sizes.values()) - sum(e - s for _, s, e in gaps),
                'ontologies': built, 'notBuilt': V3_UNAVAILABLE,
                'sources': dict(SOURCES)}
    manifest['sources'].update(V3_SOURCES)
    manifest['sources'].update(msigdb=v3.v3['msigdbURL'], panther=v3.v3['pantherURL'],
                               genes=GREAT3_GENES.format(assembly=assembly), curated=GREAT_CURATED.format(assembly=assembly))
    write_json(out / 'manifest.json', manifest)
    log('GREAT 3.0.0 {}: {:,} genes, {}'.format(assembly, len(genes), ', '.join(
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
    ap.add_argument('--great', choices=('4', '3', 'all'), default='all',
                    help='GREAT version: 4 (4.0.4, data/<assembly>), 3 (3.0.0, data/v3/<assembly>) or all (default)')
    ap.add_argument('--data', type=Path, default=APP / 'data', help='output folder (default great/data)')
    ap.add_argument('--update', action='store_true', help='download the ontology files again (new releases)')
    ap.add_argument('--keep-downloads', action='store_true', help='keep <data>/downloads (~400 MB) after building')
    a = ap.parse_args()
    v4 = [x for x in a.assemblies or ASSEMBLIES if x in ASSEMBLIES] if a.great in ('4', 'all') else []
    v3 = [x for x in a.assemblies or V3_ASSEMBLIES if x in V3_ASSEMBLIES] if a.great in ('3', 'all') else []
    unknown = [x for x in a.assemblies if x not in v4 + v3]
    if unknown:
        sys.exit('Unknown assemblies {}: GREAT 4.0.4 has {}, GREAT 3.0.0 {}'.format(
            ', '.join(unknown), ', '.join(ASSEMBLIES), ', '.join(V3_ASSEMBLIES)))
    downloads = a.data / 'downloads'
    sources = {name: download(url, downloads / name, fresh=a.update) for name, url in SOURCES.items()}
    for assembly in v4:
        build_assembly(assembly, a.data, downloads, sources)
    if v3:
        files = {name: download(url, downloads / name, fresh=a.update) for name, url in V3_SOURCES.items()}
        release = latest_name(MSIGDB, r'href="([0-9]{4}\.[0-9]+)\.Hs/"')
        files['msigdbRelease'] = release + '.Hs'
        files['msigdbURL'] = '{0}{1}.Hs/msigdb_v{1}.Hs.xml.zip'.format(MSIGDB, release)
        files['msigdb'] = download(files['msigdbURL'], downloads / 'msigdb_v{}.Hs.xml.zip'.format(release))
        panther = latest_name(PANTHER, r'href="(SequenceAssociationPathway[0-9.]+\.txt)"')
        files['pantherRelease'] = re.sub(r'^SequenceAssociationPathway|\.txt$', '', panther)
        files['pantherURL'] = PANTHER + panther
        files['panther'] = download(files['pantherURL'], downloads / panther)
        shared = V3Sources(sources, files)
        for assembly in v3:
            build_v3(assembly, a.data, downloads, sources, shared)
    if not a.keep_downloads:
        shutil.rmtree(str(downloads), ignore_errors=True)  # only the built files are read by the server
        log('removed ' + str(downloads))


if __name__ == '__main__':
    main()
