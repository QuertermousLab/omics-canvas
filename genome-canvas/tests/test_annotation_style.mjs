import assert from "node:assert/strict";
import test from "node:test";

import {
  REFSEQ_ALL_COLOR,
  REFSEQ_ARROW_SPACING,
  REFSEQ_LABEL_COLOR,
  REFSEQ_LINE_WIDTH,
  installRefSeqAllStyle,
  isRefSeqAllTrack,
} from "../annotation-style.mjs";

test("recognizes only the built-in RefSeq All annotation", () => {
  assert.equal(isRefSeqAllTrack({ id: "refseqAll", name: "Refseq All" }), true);
  assert.equal(isRefSeqAllTrack({ name: "Custom RefSeq genes" }), false);
});

test("uses deep gray features and black labels at one and a half times the font size", () => {
  const calls = [];
  const context = {
    font: "normal 10px Arial",
    fillStyle: "rgb(20, 30, 40)",
    strokeStyle: "rgb(20, 30, 40)",
    measureText(text) {
      calls.push(["measure", text, this.font]);
      return { width: 50, actualBoundingBoxAscent: 8, actualBoundingBoxDescent: 2 };
    },
    fillText(text) {
      calls.push(["fill", text, this.font, this.fillStyle]);
    },
  };
  const originalFillText = context.fillText;
  const track = {
    id: "refseqAll",
    name: "Refseq All",
    config: {},
    render(feature, _start, _scale, _height, drawingContext) {
      drawingContext.measureText(feature.name);
      drawingContext.fillStyle = this.color;
      drawingContext.fillText(feature.name, 10, 20);
    },
  };

  assert.equal(installRefSeqAllStyle(track), true);
  assert.equal(track.color, REFSEQ_ALL_COLOR);
  assert.equal(track.arrowSpacing, REFSEQ_ARROW_SPACING);
  track.render({ name: "SMAD3" }, 0, 1, 50, context, { drawLabel: true });
  assert.deepEqual(calls[0], ["measure", "SMAD3", "normal 15px Arial"]);
  assert.deepEqual(calls[1], ["fill", "SMAD3", "normal 15px Arial", REFSEQ_LABEL_COLOR]);
  assert.equal(context.fillText, originalFillText);
});

test("thickens the gene backbone and directional arrow strokes", () => {
  const calls = [];
  const context = {
    lineWidth: 1,
    lineCap: "butt",
    lineJoin: "miter",
    stroke() {
      calls.push([this.lineWidth, this.lineCap, this.lineJoin]);
    },
  };
  const originalStroke = context.stroke;
  const track = {
    id: "refseqAll",
    config: {},
    render(_feature, _start, _scale, _height, drawingContext) {
      drawingContext.stroke();
      drawingContext.stroke();
    },
  };

  installRefSeqAllStyle(track);
  track.render({ name: "SMAD3", strand: "+" }, 0, 1, 50, context, { drawLabel: false });
  assert.deepEqual(calls, [
    [REFSEQ_LINE_WIDTH, "round", "round"],
    [REFSEQ_LINE_WIDTH, "round", "round"],
  ]);
  assert.equal(context.stroke, originalStroke);
  assert.equal(context.lineWidth, 1);
});
