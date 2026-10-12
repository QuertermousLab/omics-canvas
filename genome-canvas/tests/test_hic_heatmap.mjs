import assert from "node:assert/strict";
import test from "node:test";

import { configureHicHeatmap, installHicHeatmapRenderer } from "../hic-heatmap.mjs";

test("configures hic files as heatmap interaction tracks", () => {
  const config = configureHicHeatmap({ name: "contacts.hic", format: "hic", url: "/data/contacts.hic" });
  assert.equal(config.type, "interact");
  assert.equal(config.height, 280);
  assert.equal(config.binThreshold, -1);
  assert.equal(config.genomeCanvasHicHeatmap, true);
});

test("reads hic records and paints a triangular heatmap", async () => {
  const calls = [];
  const context = {
    fillStyle: "",
    strokeStyle: "",
    lineWidth: 1,
    save() {},
    restore() {},
    fillRect() {},
    beginPath() { calls.push(["begin"]); },
    moveTo(x, y) { calls.push(["move", x, y]); },
    lineTo(x, y) { calls.push(["line", x, y]); },
    closePath() {},
    fill() { calls.push(["fill", this.fillStyle]); },
    stroke() {},
  };
  const source = {
    hicFile: { initialized: true, bpResolutions: [1000, 5000, 10000] },
    async getRecords() { return [{ bin1: 1, bin2: 3, counts: 25 }]; },
  };
  const track = {
    type: "interact",
    format: "hic",
    config: { format: "hic", color: "rgb(190, 38, 52)" },
    featureSource: source,
    browser: { genome: { getChromosomeName: (chr) => chr } },
    draw() {},
  };

  assert.equal(installHicHeatmapRenderer(track), true);
  const features = await track.getFeatures("chr1", 0, 10000, 100);
  assert.equal(features.length, 1);
  assert.equal(features[0].binSize, 1000);
  track.draw({
    context,
    features,
    bpStart: 0,
    bpPerPixel: 100,
    pixelWidth: 200,
    pixelHeight: 100,
    pixelTop: 0,
  });
  assert.ok(calls.some(([kind, color]) => kind === "fill" && /^rgb\(/.test(color)));
  assert.ok(calls.filter(([kind]) => kind === "line").length >= 4);
});

test("svg exports embed the heatmap as one raster image instead of per-cell paths", async () => {
  const recordingContext = () => {
    const calls = [];
    return {
      calls,
      fillStyle: "",
      strokeStyle: "",
      lineWidth: 1,
      save() {}, restore() {}, scale(x, y) { calls.push(["scale", x, y]); }, fillRect() {},
      beginPath() {}, moveTo() {}, lineTo() {}, closePath() {}, stroke() {},
      fill() { calls.push(["fill"]); },
    };
  };
  const rasterContext = recordingContext();
  const created = [];
  globalThis.document = {
    createElement(tag) {
      const canvas = { tag, width: 0, height: 0, getContext: () => rasterContext };
      created.push(canvas);
      return canvas;
    },
  };
  try {
    const svgContext = { ...recordingContext(), getSerializedSvg() {}, drawImage(...args) { this.calls.push(["drawImage", ...args]); } };
    const source = {
      hicFile: { initialized: true, bpResolutions: [1000] },
      async getRecords() { return Array.from({ length: 50 }, (_, index) => ({ bin1: index, bin2: index + 2, counts: 10 + index })); },
    };
    const track = {
      type: "interact", format: "hic", config: { format: "hic" }, featureSource: source,
      browser: { genome: { getChromosomeName: (chr) => chr } }, draw() {},
    };
    installHicHeatmapRenderer(track);
    const features = await track.getFeatures("chr1", 0, 60000, 100);
    track.draw({ context: svgContext, features, bpStart: 0, bpPerPixel: 100, pixelWidth: 600, pixelHeight: 280, pixelTop: 0 });

    const images = svgContext.calls.filter(([kind]) => kind === "drawImage");
    assert.equal(images.length, 1);
    assert.equal(svgContext.calls.filter(([kind]) => kind === "fill").length, 0);
    const [, canvas, x, y, width, height] = images[0];
    assert.deepEqual([x, y, width, height], [0, 0, 600, 280]);
    assert.deepEqual([canvas.width, canvas.height], [1200, 560]);
    assert.ok(rasterContext.calls.filter(([kind]) => kind === "fill").length >= 40);
  } finally {
    delete globalThis.document;
  }
});
