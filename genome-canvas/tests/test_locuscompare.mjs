import assert from "node:assert/strict";
import test from "node:test";

import { LD_COLORS } from "../manhattan-style.mjs";
import { ldClass, locusCompareSVG, locusCompareTSV, niceTicks, packGenes, snpTooltip } from "../locuscompare.mjs";

const data = {
  region: { chrom: "chr6", start: 1000, end: 9000 },
  gwas: { label: "CAD", subset: "", subsets: [] },
  eqtl: { symbol: "GENE1", gene: "ENSG1", tissueLabel: "Artery - Coronary", tissue: "artery_coronary" },
  ld: { available: true, label: "1000 Genomes EUR" },
  lead: "rs1",
  snps: [
    { id: "rs1", rsid: "rs1", pos: 2000, ref: "A", alt: "G", p1: 1e-10, logp1: 10, beta1: 0.1, p2: 1e-8, logp2: 8, beta2: 0.5, r2: 1 },
    { id: "6:3000:C:T", rsid: "", pos: 3000, ref: "C", alt: "T", p1: 1e-6, logp1: 6, beta1: null, p2: 0.01, logp2: 2, beta2: 0.1, r2: null },
    { id: "rs3", rsid: "rs3", pos: 4000, ref: "G", alt: "A", p1: 0.2, logp1: 0.7, beta1: 0, p2: 0.3, logp2: 0.5, beta2: 0, r2: 0.85 },
  ],
  genes: [
    { id: "ENSG1", symbol: "GENE1", start: 1500, end: 5000, strand: "+" },
    { id: "ENSG2", symbol: "GENE2", start: 4000, end: 8000, strand: "-" },
  ],
};

test("LD classes follow the GWAS tracks' r² bins, the lead apart", () => {
  assert.equal(ldClass(0.9), "high");
  assert.equal(ldClass(0.6), "mediumHigh");
  assert.equal(ldClass(0.1), "low");
  assert.equal(ldClass(null), "missing");
  assert.equal(ldClass(0.1, true), "reference");
});

test("axis ticks are round numbers covering the range", () => {
  assert.deepEqual(niceTicks(0, 10, 5), [0, 2, 4, 6, 8, 10]);
  assert.deepEqual(niceTicks(12.4, 13.4, 5), [12.4, 12.6, 12.8, 13, 13.2, 13.4]);
});

test("overlapping genes go to separate rows", () => {
  const { placed, rows } = packGenes(data.genes, (pos) => pos / 10);
  assert.equal(rows, 2);
  assert.notEqual(placed[0].row, placed[1].row);
});

test("the figure has a point per variant in each panel, the lead labelled and colored", () => {
  const figure = locusCompareSVG(data, { theme: { panelText: "#111" } });
  assert.equal(figure.points.length, 9);  // scatter + two LocusZoom panels
  assert.match(figure.svg, /^<svg /);
  assert.match(figure.svg, />rs1</);
  assert.ok(figure.svg.includes(LD_COLORS.reference));
  assert.ok(figure.svg.includes(LD_COLORS.high));     // rs3, r² 0.85
  assert.ok(figure.svg.includes(LD_COLORS.missing));  // the variant without LD
  assert.match(figure.svg, /GENE2/);
  assert.ok(figure.height > 400);
});

test("the table lists every shared variant and marks the lead", () => {
  const lines = locusCompareTSV(data).trim().split("\n");
  assert.equal(lines.length, 4);
  assert.equal(lines[0].split("\t")[0], "variant");
  assert.ok(lines[1].endsWith("\t1"));
  assert.match(snpTooltip(data, data.snps[2]), /Click: use as the lead SNP/);
});
