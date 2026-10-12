// Canvas "chrome" palette for the genome canvas (text, axes, guides, legend
// panels, empty-heatmap background). Track feature colors never come from
// here: they are owned by track-colors.mjs and the renderer modules.

export const LIGHT_CANVAS = Object.freeze({
  dark: false,
  background: "rgb(255, 255, 255)",
  backgroundRGB: Object.freeze([255, 255, 255]),
  diagonal: "rgb(226, 229, 228)",
  guide: "rgb(205, 214, 211)",
  grid: "rgba(104, 112, 116, 0.16)",
  axisLine: "rgba(96, 104, 108, 0.34)",
  threshold: "rgba(157, 94, 99, 0.78)",
  thresholdText: "rgba(116, 72, 76, 0.95)",
  panel: "rgba(255, 255, 255, 0.93)",
  panelBorder: "rgba(49, 52, 54, 0.82)",
  panelTitle: "#202427",
  panelText: "#272b2e",
  panelMuted: "#6f7477",
  halo: "rgba(255, 255, 255, 0.94)",
  labelText: "#292d30",
  // Used by the dark adapter only.
  text: "#292d30",
  line: "#3b4252",
  liftedFeature: null,
});

export const DARK_CANVAS = Object.freeze({
  dark: true,
  background: "rgb(46, 52, 64)",
  backgroundRGB: Object.freeze([46, 52, 64]),
  diagonal: "rgb(76, 86, 106)",
  guide: "rgb(76, 86, 106)",
  grid: "rgba(216, 222, 233, 0.12)",
  axisLine: "rgba(216, 222, 233, 0.3)",
  threshold: "rgba(208, 135, 112, 0.85)",
  thresholdText: "rgba(222, 160, 140, 0.98)",
  panel: "rgba(46, 52, 64, 0.94)",
  panelBorder: "rgba(129, 140, 160, 0.7)",
  panelTitle: "#eceff4",
  panelText: "#d8dee9",
  panelMuted: "#9aa5b8",
  halo: "rgba(46, 52, 64, 0.92)",
  labelText: "#eceff4",
  text: "#d8dee9",
  line: "#a3abb9",
  // Neutral dark-gray features (e.g. RefSeq genes, rgb(69, 74, 72)) vanish on
  // a dark canvas, so dark mode draws only those in a light neutral gray.
  liftedFeature: "#c8cfdb",
});

let current = LIGHT_CANVAS;
let bypassDepth = 0;

export function canvasTheme() {
  return current;
}

export function setCanvasTheme(theme) {
  current = theme && typeof theme === "object" ? theme : LIGHT_CANVAS;
  return current;
}

// Draw renderer-owned chrome (already theme-aware) without adaptation.
export function withoutCanvasAdapter(draw) {
  bypassDepth += 1;
  try {
    return draw();
  } finally {
    bypassDepth -= 1;
  }
}

const parsedColors = new Map();

// Returns [r, g, b, a] for the normalized strings a 2D context reports
// ("#rrggbb" or "rgba(r, g, b, a)"); gradients and patterns return null.
export function parseCanvasColor(value) {
  if (typeof value !== "string") return null;
  if (parsedColors.has(value)) return parsedColors.get(value);
  let parsed = null;
  const hex = value.match(/^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i);
  if (hex) parsed = [parseInt(hex[1], 16), parseInt(hex[2], 16), parseInt(hex[3], 16), 1];
  const rgb = value.match(/^rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*(?:,\s*([\d.]+)\s*)?\)$/i);
  if (rgb) parsed = [Number(rgb[1]), Number(rgb[2]), Number(rgb[3]), rgb[4] === undefined ? 1 : Number(rgb[4])];
  if (parsedColors.size > 512) parsedColors.clear();
  parsedColors.set(value, parsed);
  return parsed;
}

// Near-neutral (gray) colors at or below the given HSL lightness.
export function isDarkNeutral(value, maximumLightness) {
  const rgb = parseCanvasColor(value);
  if (!rgb) return false;
  const high = Math.max(rgb[0], rgb[1], rgb[2]);
  const low = Math.min(rgb[0], rgb[1], rgb[2]);
  return high - low <= 24 && (high + low) / 510 <= maximumLightness;
}

function withAlpha(color, source) {
  const alpha = parseCanvasColor(source)?.[3];
  const rgb = parseCanvasColor(color);
  if (!rgb || alpha === undefined || alpha >= 1) return color;
  return `rgba(${rgb[0]}, ${rgb[1]}, ${rgb[2]}, ${alpha})`;
}

function isIdeogram(context) {
  return context.canvas?.classList?.contains("igv-ideogram-canvas") === true;
}

const sideColumnCanvases = new WeakMap();

// Axis and sample-name canvases sit beside the track viewports; IGV fills
// them with opaque white before drawing tick labels.
function isSideColumn(context) {
  const canvas = context.canvas;
  if (!canvas || typeof canvas.closest !== "function") return false;
  if (!sideColumnCanvases.has(canvas)) {
    sideColumnCanvases.set(canvas, Boolean(canvas.closest(".igv-axis-column, .igv-sample-name-column, .igv-sample-info-column")));
  }
  return sideColumnCanvases.get(canvas);
}

// IGV clears feature tracks with a white rectangle spanning the canvas.
function isFullWidthClear(context, args) {
  const canvas = context.canvas;
  const width = Number(args?.[2]);
  if (!canvas || !Number.isFinite(width) || isIdeogram(context)) return false;
  const scale = globalThis.devicePixelRatio || 1;
  return Number(args[0]) <= 0 && width * scale >= canvas.width * 0.9;
}

function isWhite(value) {
  const rgb = parseCanvasColor(value);
  return Boolean(rgb) && rgb[0] >= 250 && rgb[1] >= 250 && rgb[2] >= 250;
}

// IGV.js paints its ruler, axes, feature names and ideogram outline in
// hard-coded black. On a dark canvas, swap only those near-black neutral
// chrome colors; colored track features keep their exact colors.
export function installDarkCanvasAdapter(prototype = globalThis.CanvasRenderingContext2D?.prototype) {
  if (!prototype || prototype.__genomeCanvasDarkAdapter) return false;
  const swap = (context, property, replacement, draw) => {
    const previous = context[property];
    context[property] = withAlpha(replacement, previous);
    try {
      return draw();
    } finally {
      context[property] = previous;
    }
  };
  const wrap = (name, property, test, replacement) => {
    const native = prototype[name];
    prototype[name] = function adaptedCanvasDraw(...args) {
      if (!current.dark || bypassDepth) return native.apply(this, args);
      const style = this[property];
      const target = test(this, style, args);
      if (!target) return native.apply(this, args);
      return swap(this, property, target, () => native.apply(this, args));
    };
  };
  const textColor = (context, style) => (isDarkNeutral(style, 0.5) ? current.text : null);
  const lineColor = (context, style) => {
    // Near-black strokes are chrome (ruler ticks, axes, ideogram outline);
    // slightly lighter neutral grays are feature colors such as RefSeq genes.
    if (isDarkNeutral(style, 0.22)) return current.line;
    return current.liftedFeature && !isIdeogram(context) && isDarkNeutral(style, 0.42) ? current.liftedFeature : null;
  };
  const fillColor = (context, style, args) => {
    if (isWhite(style) && (isSideColumn(context) || isFullWidthClear(context, args))) return current.background;
    return current.liftedFeature && !isIdeogram(context) && isDarkNeutral(style, 0.42) ? current.liftedFeature : null;
  };
  wrap("fillText", "fillStyle", textColor);
  wrap("stroke", "strokeStyle", lineColor);
  wrap("strokeRect", "strokeStyle", lineColor);
  wrap("fill", "fillStyle", fillColor);
  wrap("fillRect", "fillStyle", fillColor);
  prototype.__genomeCanvasDarkAdapter = true;
  return true;
}
