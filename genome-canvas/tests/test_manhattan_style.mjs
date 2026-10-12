import assert from "node:assert/strict";
import test from "node:test";

import {
  GENOME_WIDE_THRESHOLD,
  LD_COLORS,
  SIGNIFICANCE_P_VALUE,
  installManhattanRenderer,
  manhattanPointStyle,
  manhattanShape,
  manhattanValue,
  normalizedManhattanRange,
} from "../manhattan-style.mjs";

test("converts GWAS p values to -log10 values", () => {
  assert.equal(manhattanValue({ valueProperty: "value" }, { value: 1e-8 }), 8);
  assert.ok(Number.isNaN(manhattanValue({ valueProperty: "value" }, { value: 0 })));
});

test("uses the corrected 5e-8 genome-wide significance threshold", () => {
  assert.equal(SIGNIFICANCE_P_VALUE, 5e-8);
  assert.equal(GENOME_WIDE_THRESHOLD, -Math.log10(5e-8));
  assert.ok(Math.abs(GENOME_WIDE_THRESHOLD - 7.30103) < 0.00001);
  const range = normalizedManhattanRange({ min: 2, max: 4 });
  assert.equal(range.min, 0);
  assert.ok(range.max > GENOME_WIDE_THRESHOLD);
});

test("maps real LD r squared bins to the LocusZoom-style palette", () => {
  assert.equal(manhattanPointStyle(0.95).fill, LD_COLORS.high);
  assert.equal(manhattanPointStyle(0.75).fill, LD_COLORS.mediumHigh);
  assert.equal(manhattanPointStyle(0.55).fill, LD_COLORS.medium);
  assert.equal(manhattanPointStyle(0.35).fill, LD_COLORS.lowMedium);
  assert.equal(manhattanPointStyle(0.1).fill, LD_COLORS.low);
  assert.equal(manhattanPointStyle(Number.NaN).fill, LD_COLORS.missing);
  assert.equal(manhattanPointStyle(1, true).fill, LD_COLORS.reference);
});

test("uses beta direction for point shapes and a diamond for the LD reference", () => {
  const feature = (beta) => ({ line: `chr6\t1\trs1\t1e-9\t9\tA\tG\t${beta}`, columns: ["chromosome", "position", "rsid", "pvalue", "neg_log_pvalue", "ref", "alt", "beta"] });
  assert.equal(manhattanShape(feature("0.2")), "triangle-up");
  assert.equal(manhattanShape(feature("-0.2")), "triangle-down");
  assert.equal(manhattanShape(feature(".")), "circle");
  assert.equal(manhattanShape(feature("0.2"), true), "diamond");
});

test("installs only on GWAS tracks", () => {
  const gwas = { type: "gwas", config: {}, dataRange: { min: 1, max: 2 }, draw() {} };
  assert.equal(installManhattanRenderer(gwas), true);
  assert.equal(gwas.__genomeCanvasManhattanRenderer, true);
  assert.ok(gwas.dataRange.max > GENOME_WIDE_THRESHOLD);
  assert.equal(installManhattanRenderer(gwas), false);
  assert.equal(installManhattanRenderer({ type: "wig", config: {}, draw() {} }), false);
});

function renderAssociationRecords(records) {
  const labels = [];
  let points = 0;
  const context = new Proxy({
    fillText(text) { labels.push(text); },
    fill() { points += 1; },
  }, { get(target, key) { return key in target ? target[key] : () => {}; } });
  const features = records.map(([name, pvalue, role], index) => ({
    chr: "chr1", start: 20 + index * 30, value: pvalue,
    columns: ["rsid", "record_type"], line: `${name}\t${role}`,
  }));
  const track = { type: "gwas", config: {}, dataRange: { min: 0, max: 30 }, draw() {} };
  installManhattanRenderer(track);
  track.draw({ context, features, pixelWidth: 200, pixelHeight: 220, bpStart: 0, bpPerPixel: 1 });
  return { labels, points, features };
}

test("draws proxy points but labels the lead even when a proxy has a smaller P value", () => {
  const result = renderAssociationRecords([["rsLead", 1e-10, "lead"], ["rsProxy", 1e-20, "proxy"]]);
  assert.equal(result.points, 2);
  assert.ok(result.labels.includes("rsLead"));
  assert.ok(!result.labels.includes("rsProxy"));
  assert.ok(result.features.every(feature => Number.isFinite(feature.px) && Number.isFinite(feature.py)));
});

test("draws proxy-only regions without a variant text label", () => {
  const result = renderAssociationRecords([["rsProxy", 1e-20, "proxy"]]);
  assert.equal(result.points, 1);
  assert.ok(!result.labels.includes("rsProxy"));
});

test("retains strongest-point labeling for tracks without record types", () => {
  const result = renderAssociationRecords([["rsWeak", 1e-10, ""], ["rsStrong", 1e-20, ""]]);
  assert.equal(result.points, 2);
  assert.ok(result.labels.includes("rsStrong"));
  assert.ok(!result.labels.includes("rsWeak"));
});

function ldFeature(position, r2 = ".", reference = "0", population = "1000 Genomes Phase 3 EUR") {
  return {
    value: position === 100 ? 1e-12 : 1e-20,
    columns: ["hg19_locus", "ref", "alt", "ld_r2", "ld_reference", "ld_reference_locus", "ld_population", "ld_method", "ld_is_reference"],
    line: `9:${position}\tA\tG\t${r2}\trsFixed\t9:100\t${population}\tunphased dosage r2\t${reference}`,
    popupData() { return []; },
  };
}

test("reads embedded LD without an endpoint and preserves the fixed reference and population", async () => {
  const features = [ldFeature(100, "1", "1"), ldFeature(125, "0.625"), ldFeature(150)];
  const track = { type: "gwas", config: {}, draw() {}, async getFeatures() { return features; } };
  installManhattanRenderer(track);
  assert.equal(await track.getFeatures(), features);
  assert.equal(track.__genomeCanvasLDStatus.source, "embedded");
  assert.equal(track.__genomeCanvasLDStatus.label, "1000 Genomes Phase 3 EUR");
  assert.equal(features[0].genomeCanvasIsLDReference, true);
  assert.equal(features[1].genomeCanvasIsLDReference, false);
  assert.ok(features[1].popupData().some(item => item.name === "LD reference" && item.value === "rsFixed"));
  assert.ok(features[1].popupData().some(item => item.name.includes("EUR") && item.value === "0.625"));
  assert.ok(features[2].popupData().some(item => item.value === "Not available"));
});

test("does not fetch LD for annotated missing values or invalid r squared values", async (t) => {
  t.mock.method(globalThis, "fetch", () => { throw new Error("Embedded LD must not call the server"); });
  const features = [ldFeature(100, "."), ldFeature(125, "1.2"), ldFeature(150, "-0.2")];
  const track = { type: "gwas", config: { ldEndpoint: "api/gwas-ld" }, draw() {}, async getFeatures() { return features; } };
  installManhattanRenderer(track);
  await track.getFeatures();
  assert.equal(track.__genomeCanvasLDStatus.available, false);
  assert.equal(globalThis.fetch.mock.callCount(), 0);
});

test("recalculates dynamic LD when a reused feature set gets a new lead", async (t) => {
  let calls = 0;
  t.mock.method(globalThis, "fetch", async () => {
    calls += 1;
    const position = calls === 1 ? 100 : 125;
    return { ok: true, async json() { return {
      available: true, label: "Test panel", reference: { position, ref: "A", alt: "G", id: `rs${position}` },
      variants: [{ position: 100, ref: "A", alt: "G", r2: calls === 1 ? 1 : 0.2 },
        { position: 125, ref: "A", alt: "G", r2: calls === 1 ? 0.2 : 1 }],
    }; } };
  });
  const features = [100, 125].map(position => ({
    value: position === 100 ? 1e-20 : 1e-10, columns: ["hg19_locus", "ref", "alt"], line: `9:${position}\tA\tG`,
  }));
  const track = { type: "gwas", config: { ldEndpoint: "api/gwas-ld" }, draw() {}, async getFeatures() { return features; } };
  installManhattanRenderer(track);
  await track.getFeatures();
  assert.equal(features[0].genomeCanvasIsLDReference, true);
  features[1].value = 1e-30;
  await track.getFeatures();
  assert.equal(calls, 2);
  assert.equal(features[0].genomeCanvasR2, 0.2);
  assert.equal(features[0].genomeCanvasIsLDReference, undefined);
  assert.equal(features[1].genomeCanvasIsLDReference, true);
});

test("does not assign LD to a different allele at the same position", async (t) => {
  t.mock.method(globalThis, "fetch", async () => ({ ok: true, async json() { return {
    available: true, reference: { position: 100, ref: "A", alt: "G", id: "rsFixed" },
    variants: [{ position: 100, ref: "A", alt: "G", r2: 1 }, { position: 125, ref: "A", alt: "C", r2: 0.8 }],
  }; } }));
  const features = [100, 125].map(position => ({
    value: 1e-10, columns: ["hg19_locus", "ref", "alt"], line: `9:${position}\tA\tG`,
  }));
  const track = { type: "gwas", config: { ldEndpoint: "api/gwas-ld" }, draw() {}, async getFeatures() { return features; } };
  installManhattanRenderer(track);
  await track.getFeatures();
  assert.equal(features[1].genomeCanvasR2, undefined);
});

test("preserves the right-aligned LD legend in SVG and PNG exports", () => {
  const translations = [];
  const rectangles = [];
  const context = new Proxy({
    getSerializedSvg() {},
    translate(x, y) { translations.push([x, y]); },
    fillRect(...args) { rectangles.push(args); },
  }, { get(target, key) { return key in target ? target[key] : () => {}; } });
  const track = { type: "gwas", config: {}, draw() {} };
  installManhattanRenderer(track);
  track.draw({ context, features: [], pixelWidth: 300, viewportWidth: 300, pixelXOffset: 0, contentTop: 30,
    viewport: { viewportElement: {} }, pixelHeight: 220, bpStart: 0, bpPerPixel: 1 });
  assert.ok(translations.some(([x, y]) => x === 0 && y === 30));
  assert.ok(rectangles.some(([x, y, width, height]) => x === 178 && y === 8 && width === 114 && height === 192));
});

function legendViewport() {
  const labels = [];
  let observer;
  const context = new Proxy({
    getSerializedSvg: undefined,
    fillText(text) { labels.push(text); },
  }, { get(target, key) { return key in target ? target[key] : () => {}; } });
  const children = [];
  const element = {
    clientWidth: 300,
    appendChild(child) { children.push(child); },
    ownerDocument: {
      defaultView: {
        devicePixelRatio: 2,
        ResizeObserver: class {
          constructor(callback) { this.callback = callback; observer = this; }
          observe(target) { this.target = target; }
          disconnect() { this.disconnected = true; }
        },
      },
      createElement(tag) {
        assert.equal(tag, "canvas");
        return { style: {}, setAttribute() {}, getContext() { return context; }, remove() { this.removed = true; } };
      },
    },
  };
  const viewport = { viewportElement: element, dispose() { this.disposed = true; } };
  const track = { type: "gwas", config: {}, draw() {}, __genomeCanvasLDStatus: { available: true, label: "Test panel" } };
  installManhattanRenderer(track);
  const draw = (offset = -300) => track.draw({ context, features: [], viewport,
    pixelWidth: 900, viewportWidth: element.clientWidth, pixelXOffset: offset,
    pixelHeight: 220, bpStart: 0, bpPerPixel: 1 });
  return { track, viewport, element, children, labels, draw, get observer() { return observer; } };
}

test("anchors one LD overlay to the viewport instead of the panning plot canvas", () => {
  const fixture = legendViewport();
  fixture.draw();
  const canvas = fixture.children[0];
  assert.equal(canvas.className, "genome-canvas-ld-legend");
  assert.equal(canvas.style.position, "absolute");
  assert.equal(canvas.style.right, "0");
  assert.equal(canvas.style.left, undefined);
  assert.equal(canvas.style.pointerEvents, "none");
  assert.equal(canvas.width, 244);
  assert.equal(canvas.height, 400);
  fixture.draw(-180);
  assert.equal(fixture.children.length, 1);
  assert.equal(canvas.style.right, "0");
  assert.equal(canvas.style.left, undefined);
});

test("refreshes overlay status and hides the legend in narrow viewports", () => {
  const fixture = legendViewport();
  fixture.draw();
  assert.ok(fixture.labels.includes("Test panel"));
  fixture.element.clientWidth = 190;
  fixture.observer.callback();
  assert.equal(fixture.children[0].style.display, "none");
  fixture.element.clientWidth = 450;
  fixture.track.__genomeCanvasLDStatus = { available: false };
  fixture.observer.callback();
  assert.equal(fixture.children[0].style.display, "block");
  assert.ok(fixture.labels.includes("LD unavailable"));
});

test("cleans up the legend and resize observer when a viewport is removed", () => {
  const fixture = legendViewport();
  fixture.draw();
  fixture.viewport.dispose();
  assert.equal(fixture.viewport.disposed, true);
  assert.equal(fixture.children[0].removed, true);
  assert.equal(fixture.observer.disconnected, true);
});

test("ignores missing metadata markers in genome-wide LD regions", async () => {
  const features = [ldFeature(100, "1", "1"), {
    value: 1e-2,
    columns: ["ld_r2", "ld_population", "ld_reference", "ld_reference_locus", "ld_method", "ld_is_reference", "ld_locus"],
    line: ".\t.\t.\t.\t.\t0\t.",
    popupData() { return []; },
  }];
  const track = { type: "gwas", config: { ldLabel: "Test ALL" }, draw() {}, async getFeatures() { return features; } };
  installManhattanRenderer(track);
  await track.getFeatures();
  assert.equal(track.__genomeCanvasLDStatus.label, "1000 Genomes Phase 3 EUR");
  assert.deepEqual(track.__genomeCanvasLDStatus.referenceIds, ["rsFixed"]);
  assert.equal(features[1].genomeCanvasLDId, undefined);
  assert.ok(!features[1].popupData().some(item => item.name === "LD reference"));
  features.shift();
  await track.getFeatures();
  assert.equal(track.__genomeCanvasLDStatus.label, "Test ALL");
  assert.equal(track.__genomeCanvasLDStatus.reason, "Outside precomputed LD loci");
});
