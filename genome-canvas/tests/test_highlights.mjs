import assert from "node:assert/strict";
import test from "node:test";

import {
  DEFAULT_HIGHLIGHT_COLOR,
  genomicExtentFromPixels,
  hexToRgba,
  linkedTrack,
  linkedView,
  normalizeHighlights,
  parseRegion,
  visibleHighlightPixels,
} from "../highlights.mjs";

test("uses a warning-yellow translucent highlight palette", () => {
  assert.equal(DEFAULT_HIGHLIGHT_COLOR, "#f2c94c");
  assert.equal(hexToRgba(DEFAULT_HIGHLIGHT_COLOR, 0.14), "rgba(242, 201, 76, 0.14)");
});

test("converts a reverse mouse drag into a genomic interval", () => {
  const extent = genomicExtentFromPixels({ chr: "chr8", start: 1000, bpPerPixel: 5 }, 80, 20, 100);
  assert.deepEqual(extent, { chr: "chr8", start: 1100, end: 1400 });
  assert.equal(genomicExtentFromPixels({ chr: "chr8", start: 0, bpPerPixel: 1 }, 10, 11, 100), null);
});

test("clips highlights to the visible reference frame", () => {
  const pixels = visibleHighlightPixels(
    { chr: "chr8", start: 900, end: 1250 },
    { chr: "chr8", start: 1000, bpPerPixel: 5 },
    100,
  );
  assert.deepEqual(pixels, { left: 0, width: 50 });
  assert.equal(visibleHighlightPixels({ chr: "chr1", start: 0, end: 10 }, { chr: "chr8", start: 0, bpPerPixel: 1 }, 100), null);
});

test("restored highlights are validated and normalized", () => {
  assert.deepEqual(normalizeHighlights([
    { chr: "chr1", start: 10.8, end: 20.1, color: "#FC3" },
    { chr: "chr1", start: 30, end: 20 },
  ]), [{ id: "highlight-0", chr: "chr1", start: 10, end: 21, color: "#ffcc33" }]);
});

test("link parameters open a locus with highlighted regions", () => {
  assert.deepEqual(parseRegion("chr8:127,728,001-127,742,000"), { chr: "chr8", start: 127728000, end: 127742000 });
  assert.equal(parseRegion("MYC"), null);
  assert.equal(parseRegion("chr1:20-10"), null);
  const view = linkedView("?locus=chr8:127000000-128000000&genome=hg38&highlight=chr8:101-200;chr8:301-400&color=5f8a6a");
  assert.equal(view.locus, "chr8:127000000-128000000");
  assert.equal(view.genome, "hg38");
  assert.deepEqual(view.highlights.map(({ chr, start, end, color }) => ({ chr, start, end, color })), [
    { chr: "chr8", start: 100, end: 200, color: "#5f8a6a" },
    { chr: "chr8", start: 300, end: 400, color: "#5f8a6a" },
  ]);
  assert.deepEqual(linkedView(""), { locus: null, genome: null, highlights: [], tracks: [] });
  assert.equal(linkedView("?highlight=chr1:1-10").highlights[0].color, DEFAULT_HIGHLIGHT_COLOR);
});

test("linked tracks carry a name and load enhancer links as proportional arcs", () => {
  assert.deepEqual(linkedTrack("Screen · significant|/hub/api/arcs/screen/significant.bedpe"), {
    name: "Screen · significant", url: "/hub/api/arcs/screen/significant.bedpe", format: "bedpe", type: "interact", arcType: "proportional",
  });
  assert.deepEqual(linkedTrack("https://example.org/data/signal.bw"), { name: "signal.bw", url: "https://example.org/data/signal.bw" });
  assert.equal(linkedTrack("javascript:alert(1)"), null);
  assert.equal(linkedTrack("name|file:///etc/passwd"), null);
  const view = linkedView("?locus=MYC&track=A|/a.bedpe&track=/b.bw");
  assert.deepEqual(view.tracks.map((track) => track.name), ["A", "b.bw"]);
});
