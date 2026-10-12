import test from "node:test";
import assert from "node:assert/strict";

import {
  filterHubTracks,
  flattenUCSCHubGroups,
  hubsForGenome,
  inferHubKind,
  normalizeWashUHub,
} from "../public-hubs.mjs";

test("public catalog only returns hubs compatible with the current assembly", () => {
  const hg38 = hubsForGenome("hg38");
  assert.ok(hg38.some((hub) => hub.id === "ucsc-encode-dna"));
  assert.ok(hg38.some((hub) => hub.id === "ucsc-gtex-analysis"));
  assert.ok(hg38.some((hub) => hub.id === "washu-roadmap-states"));
  assert.ok(!hg38.some((hub) => hub.id === "washu-4dn-mouse"));
  assert.deepEqual(hubsForGenome("mm10", "4dn").map((hub) => hub.id), ["washu-4dn-mouse"]);
});

test("custom hub kind is inferred from the descriptor extension", () => {
  assert.equal(inferHubKind("https://example.org/hub.txt"), "ucsc");
  assert.equal(inferHubKind("https://example.org/data.json?version=2"), "washu");
});

test("WashU JSON tracks are normalized into IGV configurations", () => {
  const payload = [
    {
      url: "https://example.org/sample.bigWig",
      type: "bigwig",
      name: "Sample signal",
      metadata: { Sample: ["Blood", "CD4 T cell"], Assay: "ATAC-seq" },
      options: { height: 70 },
    },
    { url: "https://example.org/contact.hic", type: "hic", name: "Contact map" },
    { url: "https://example.org/model.cool", type: "cool", name: "Unsupported" },
  ];
  const result = normalizeWashUHub(payload, { id: "test", name: "Test hub" });
  assert.equal(result.tracks.length, 2);
  assert.equal(result.skipped, 1);
  assert.equal(result.tracks[0].format, "bigwig");
  assert.equal(result.tracks[0].sample, "CD4 T cell");
  assert.equal(result.tracks[0].assay, "ATAC-seq");
  assert.equal(result.tracks[0].height, 70);
  assert.equal(result.tracks[0].genomeCanvasAutoColor, true);
  assert.equal(result.tracks[1].format, "hic");
  assert.equal(result.tracks[1].type, "interact");
});

test("duplicate ENCODE Hi-C names include their unique file accessions", () => {
  const payload = [
    {
      url: "https://www.encodeproject.org/files/ENCFF121YPY/@@download/ENCFF121YPY.hic",
      type: "hic",
      name: "A549 ",
      metadata: { Sample: ["cell line", "A549"], Assay: "HiC" },
    },
    {
      url: "https://www.encodeproject.org/files/ENCFF675SJE/@@download/ENCFF675SJE.hic",
      type: "hic",
      name: "A549 ",
      metadata: { Sample: ["cell line", "A549"], Assay: "HiC" },
    },
  ];
  const result = normalizeWashUHub(payload, { id: "encode-hic", name: "ENCODE Hi-C" });
  assert.deepEqual(result.tracks.map((track) => track.name), [
    "A549 · ENCFF121YPY",
    "A549 · ENCFF675SJE",
  ]);
  assert.deepEqual(result.tracks.map((track) => track.sample), ["A549", "A549"]);
  assert.deepEqual(filterHubTracks(result.tracks, "ENCFF675SJE").map((track) => track.accession), ["ENCFF675SJE"]);
});

test("nested UCSC groups flatten into searchable tracks", () => {
  const groups = [{
    label: "Regulation",
    tracks: [{ id: "dnase", name: "DNase", url: "https://example.org/dnase.bw", format: "bigwig", visible: false }],
    children: [{ label: "Transcription", tracks: [{ id: "rna", name: "RNA-seq", url: "https://example.org/rna.bw", format: "bigwig" }], children: [] }],
  }];
  const tracks = flattenUCSCHubGroups(groups, { id: "hub", name: "Hub" });
  assert.equal(tracks.length, 2);
  assert.equal(tracks[1].hubGroup, "Regulation / Transcription");
  assert.equal("visible" in tracks[0], false);
  assert.deepEqual(filterHubTracks(tracks, "transcription").map((track) => track.id), ["rna"]);
});
