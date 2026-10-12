import { canvasTheme } from "./canvas-theme.mjs?v=20261007.2";

function clamp(value, minimum, maximum) {
  return Math.max(minimum, Math.min(maximum, value));
}

function rgba(color, alpha) {
  const rgb = String(color || "").match(/^rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)/i);
  if (rgb) return `rgba(${rgb[1]}, ${rgb[2]}, ${rgb[3]}, ${clamp(alpha, 0, 1)})`;

  const hex = String(color || "").match(/^#([0-9a-f]{3}|[0-9a-f]{6})$/i);
  if (hex) {
    const value = hex[1].length === 3
      ? [...hex[1]].map((part) => part + part).join("")
      : hex[1];
    return `rgba(${Number.parseInt(value.slice(0, 2), 16)}, ${Number.parseInt(value.slice(2, 4), 16)}, ${Number.parseInt(value.slice(4, 6), 16)}, ${clamp(alpha, 0, 1)})`;
  }
  return color || "rgb(90, 120, 112)";
}

function isSignalTrack(track) {
  const format = String(track?.format || track?.config?.format || "").toLowerCase();
  return track?.type === "wig" || ["bigwig", "bw", "wig", "bedgraph", "tdf"].includes(format);
}

function groupVisibleFeatures(track, options, yScale, baseline) {
  const groups = [];
  let current;
  let previousEnd = Number.NEGATIVE_INFINITY;
  let previousWidth = 1;
  const bpEnd = options.bpStart + options.pixelWidth * options.bpPerPixel + 1;

  for (const feature of options.features || []) {
    if (feature.end < options.bpStart) continue;
    if (feature.start > bpEnd) break;

    const x0 = (feature.start - options.bpStart) / options.bpPerPixel;
    const x1 = Math.max(x0 + 0.5, (feature.end - options.bpStart) / options.bpPerPixel);
    const y = clamp(yScale(feature.value), 0, options.pixelHeight - 1);
    if (![x0, x1, y].every(Number.isFinite)) continue;

    const sign = feature.value < 0 ? -1 : 1;
    const gapLimit = Math.max(3, previousWidth * 2.5);
    if (!current || current.sign !== sign || x0 - previousEnd > gapLimit) {
      current = { sign, baseline, points: [] };
      groups.push(current);
    }
    current.points.push({ x0, x1, y });
    previousEnd = x1;
    previousWidth = Math.max(0.5, x1 - x0);
  }
  return groups;
}

function smoothPoints(group) {
  const points = group.points.map((point) => ({
    x: (point.x0 + point.x1) / 2,
    y: point.y,
  }));
  if (!points.length) return points;
  points.unshift({ x: group.points[0].x0, y: group.points[0].y });
  points.push({ x: group.points[group.points.length - 1].x1, y: group.points[group.points.length - 1].y });
  return points;
}

function continueSmoothCurve(context, points) {
  if (points.length < 2) return;
  if (points.length === 2) {
    context.lineTo(points[1].x, points[1].y);
    return;
  }

  for (let index = 1; index < points.length - 1; index += 1) {
    const current = points[index];
    const next = points[index + 1];
    const midpointX = (current.x + next.x) / 2;
    const midpointY = (current.y + next.y) / 2;
    context.quadraticCurveTo(current.x, current.y, midpointX, midpointY);
  }
  const penultimate = points[points.length - 2];
  const last = points[points.length - 1];
  context.quadraticCurveTo(penultimate.x, penultimate.y, last.x, last.y);
}

function paintAreaGroup(context, group, color, pixelHeight, alpha, fillOpacity) {
  if (!group.points.length) return;
  const points = smoothPoints(group);
  const first = points[0];
  const last = points[points.length - 1];
  const gradient = context.createLinearGradient(0, 0, 0, pixelHeight);

  if (group.sign > 0) {
    gradient.addColorStop(0, rgba(color, fillOpacity * alpha));
    gradient.addColorStop(0.62, rgba(color, fillOpacity * 0.58 * alpha));
    gradient.addColorStop(1, rgba(color, fillOpacity * 0.18 * alpha));
  } else {
    gradient.addColorStop(0, rgba(color, fillOpacity * 0.18 * alpha));
    gradient.addColorStop(0.38, rgba(color, fillOpacity * 0.58 * alpha));
    gradient.addColorStop(1, rgba(color, fillOpacity * alpha));
  }

  context.save();
  context.beginPath();
  context.moveTo(first.x, group.baseline);
  context.lineTo(first.x, first.y);
  continueSmoothCurve(context, points);
  context.lineTo(last.x, group.baseline);
  context.closePath();
  context.fillStyle = gradient;
  context.fill();

  context.beginPath();
  context.moveTo(first.x, first.y);
  continueSmoothCurve(context, points);
  context.strokeStyle = rgba(color, alpha);
  context.lineWidth = 1.75;
  context.lineJoin = "round";
  context.lineCap = "round";
  context.stroke();
  context.restore();
}

function paintGuideLines(track, options, yScale) {
  const context = options.context;
  for (const line of track.config?.guideLines || []) {
    if (!Number.isFinite(line.y)) continue;
    context.save();
    context.beginPath();
    context.moveTo(0, yScale(line.y));
    context.lineTo(options.pixelWidth, yScale(line.y));
    context.strokeStyle = line.color || canvasTheme().guide;
    context.lineWidth = 1;
    context.setLineDash?.(line.dotted ? [4, 5] : []);
    context.stroke();
    context.restore();
  }
}

function drawGradientSignal(track, options) {
  const pixelHeight = options.pixelHeight - 1;
  const minimum = track.dataRange?.min;
  const maximum = track.dataRange?.max;
  if (!Number.isFinite(minimum) || !Number.isFinite(maximum) || maximum <= minimum) return;

  const scaleFactor = track.getScaleFactor(minimum, maximum, pixelHeight, track.logScale);
  const yScale = (value) => track.logScale
    ? track.computeYPixelValueInLogScale(value, scaleFactor)
    : track.computeYPixelValue(value, scaleFactor);
  const baseline = clamp(yScale(0), 0, pixelHeight);
  const groups = groupVisibleFeatures(track, options, yScale, baseline);
  const alpha = Number.isFinite(options.alpha) ? options.alpha : 1;
  const fillOpacity = clamp(Number(track.config?.gradientFillOpacity) || 0.62, 0.08, 0.9);
  const positiveColor = track.color || track.config?.color || "rgb(30, 158, 140)";
  const negativeColor = track.altColor || track.config?.altColor || positiveColor;

  for (const group of groups) {
    paintAreaGroup(options.context, group, group.sign < 0 ? negativeColor : positiveColor, pixelHeight, alpha, fillOpacity);
  }

  if (minimum < 0) {
    const context = options.context;
    context.save();
    context.beginPath();
    context.moveTo(0, baseline);
    context.lineTo(options.pixelWidth, baseline);
    context.strokeStyle = track.baselineColor || canvasTheme().guide;
    context.lineWidth = 1;
    context.stroke();
    context.restore();
  }
  paintGuideLines(track, options, yScale);
}

export function installGradientSignalRenderer(track) {
  if (!isSignalTrack(track) || track.__genomeCanvasGradientRenderer) return false;
  const originalDraw = track.draw;
  if (typeof originalDraw !== "function") return false;

  track.draw = function draw(options) {
    if (this.graphType !== "bar" || !options.features?.length) {
      return originalDraw.call(this, options);
    }
    return drawGradientSignal(this, options);
  };
  track.__genomeCanvasGradientRenderer = true;
  track.config.genomeCanvasGradient = true;
  return true;
}
