import assert from "node:assert/strict";
import test from "node:test";
import { normalizeReferenceResources } from "../reference-resources.mjs";

const baseURL = "http://server/genome-canvas/";
const config = {
  referenceResources: { "https://public/hg38.2bit": "/reference/hg38.2bit" },
  indexedReferenceTracks: {
    "https://public/refgene.gz": {
      url: "/reference/refgene.gz", indexURL: "/reference/refgene.gz.tbi", indexed: true,
    },
  },
};

test("saved RefSeq tracks use the regional index and retain custom settings", () => {
  const original = { url: "https://public/refgene.gz", indexed: false, height: 120, color: "gray", name: "My genes" };
  const result = normalizeReferenceResources(original, config, baseURL);
  assert.equal(result.url, `${baseURL}reference/refgene.gz`);
  assert.equal(result.indexURL, `${baseURL}reference/refgene.gz.tbi`);
  assert.equal(result.indexed, true);
  assert.equal(result.height, 120);
  assert.equal(result.name, "My genes");
  assert.equal(original.indexed, false);
});

test("cached 2bit overrides legacy FASTA while custom references stay intact", () => {
  const result = normalizeReferenceResources({
    id: "hg38", twoBitURL: "https://public/hg38.2bit", fastaURL: "https://public/old.fa",
    indexURL: "https://public/old.fa.fai", tracks: [{ url: "https://public/refgene.gz" }],
  }, config, baseURL);
  assert.equal(result.twoBitURL, `${baseURL}reference/hg38.2bit`);
  assert.equal(result.fastaURL, undefined);
  assert.equal(result.tracks[0].indexed, true);
  const custom = { id: "hg38", fastaURL: "https://custom/genome.fa", indexURL: "https://custom/genome.fa.fai" };
  assert.deepEqual(normalizeReferenceResources(custom, config, baseURL), custom);
});

test("uncached assemblies and null server configuration retain upstream URLs", () => {
  const original = { twoBitURL: "https://public/mm10.2bit" };
  assert.deepEqual(normalizeReferenceResources(original, null, baseURL), original);
});
