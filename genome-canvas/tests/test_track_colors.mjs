import assert from "node:assert/strict";
import test from "node:test";

import { assayTypeForTrack, automaticTrackColor, sampleKeyForTrack } from "../track-colors.mjs";

test("recognizes the reference assay palette", () => {
  assert.equal(assayTypeForTrack({ name: "CL:0000236 DNase-seq", type: "wig" }), "dnase");
  assert.equal(assayTypeForTrack({ name: "sample_A ATAC-seq", type: "wig" }), "atac");
  assert.equal(assayTypeForTrack({ name: "sample_A H3K27ac", type: "wig" }), "histone");
  assert.equal(assayTypeForTrack({ name: "sample_A RNA-seq", type: "wig" }), "rna");
  assert.equal(assayTypeForTrack({ name: "Refseq All", type: "annotation" }), "gene");
});

test("normalizes common processing suffixes to a sample key", () => {
  assert.equal(sampleKeyForTrack({ name: "1508_rmdup.bam.bw" }), "1508");
  assert.equal(sampleKeyForTrack({ name: "donor7_ATAC-seq.sorted.bigWig" }), "donor7");
});

test("same assay uses one hue while samples receive stable variants", () => {
  const first = automaticTrackColor({ name: "sample_A ATAC-seq", type: "wig" });
  const firstAgain = automaticTrackColor({ name: "sample_A ATAC-seq", type: "wig" });
  const second = automaticTrackColor({ name: "sample_B ATAC-seq", type: "wig" });
  assert.equal(first, firstAgain);
  assert.notEqual(first, second);
  assert.match(first, /^rgb\(\d+, \d+, \d+\)$/);
  assert.match(second, /^rgb\(\d+, \d+, \d+\)$/);
});

test("different assays use different base hues", () => {
  const dnase = automaticTrackColor({ name: "sample_A DNase-seq", type: "wig" });
  const rna = automaticTrackColor({ name: "sample_A RNA-seq", type: "wig" });
  assert.notEqual(dnase, rna);
  assert.match(dnase, /^rgb\(\d+, \d+, \d+\)$/);
  assert.match(rna, /^rgb\(\d+, \d+, \d+\)$/);
});
