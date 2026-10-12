import assert from "node:assert/strict";
import test from "node:test";

import {
  DEFAULT_HIGHLIGHT_COLOR,
  genomicExtentFromPixels,
  hexToRgba,
  normalizeHighlights,
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
