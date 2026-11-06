# GREAT

GREAT (Genomic Regions Enrichment of Annotations Tool; McLean et al. 2010, *Nat Biotechnol* 28:495) for Omics Canvas:
GO, phenotype and gene enrichment of a set of genomic regions, as GREAT 4.0.4 (the default) and GREAT 3.0.0 computed it,
with its web interface, a more flexible region reader and GREAT's own interfaces for rGREAT and scripts. great.stanford.edu no longer answers,
so this follows GREAT's published core code (greatTools: `createRegulatoryDomains.c`, `calculateBinomialP.c`, ported
line by line) and documentation.

On the lab server: **http://171.65.68.140:8892/great/** (the gateway routes `/great/` to `server.py`, port 8894).

| Path | Role |
| --- | --- |
| `server.py` | HTTP server (standard library, Python 3.8+): the page, `/great/api/`, GREAT's `.php` interfaces, `rgreat.R` |
| `great.py` | the engine: region reader, regulatory domains, statistics, analysis, term details; also the worker process (`great.py --worker <data>`) |
| `great_service.py` | jobs, worker pool, live limits and cleanup, GREAT's output formats |
| `web/` | the page (`index.html`, `great.mjs`, `great.css`; `base.css` and `fonts/` carry Genome Canvas's look) |
| `tools/fetch_great_data.py` | downloads and builds `data/<assembly>/` |
| `tests/` | `python3 -m unittest discover -s tests` (in CI) |

## What it does

- **Genes and domains**: GREAT 4.0.4's own gene sets (one canonical TSS per gene; 18,777 genes in hg38, 18,549 in
  hg19, 21,395 in mm10, 19,996 in mm9) and curated regulatory domains (SHH, HOXD, β-globin). Rules: basal plus
  extension (5 kb up, 1 kb down, extended to the nearest basal domains up to 1 Mb), two nearest genes, single nearest
  gene. A region belongs to every gene whose domain holds its midpoint, `floor((start + end − 1) / 2)` in 0-based
  coordinates; distances are to the TSS (a minus-strand gene's first base is its TSS − 1). With rGREAT's example
  regions this gives the same 885 associated regions, the same 1,418 genes and the same distances as
  great.stanford.edu.
- **Statistics**: binomial test over regions (p = the non-gap fraction of the genome in the term's domains, UCSC gap
  tables; GREAT's incomplete-beta code), hypergeometric test over genes; with a background set, a hypergeometric test
  over regions (test regions missing from the background are added to it, with a warning). Bonferroni and GREAT's FDR
  (m·p/rank) over the terms tested; the page recomputes them when the term annotation count range changes.
- **Ontologies**: GO Molecular Function / Biological Process / Cellular Component, Mouse Phenotype, Mouse Phenotype
  Single KO, Human Phenotype, Ensembl Genes, built from current releases (GREAT's 2019 ones are no longer published;
  results therefore differ from old GREAT runs in the terms, not in the method). Annotations propagate to parent terms
  (is_a, GO also part_of) and a parent with exactly a child's genes is not tested, as in GREAT. Mouse phenotypes reach
  human genes, and human phenotypes mouse genes, through MGI's homology classes.
- **Input** (`read_regions`): BED3–BED12, narrowPeak / broadPeak (optionally placed at the summit), bedGraph, CSV / TSV
  tables with a header naming the columns (`chrom`/`seqnames`, `start`, `end`, `name` … in any column; GRanges-style
  tables are 1-based), MACS `.xls`, region strings (`chr1:1,000-2,000`, `chr1-1000-2000`), positions, `.gz` `.bz2`
  `.xz` `.zip`; `1`/`MT`/PLINK 23-24 chromosome names are mapped, and contigs outside the assembly, swapped or
  out-of-range coordinates are reported instead of failing the job. Sources: upload, paste, a server file, a
  Genome Canvas track (right-click a BED-like track → **GREAT enrichment of these regions…**) or a public URL.
- **Page**: GREAT's views (significant by both tests / by the binomial test / all), FDR, fold enrichment, gene hit,
  annotation count and text filters, column choice and sorting, the three region-gene association graphs, term pages
  (graphs against all regions, region → genes and gene → regions tables, links into Genome Canvas), and exports:
  all data as GREAT's `.tsv`, shown data as `.tsv` / HTML, associations, BED of a term's regions, graphs as SVG / PNG
  (Arial).
- **GREAT 3.0.0** (the version selector on the page, `version=3` in the API, `/great/public-3.0.0/…` for rGREAT's
  `version = "3.0.0"`): GREAT 3.0.0's own gene sets (UCSC Known Genes' canonical TSSs with Ensembl 73 ids; 18,041 genes
  in hg19, 21,902 in mm10, 21,176 in mm9; its danRer7 is not offered), the same curated domains and method, and its
  ontologies in its order and categories, from current releases: GO, Mouse Phenotype, Human Phenotype, Disease Ontology
  (the Alliance's human gene-disease associations), MSigDB Oncogenic and Immunologic Signatures, Cancer Neighborhood,
  Pathway, Perturbation, Predicted Promoter Motifs and miRNA Motifs (MSigDB C6, C7 ImmuneSigDB, C4 CGN, C2 CP, C2 CGP,
  C3 TFT and MIR legacy), PANTHER Pathway, MGI Expression: Detected (Theiler stage × EMAPA structure), InterPro,
  TreeFam (TreeFam 9), HGNC Gene Families (human), Ensembl Genes. Placenta Disorders (dbPTB, PESNPdb) and BioCyc
  Pathway (licensed) can no longer be built. Against the GREAT 3.0.0 run in rGREAT's 2018 vignette (hg19, 1,005 random
  regions; submit them with `gr_is_zero_based = TRUE`, as rGREAT then did) the total gene count (18,041), a regulatory
  domain's genome fraction (PNP: 1.435477e-05) and the distances are the same; 1,586 genes are hit instead of 1,585
  (one of the HOXD curated domains, which could not be pinned down). Data: `data/v3/<assembly>/`.
- **rGREAT and scripts**: GREAT's interfaces under `/great/public/cgi-bin/` (and `/great/public-4.0.4/…`,
  `/great/public-3.0.0/…`; other versions answer with an error; a `version` field also chooses one):
  `greatWeb.php` (rGREAT's `submitGreatJob`), `readJsFromFile.php`, `downloadAllTSV.php`, `downloadAssociations.php`
  and `showTermDetails.php` (rGREAT's result functions) and `greatStart.php`, GREAT's batch Programming Interface
  (`requestURL` or, added here, a posted `requestFile`; `outputType=batch` returns every ontology as a table).
  `source("http://<server>/great/rgreat.R")` points rGREAT at this server for the R session (it rewrites
  rGREAT's `BASE_URL_LIST`); then `submitGreatJob(gr, genome = "hg38", request_interval = 0)`,
  `getEnrichmentTables`, `getRegionGeneAssociations` and `plotRegionGeneAssociations` work as with the original.
- **Limits** (`state/great/settings.json`, edited on the admin page `/admin/` → **GREAT** by an administrator and
  applied at once, also to waiting requests): minimum interval between analyses per client (default 5 s; GREAT asked
  for 60), analyses per client per hour, analyses at once (worker processes), queue wait before HTTP 429, region and
  file size limits, result retention (default 30 days) and storage limit (default 20 GB: above it the oldest results
  go), terms per ontology in batch exports (default all; GREAT: 500),
  remote URLs on/off, the API on/off, exempt and blocked clients. The panel also shows running and waiting analyses,
  workers and the clients of the last hour, and lists every stored analysis (workspace, size, age) to delete selected
  ones, those older than N days or all. A cleanup thread applies the retention and the storage limit every 10 minutes. Administrators: the sign-in's (`auth/`); a GREAT running without sign-in uses `admins`
  in `great/great.config.json` (Genome Canvas workspace ids or names), by default the server account's Home workspace.


## Running it

```bash
cd great
python3 tools/fetch_great_data.py                 # GREAT 4.0.4 data/<assembly> and 3.0.0 data/v3/<assembly> (~450 MB download, ~90 MB kept)
python3 server.py --port 8894 --canvas http://127.0.0.1:8000
```

`tools/fetch_great_data.py` downloads GREAT 4.0.4's and 3.0.0's gene and curated-domain files
(great-help.atlassian.net), UCSC chromosome sizes and gaps, GO (go-basic.obo, GOA human, MGI GAF), HPO (hp.obo,
genes_to_phenotype), MGI (mp.obo, phenotype and homology reports) and HGNC and, for GREAT 3.0.0, DOID and the
Alliance's disease and expression files, EMAPA, MSigDB's XML, PANTHER's pathway table, UniProt, InterPro and TreeFam 9;
it builds `data/<assembly>/` and `data/v3/<assembly>/` (`--great 4` or `--great 3` for one version) and deletes the
downloads (`--keep-downloads` keeps them). Run it again with `--update` for new releases, then restart GREAT.

On the lab server `./vizhubctl restart great` (unit `vizhub-great.service`, port `VIZHUB_GREAT_PORT` in
`deploy/site.env`) and `./vizhubctl logs great`. Options: `--host`, `--port`, `--canvas` (Genome Canvas's internal
address: its workspace list and `/data/` files), `--canvas-path` (where the gateway publishes Genome Canvas, default
`/genome-canvas/`), `--data` (default `data/`), `--state` (default `../state/great/`: `jobs/`, `settings.json`).
`great.config.json` (local, not in git; see `great.config.example.json`) can set the same keys and `admins`.

With sign-in on (`auth/`), the page and its API need a session; GREAT's `.php` interfaces and `rgreat.R` stay open and
read only uploaded regions and public URLs, never the server's files. Workspaces are Genome Canvas's: the page reads the selected one from Genome Canvas's API (where its cookie applies)
and sends it as `X-Vizhub-Workspace`, as the hub's pages do; GREAT checks it against Genome Canvas's workspace list.
Regions from Genome Canvas files are read through Genome Canvas's workspace-scoped `/data/` URLs, so they follow its
access rules. Results are open to anyone with a job's link through the `.php` interfaces (as GREAT's sessions were), and GREAT
fetches only public URLs.
