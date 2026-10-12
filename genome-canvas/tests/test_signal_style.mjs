import assert from "node:assert/strict";
import test from "node:test";

import { installGradientSignalRenderer } from "../signal-style.mjs";

function mockContext() {
  const calls = [];
  return {
    calls,
    createLinearGradient() {
      const gradient = { stops: [], addColorStop(offset, color) { this.stops.push([offset, color]); } };
      calls.push(["gradient", gradient]);
      return gradient;
    },
    save() {}, restore() {}, beginPath() {}, closePath() {}, moveTo() {}, lineTo() {}, setLineDash() {},
    quadraticCurveTo() { calls.push(["quadraticCurveTo"]); },
    fill() { calls.push(["fill", this.fillStyle]); },
    stroke() { calls.push(["stroke", this.strokeStyle]); },
  };
}

function signalTrack() {
  return {
    type: "wig",
    graphType: "bar",
    color: "rgb(24, 162, 141)",
    config: { format: "bigwig" },
    dataRange: { min: 0, max: 10 },
    logScale: false,
    flipAxis: false,
    getScaleFactor(minimum, maximum, height) { return height / (maximum - minimum); },
    computeYPixelValue(value, scale) { return (this.dataRange.max - value) * scale; },
    draw() { this.originalDrawCalled = true; },
  };
}

test("installs a translucent gradient area renderer for signal tracks", () => {
  const track = signalTrack();
  const context = mockContext();
  assert.equal(installGradientSignalRenderer(track), true);
  track.draw({
    context,
    features: [{ start: 0, end: 10, value: 2 }, { start: 10, end: 20, value: 8 }],
    bpStart: 0,
    bpPerPixel: 1,
    pixelWidth: 100,
    pixelHeight: 80,
  });
  const gradient = context.calls.find(([kind]) => kind === "gradient")[1];
  assert.equal(gradient.stops.length, 3);
  assert.ok(gradient.stops.every(([, color]) => color.startsWith("rgba(")));
  assert.ok(context.calls.some(([kind]) => kind === "fill"));
  assert.ok(context.calls.some(([kind]) => kind === "stroke"));
  assert.ok(context.calls.some(([kind]) => kind === "quadraticCurveTo"));
});

test("keeps IGV's original renderer for other graph types", () => {
  const track = signalTrack();
  installGradientSignalRenderer(track);
  track.graphType = "points";
  track.draw({ features: [] });
  assert.equal(track.originalDrawCalled, true);
});

test("does not patch non-signal tracks", () => {
  const track = { type: "annotation", config: { format: "bed" }, draw() {} };
  assert.equal(installGradientSignalRenderer(track), false);
});
