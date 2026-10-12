import test from "node:test";
import assert from "node:assert/strict";
import {
  DARK_CANVAS,
  LIGHT_CANVAS,
  installDarkCanvasAdapter,
  isDarkNeutral,
  parseCanvasColor,
  setCanvasTheme,
  withoutCanvasAdapter,
} from "../canvas-theme.mjs";

class FakeContext {
  constructor(canvas = {}) {
    this.canvas = { width: 300, classList: { contains: () => false }, closest: () => null, ...canvas };
    this.fillStyle = "#000000";
    this.strokeStyle = "#000000";
    this.calls = [];
  }
  fillText() { this.calls.push(["fillText", this.fillStyle]); }
  stroke() { this.calls.push(["stroke", this.strokeStyle]); }
  strokeRect() { this.calls.push(["strokeRect", this.strokeStyle]); }
  fill() { this.calls.push(["fill", this.fillStyle]); }
  fillRect() { this.calls.push(["fillRect", this.fillStyle]); }
}
installDarkCanvasAdapter(FakeContext.prototype);

test.afterEach(() => setCanvasTheme(LIGHT_CANVAS));

test("parses the normalized color strings a 2D context reports", () => {
  assert.deepEqual(parseCanvasColor("#454a48"), [69, 74, 72, 1]);
  assert.deepEqual(parseCanvasColor("rgba(10, 20, 30, 0.5)"), [10, 20, 30, 0.5]);
  assert.equal(parseCanvasColor({ gradient: true }), null);
});

test("only near-neutral dark colors count as chrome", () => {
  assert.equal(isDarkNeutral("#000000", 0.22), true);
  assert.equal(isDarkNeutral("#454a48", 0.22), false);
  assert.equal(isDarkNeutral("#454a48", 0.42), true);
  assert.equal(isDarkNeutral("rgb(130, 100, 230)", 0.9), false);
});

test("light theme draws every color unchanged", () => {
  const context = new FakeContext();
  context.fillText("label", 0, 0);
  context.fillStyle = "#ffffff";
  context.fillRect(0, 0, 300, 40);
  assert.deepEqual(context.calls, [["fillText", "#000000"], ["fillRect", "#ffffff"]]);
});

test("dark theme swaps black chrome but keeps colored features and restores styles", () => {
  setCanvasTheme(DARK_CANVAS);
  const context = new FakeContext();
  context.fillText("127,730 kb", 0, 0);
  context.stroke();
  context.fillStyle = "#8264e6";
  context.fillRect(10, 0, 20, 20);
  assert.deepEqual(context.calls, [
    ["fillText", DARK_CANVAS.text],
    ["stroke", DARK_CANVAS.line],
    ["fillRect", "#8264e6"],
  ]);
  assert.equal(context.strokeStyle, "#000000");
});

test("dark theme clears full-width white backgrounds and lifts neutral gene features", () => {
  setCanvasTheme(DARK_CANVAS);
  const context = new FakeContext();
  context.fillStyle = "#ffffff";
  context.fillRect(0, 0, 300, 40);
  context.fillRect(5, 0, 12, 12);
  context.fillStyle = "#454a48";
  context.fillRect(20, 0, 30, 8);
  assert.deepEqual(context.calls.map((call) => call[1]), [DARK_CANVAS.background, "#ffffff", DARK_CANVAS.liftedFeature]);
});

test("ideogram bands and renderer-owned chrome are never adapted", () => {
  setCanvasTheme(DARK_CANVAS);
  const ideogram = new FakeContext({ classList: { contains: (name) => name === "igv-ideogram-canvas" } });
  ideogram.fillStyle = "#000000";
  ideogram.fillRect(0, 0, 300, 10);
  assert.deepEqual(ideogram.calls, [["fillRect", "#000000"]]);
  const context = new FakeContext();
  withoutCanvasAdapter(() => context.fillText("LD", 0, 0));
  assert.deepEqual(context.calls, [["fillText", "#000000"]]);
});
