# Track data

Place local track files and their indexes here, or configure other directories in `genomecanvas.config.json`.

Examples: `sample.bam` + `sample.bam.bai`, `variants.vcf.gz` + `variants.vcf.gz.tbi`, or `signal.bigWig`.

# Manhattan / regional association tracks

Genome Canvas recognizes `.gwas`, `.gwas.gz`, and `.gwas.bgz` files. For
large studies, use BGZF compression plus a Tabix index. The first row must be
a tab-delimited header containing chromosome, position, and raw p-value
columns, for example:

```text
chromosome  position  rsid  pvalue  beta  stderr_beta
chr1        123456    rs1   1e-9    0.12  0.03
```

Index a coordinate-sorted file whose first row is the header with:

```bash
bgzip study.hg38.gwas
tabix -S 1 -s 1 -b 2 -e 2 study.hg38.gwas.gz
```

Files loaded in the website are assumed to already match the selected genome
assembly. Liftover is intentionally an offline preparation step.

## Precomputed LD

LD r² is specific to a reference SNP and a reference population; a single value
per variant cannot describe its LD with every possible lead. To avoid PLINK
calculations while browsing selected regions, create a separate indexed track:

```bash
python3 tools/precompute_gwas_ld.py \
  --input data/study.hg38.gwas.gz \
  --output data/study.hg38.region.ld.gwas.gz \
  --region chr9:21737208-22275000
```

The tool uses `ldReferenceHg19` from `genomecanvas.config.json`, requires the
original `hg19_locus` or `grch37_locus` and allele columns, and defaults to the
strongest association in each region. Use `--lead rs1412829` for a specific lead
in one region, or repeat `--region` for non-overlapping regions. It writes only
the selected regions, never overwrites the input or an existing output, and
creates a matching `.tbi` index and `.ld.json` provenance file. This is not an
automatic genome-wide clumping or fine-mapping analysis.

The additional columns are `ld_r2`, `ld_reference`, `ld_reference_locus`
(GRCh37), `ld_population`, `ld_method`, and `ld_is_reference`. Unmatched variants
have `ld_r2 = .` and render gray; the fixed reference renders as a diamond.
Colors remain relative to the saved lead when zooming, even if that lead is
outside the visible region. Load the original track for viewport-dependent LD.
Embedded LD works without a configured backend panel, including local mode.

## Genome-wide lead discovery and LD

To identify lead variants from the complete summary-statistics file and preserve
all original rows in the annotated output, run:

```bash
python3 tools/precompute_gwas_ld_genomewide.py \
  --input data/study.hg38.gwas.gz \
  --output data/study.hg38.ld.gwas.gz \
  --p-threshold 5e-8 --clump-r2 0.1 --radius-kb 1000 \
  --workers 4 --threads 2
```

The pipeline matches significant variants to the local GRCh37 reference by
original coordinates and alleles, including single-base strand complements. It
uses coordinate-and-allele genotype IDs rather than requiring an rsID in the
GWAS table. Ambiguous duplicate panel variants are excluded. PLINK2 unphased LD
clumping selects index variants at `P <= 5e-8` with an r² threshold of `0.1` in a
1 Mb radius. These are reference-panel clumped leads, not conditional or
fine-mapped causal variants. Overlapping lead windows are merged into loci.

All lead–panel variant pairs within the specified radius are computed with
`--r2-unphased`, including r² below 0.1. The main table uses the strongest r²
among eligible leads, with self-reference first and ties resolved by association
strength, distance, and genotype ID. Thus different points in an overlapping
locus can refer to different leads: inspect `ld_reference` in the popup. The
complete pair table retains every calculated lead–variant relationship rather
than discarding secondary references.

The generated files are:

- `study.hg38.ld.gwas.gz` and `.tbi`: complete original hg38 table plus embedded
  LD, reference, and locus annotations; original columns and row order are preserved.
- `.leads.tsv` and `.loci.tsv`: lead and merged-locus catalogs.
- `.ld-pairs.hg19.tsv.gz` and `.tbi`: all calculated pairs, indexed by the target
  variant's **GRCh37** position. Lead positions are also provided in hg38.
- `.ld.json`: parameters, reference paths, coverage, catalogs, row counts,
  and a SHA-256 digest validating the preserved original data.

Outside discovered windows, unavailable chromosomes, and variants absent from
the panel have missing LD rather than invented r² values. These rows remain in
the output and render gray. This is genome-wide lead-oriented precomputation,
not an all-by-all matrix over every variant in the genome. No output is published
until annotation, row preservation, reference counts, and indexes validate.

For a separately named chrX reference, also provide
`--bfile-x /path/to/chrX.genotypes --sex-panel /path/to/1000G.ALL.panel`.
The sample panel must contain `sample` and `gender` columns. chrX processing
updates sex in memory, separates GRCh37 pseudoautosomal regions with
`--split-par b37`, and does not modify the original genotype files. Missing
chromosome panels are explicitly reported in the provenance file.
