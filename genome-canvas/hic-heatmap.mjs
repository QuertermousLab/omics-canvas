import { canvasTheme, withoutCanvasAdapter } from "./canvas-theme.mjs?v=20261007.2";

const DEFAULT_HEATMAP_COLOR = "rgb(190, 38, 52)";

function clamp(value, minimum, maximum) {
  return Math.max(minimum, Math.min(maximum, value));
}

function formatForTrack(track) {
  return String(track?.format || track?.config?.format || "").toLowerCase();
}

export function isHicTrack(track) {
  return formatForTrack(track) === "hic";
}

export function configureHicHeatmap(config) {
  if (String(config?.format || "").toLowerCase() !== "hic") return config;
  return {
    ...config,
    type: "interact",
    height: config.height || 280,
    minHeight: config.minHeight || 160,
    color: config.color || DEFAULT_HEATMAP_COLOR,
    binThreshold: -1,
    percentileThreshold: 0.1,
    genomeCanvasHicHeatmap: true,
  };
}

function percentile(values, fraction) {
  if (!values.length) return 1;
  const sorted = [...values].sort((left, right) => left - right);
  const index = Math.min(sorted.length - 1, Math.max(0, Math.floor((sorted.length - 1) * fraction)));
  return sorted[index] || sorted[sorted.length - 1] || 1;
}

function parseRGB(color) {
  const match = String(color || "").match(/^rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)/i);
  return match
    ? match.slice(1, 4).map((component) => clamp(Number.parseInt(component, 10), 0, 255))
    : [190, 38, 52];
}

function heatColor(color, value, maximum, alpha = 1, background = [255, 255, 255]) {
  const [red, green, blue] = parseRGB(color);
  const [baseRed, baseGreen, baseBlue] = background;
  const intensity = maximum > 0 ? clamp(Math.log1p(Math.max(0, value)) / Math.log1p(maximum), 0, 1) : 0;
  const shaped = Math.pow(intensity, 0.72) * clamp(alpha, 0, 1);
  return `rgb(${Math.round(baseRed + (red - baseRed) * shaped)}, ${Math.round(baseGreen + (green - baseGreen) * shaped)}, ${Math.round(baseBlue + (blue - baseBlue) * shaped)})`;
}

function bestResolution(resolutions, bpPerPixel) {
  const valid = (resolutions || []).filter((resolution) => Number.isFinite(resolution) && resolution > 0);
  if (!valid.length) return Math.max(1, Math.round(bpPerPixel));
  const target = Math.max(1, bpPerPixel * 2);
  const suitable = valid.filter((resolution) => resolution >= target);
  return suitable.length ? Math.min(...suitable) : Math.min(...valid);
}

async function hicHeatmapFeatures(track, chr, start, end, bpPerPixel) {
  const source = track.featureSource;
  const hicFile = source?.hicFile;
  if (!source?.getRecords || !hicFile) return [];
  if (!hicFile.initialized) await hicFile.init();

  const binSize = bestResolution(hicFile.bpResolutions, bpPerPixel);
  const records = await source.getRecords(chr, start, end, binSize);
  const normalization = track.normalization || track.config?.normalization || "NONE";
  let normalizationValues;
  let firstBin = 0;

  if (normalization !== "NONE" && source.getNormalizationVector) {
    const vector = await source.getNormalizationVector(normalization, chr, binSize);
    firstBin = Math.floor(start / binSize);
    normalizationValues = await vector.getValues(firstBin, Math.ceil(end / binSize));
  }

  const chromosome = track.browser?.genome?.getChromosomeName?.(chr) || chr;
  const features = [];
  for (const record of records || []) {
    const bin1 = Number(record.bin1);
    const bin2 = Number(record.bin2);
    let value = Number(record.counts);
    if (![bin1, bin2, value].every(Number.isFinite) || value <= 0) continue;

    if (normalizationValues) {
      const divisor = normalizationValues[bin1 - firstBin] * normalizationValues[bin2 - firstBin];
      if (!Number.isFinite(divisor) || divisor === 0) continue;
      value /= divisor;
    }

    const start1 = bin1 * binSize;
    const start2 = bin2 * binSize;
    features.push({
      chr: chromosome,
      chr1: chromosome,
      chr2: chromosome,
      start: Math.min(start1, start2),
      end: Math.max(start1, start2) + binSize,
      start1,
      end1: start1 + binSize,
      start2,
      end2: start2 + binSize,
      binSize,
      value,
      score: value,
    });
  }
  features.sort((left, right) => left.start - right.start || left.end - right.end);
  return features;
}

function drawDiamond(context, centerX, centerY, halfWidth, halfHeight) {
  context.beginPath();
  context.moveTo(centerX - halfWidth, centerY);
  context.lineTo(centerX, centerY - halfHeight);
  context.lineTo(centerX + halfWidth, centerY);
  context.lineTo(centerX, centerY + halfHeight);
  context.closePath();
  context.fill();
}

// Pixel density of the raster embedded in SVG/PNG exports (matches exportPNG's 2x).
const EXPORT_RASTER_SCALE = 2;

function drawHicHeatmap(track, options) {
  // Contacts fade into the canvas background, so empty cells match the theme.
  return withoutCanvasAdapter(() => {
    const palette = canvasTheme();
    const raster = typeof options.context?.getSerializedSvg === "function" ? rasterContextFor(options) : null;
    if (!raster) return drawHicHeatmapCells(track, options, palette);
    // A heatmap is tens of thousands of cells; as SVG paths they dominate export
    // time and size, so exports embed the painted heatmap as one image instead.
    drawHicHeatmapCells(track, { ...options, context: raster.context, pixelTop: 0 }, palette);
    options.context.drawImage(raster.canvas, 0, 0, raster.width, raster.height);
  });
}

function rasterContextFor(options) {
  if (typeof document === "undefined" || typeof document.createElement !== "function") return null;
  const width = Math.max(1, Math.ceil(options.pixelWidth));
  const height = Math.max(1, Math.ceil(options.pixelHeight));
  const canvas = document.createElement("canvas");
  canvas.width = width * EXPORT_RASTER_SCALE;
  canvas.height = height * EXPORT_RASTER_SCALE;
  const context = canvas.getContext?.("2d");
  if (!context) return null;
  context.scale(EXPORT_RASTER_SCALE, EXPORT_RASTER_SCALE);
  return { canvas, context, width, height };
}

function drawHicHeatmapCells(track, options, palette) {
  const { context, features = [], bpStart, bpPerPixel, pixelWidth, pixelHeight } = options;
  context.save();
  context.fillStyle = palette.background;
  context.fillRect(0, options.pixelTop || 0, pixelWidth, pixelHeight);
  context.beginPath();
  context.moveTo(0, pixelHeight - 0.5);
  context.lineTo(pixelWidth, pixelHeight - 0.5);
  context.strokeStyle = palette.diagonal;
  context.lineWidth = 1;
  context.stroke();

  const visible = features.filter((feature) => Number.isFinite(feature.value) && feature.value > 0);
  const maximum = percentile(visible.map((feature) => feature.value), 0.98);
  const baseColor = track.color || track.config?.color || DEFAULT_HEATMAP_COLOR;
  const alpha = Number.isFinite(options.alpha) ? options.alpha : 1;

  for (const feature of visible) {
    const center1 = (feature.start1 + feature.end1) / 2;
    const center2 = (feature.start2 + feature.end2) / 2;
    const centerX = ((center1 + center2) / 2 - bpStart) / bpPerPixel;
    const centerY = pixelHeight - Math.abs(center2 - center1) / (2 * bpPerPixel);
    const binSize = feature.binSize || Math.max(feature.end1 - feature.start1, feature.end2 - feature.start2);
    const halfCell = Math.max(0.65, binSize / (2 * bpPerPixel));
    if (centerX + halfCell < 0 || centerX - halfCell > pixelWidth || centerY + halfCell < 0 || centerY - halfCell > pixelHeight) continue;
    context.fillStyle = heatColor(baseColor, feature.value, maximum, alpha, palette.backgroundRGB);
    drawDiamond(context, centerX, centerY, halfCell + 0.2, halfCell + 0.2);
  }
  context.restore();
}

export function installHicHeatmapRenderer(track) {
  if (!isHicTrack(track) || track.__genomeCanvasHicHeatmapRenderer) return false;
  if (!track.featureSource?.hicFile || typeof track.draw !== "function") return false;

  const automaticColor = track.genomeCanvasAutoColor || track.config?.genomeCanvasAutoColor;
  track.color = automaticColor ? DEFAULT_HEATMAP_COLOR : track.config?.color || DEFAULT_HEATMAP_COLOR;
  track.config.color = track.color;
  track.config.genomeCanvasHicHeatmap = true;
  track.height = Math.max(280, Number(track.height) || 0);
  track.config.height = track.height;
  track.getFeatures = function getHicHeatmapFeatures(chr, start, end, bpPerPixel) {
    return hicHeatmapFeatures(this, chr, start, end, bpPerPixel);
  };
  track.draw = function draw(options) {
    return drawHicHeatmap(this, options);
  };
  track.__genomeCanvasHicHeatmapRenderer = true;
  if (track.trackView?.setTrackHeight) track.trackView.setTrackHeight(track.height, true);
  return true;
}
