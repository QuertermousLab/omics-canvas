import { canvasTheme, withoutCanvasAdapter } from "./canvas-theme.mjs?v=20261007.2";

export const SIGNIFICANCE_P_VALUE = 5e-8;
export const GENOME_WIDE_THRESHOLD = -Math.log10(SIGNIFICANCE_P_VALUE);

export const LD_COLORS = Object.freeze({
  reference: "#9632b8",
  missing: "#b8b8b8",
  low: "#357ebd",
  lowMedium: "#46b8da",
  medium: "#5cb85c",
  mediumHigh: "#eea236",
  high: "#d43f3a",
});

function clamp(value, minimum, maximum) {
  return Math.max(minimum, Math.min(maximum, value));
}

function featureFields(feature) {
  if (feature?.__genomeCanvasFieldMap) return feature.__genomeCanvasFieldMap;
  const result = new Map();
  if (feature?.line && Array.isArray(feature.columns)) {
    const values = feature.line.split("\t");
    feature.columns.forEach((column, index) => result.set(String(column).toLowerCase(), values[index]));
  }
  if (feature) feature.__genomeCanvasFieldMap = result;
  return result;
}

function featureField(feature, names) {
  const fields = featureFields(feature);
  for (const name of names) {
    const value = fields.get(String(name).toLowerCase());
    if (value !== undefined && value !== "") return value;
  }
  return undefined;
}

function numericFeatureField(feature, names) {
  const value = Number(featureField(feature, names));
  return Number.isFinite(value) ? value : Number.NaN;
}

function ldMetadataField(feature, name) {
  const value = featureField(feature, [name]);
  return value && !/^(?:\.|na|nan|null)$/i.test(String(value).trim()) ? value : undefined;
}

export function manhattanValue(track, feature) {
  const property = track?.valueProperty || "value";
  const raw = Number(feature?.[property]);
  if (!Number.isFinite(raw)) return Number.NaN;
  if (track?.posteriorProbability) return raw;
  if (raw <= 0) return Number.NaN;
  return -Math.log10(raw);
}

export function manhattanPointStyle(rSquared, isReference = false) {
  if (isReference) return { fill: LD_COLORS.reference, stroke: "#642477", radius: 5.2 };
  if (!Number.isFinite(rSquared)) return { fill: LD_COLORS.missing, stroke: "#7f7f7f", radius: 3.8 };
  if (rSquared >= 0.8) return { fill: LD_COLORS.high, stroke: "#922c29", radius: 4.4 };
  if (rSquared >= 0.6) return { fill: LD_COLORS.mediumHigh, stroke: "#a96e1e", radius: 4.2 };
  if (rSquared >= 0.4) return { fill: LD_COLORS.medium, stroke: "#39743a", radius: 4 };
  if (rSquared >= 0.2) return { fill: LD_COLORS.lowMedium, stroke: "#287b91", radius: 3.9 };
  return { fill: LD_COLORS.low, stroke: "#28577e", radius: 3.8 };
}

export function manhattanShape(feature, isReference = false) {
  if (isReference) return "diamond";
  const beta = numericFeatureField(feature, ["beta", "effect", "effect_size", "estimate"]);
  if (!Number.isFinite(beta) || beta === 0) return "circle";
  return beta > 0 ? "triangle-up" : "triangle-down";
}

export function normalizedManhattanRange(dataRange) {
  const observedMaximum = Number(dataRange?.max);
  return {
    min: 0,
    max: Math.max(GENOME_WIDE_THRESHOLD + 0.7, Number.isFinite(observedMaximum) ? observedMaximum : 0),
  };
}

const LD_FIELDS = ["r2", "r_squared", "ld_r2", "ld", "correlation"];

function embeddedR2(feature) {
  const value = numericFeatureField(feature, LD_FIELDS);
  return value >= 0 && value <= 1 ? value : Number.NaN;
}

function featureR2(feature) {
  if (Number.isFinite(feature?.genomeCanvasR2)) return feature.genomeCanvasR2;
  return embeddedR2(feature);
}

function featureOriginalLocus(feature) {
  const value = featureField(feature, ["hg19_locus", "grch37_locus"]);
  const match = String(value || "").match(/^(?:chr)?([0-9]+|X|Y):(\d+)$/i);
  if (!match) return null;
  return { chromosome: match[1].toUpperCase(), position: Number(match[2]) };
}

function featureAlleles(feature) {
  return {
    ref: String(featureField(feature, ["ref", "reference_allele"]) || ""),
    alt: String(featureField(feature, ["alt", "alternate_allele", "effect_allele"]) || ""),
  };
}

function allelesMatch(first, second) {
  if (!first?.ref || !first?.alt || !second?.ref || !second?.alt) return true;
  const normalized = (pair) => [pair.ref.toUpperCase(), pair.alt.toUpperCase()].sort().join("/");
  if (normalized(first) === normalized(second)) return true;
  const complement = (value) => value.toUpperCase().replace(/[ACGT]/g, (base) => ({ A: "T", C: "G", G: "C", T: "A" })[base]);
  return normalized({ ref: complement(first.ref), alt: complement(first.alt) }) === normalized(second);
}

function augmentPopup(feature, label) {
  if (!feature || feature.__genomeCanvasLDPopup || typeof feature.popupData !== "function") return;
  const originalPopup = feature.popupData;
  feature.popupData = function popupData(...args) {
    const originalResult = originalPopup.apply(this, args);
    const result = Array.isArray(originalResult) ? originalResult : [];
    const rSquared = featureR2(this);
    if (Number.isFinite(rSquared)) {
      result.push({ name: `LD r² (${this.genomeCanvasLDLabel || label})`, value: rSquared.toFixed(3) });
    } else {
      result.push({ name: "LD r²", value: "Not available" });
    }
    const reference = ldMetadataField(this, "ld_reference") || this.genomeCanvasLDId;
    if (reference || this.genomeCanvasIsLDReference) result.push({ name: "LD reference", value: reference || "Yes" });
    const locus = ldMetadataField(this, "ld_reference_locus");
    if (locus) result.push({ name: "LD reference (GRCh37)", value: locus });
    const hg38Locus = ldMetadataField(this, "ld_reference_hg38");
    if (hg38Locus) result.push({ name: "LD reference (GRCh38)", value: hg38Locus });
    const ldLocus = ldMetadataField(this, "ld_locus");
    if (ldLocus) result.push({ name: "LD locus", value: ldLocus });
    const method = ldMetadataField(this, "ld_method");
    if (method) result.push({ name: "LD method", value: method });
    return result;
  };
  feature.__genomeCanvasLDPopup = true;
}

function endpointURL(endpoint, parameters) {
  const base = typeof document === "undefined" ? "http://localhost/" : document.baseURI;
  const url = new URL(String(endpoint || "api/gwas-ld").replace(/^\/+/, ""), base);
  for (const [name, value] of Object.entries(parameters)) url.searchParams.set(name, value);
  return url;
}

async function fetchLDForFeatures(track, features) {
  if (!features?.length) return features;
  // Only file columns identify embedded LD: previous dynamic results must not
  // freeze the reference SNP when the viewport changes.
  const embedded = features.some((feature) => LD_FIELDS.some((name) => featureFields(feature).has(name)));
  if (embedded) {
    const available = features.some((feature) => Number.isFinite(embeddedR2(feature)));
    const populations = [...new Set(features.map((feature) => ldMetadataField(feature, "ld_population")).filter(Boolean))];
    const references = [...new Set(features.map((feature) => ldMetadataField(feature, "ld_reference")).filter(Boolean))];
    const outsideLoci = features.some((feature) => featureFields(feature).has("ld_locus"))
      && !features.some((feature) => ldMetadataField(feature, "ld_locus"));
    track.__genomeCanvasLDStatus = {
      available,
      label: populations.join(", ") || track.config?.ldLabel || "embedded LD",
      referenceIds: references,
      source: "embedded",
      reason: available ? undefined : outsideLoci ? "Outside precomputed LD loci" : "No matching variants in the precomputed LD panel",
    };
    for (const feature of features) {
      delete feature.genomeCanvasR2;
      const flag = String(featureField(feature, ["ld_is_reference"]) || "").toLowerCase();
      feature.genomeCanvasIsLDReference = ["1", "true", "yes"].includes(flag) && Number.isFinite(embeddedR2(feature));
      feature.genomeCanvasLDId = ldMetadataField(feature, "ld_reference");
      feature.genomeCanvasLDLabel = ldMetadataField(feature, "ld_population") || track.__genomeCanvasLDStatus.label;
      augmentPopup(feature, track.__genomeCanvasLDStatus.label);
    }
    return features;
  }

  for (const feature of features) {
    delete feature.genomeCanvasR2;
    delete feature.genomeCanvasIsLDReference;
    delete feature.genomeCanvasLDId;
    delete feature.genomeCanvasLDLabel;
  }

  const candidates = features
    .map((feature) => ({ feature, locus: featureOriginalLocus(feature), value: manhattanValue(track, feature) }))
    .filter((item) => item.locus && Number.isFinite(item.value));
  if (!candidates.length || !track.config?.ldEndpoint) {
    track.__genomeCanvasLDStatus = { available: false, label: track.config?.ldLabel || "LD", reason: "No compatible LD coordinates" };
    return features;
  }
  candidates.sort((first, second) => second.value - first.value);
  const lead = candidates[0];
  const chromosome = lead.locus.chromosome;
  const sameChromosome = candidates.filter((item) => item.locus.chromosome === chromosome);
  const start = Math.min(...sameChromosome.map((item) => item.locus.position));
  const end = Math.max(...sameChromosome.map((item) => item.locus.position));
  const maxWindow = Number(track.config.ldMaxWindow) || 2_000_000;
  if (end - start > maxWindow) {
    track.__genomeCanvasLDStatus = { available: false, label: track.config?.ldLabel || "LD", reason: `Zoom to ≤${maxWindow / 1_000_000} Mb` };
    return features;
  }

  const alleles = featureAlleles(lead.feature);
  const key = [chromosome, start, end, lead.locus.position, alleles.ref, alleles.alt].join(":");
  track.__genomeCanvasLDCache ||= new Map();
  let payload = track.__genomeCanvasLDCache.get(key);
  if (!payload) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 22000);
    try {
      const response = await fetch(endpointURL(track.config.ldEndpoint, {
        chrom: chromosome,
        start,
        end,
        lead: lead.locus.position,
        ref: alleles.ref,
        alt: alleles.alt,
      }), { credentials: "same-origin", signal: controller.signal });
      if (!response.ok) throw new Error(`LD request returned ${response.status}`);
      payload = await response.json();
      if (track.__genomeCanvasLDCache.size >= 8) {
        track.__genomeCanvasLDCache.delete(track.__genomeCanvasLDCache.keys().next().value);
      }
      track.__genomeCanvasLDCache.set(key, payload);
    } catch (error) {
      payload = { available: false, label: track.config?.ldLabel || "LD", reason: error.name === "AbortError" ? "LD request timed out" : error.message };
    } finally {
      clearTimeout(timeout);
    }
  }

  track.__genomeCanvasLDStatus = payload;
  if (!payload.available) return features;
  const byPosition = new Map();
  for (const variant of payload.variants || []) {
    if (!byPosition.has(Number(variant.position))) byPosition.set(Number(variant.position), []);
    byPosition.get(Number(variant.position)).push(variant);
  }
  for (const item of sameChromosome) {
    const matches = byPosition.get(item.locus.position) || [];
    const allelesForFeature = featureAlleles(item.feature);
    const match = matches.find((variant) => allelesMatch(allelesForFeature, variant));
    if (match && Number.isFinite(Number(match.r2))) item.feature.genomeCanvasR2 = Number(match.r2);
    if (
      payload.reference
      && item.locus.position === Number(payload.reference.position)
      && allelesMatch(allelesForFeature, payload.reference)
    ) {
      item.feature.genomeCanvasIsLDReference = true;
      item.feature.genomeCanvasR2 = 1;
      item.feature.genomeCanvasLDId = payload.reference.id;
    }
    augmentPopup(item.feature, payload.label || track.config.ldLabel || "LD");
  }
  return features;
}

function ensureTrackRange(track) {
  const normalized = normalizedManhattanRange(track.dataRange);
  if (track.dataRange?.min !== normalized.min || track.dataRange?.max !== normalized.max) {
    track.dataRange = normalized;
  }
  return normalized;
}

function drawHorizontalGuides(context, width, height, dataRange) {
  const minimum = Number(dataRange?.min);
  const maximum = Number(dataRange?.max);
  if (!Number.isFinite(minimum) || !Number.isFinite(maximum) || maximum <= minimum) return;

  const yFor = (value) => height - ((value - minimum) / (maximum - minimum)) * height;
  const interval = maximum > 30 ? 10 : maximum > 12 ? 5 : 2;
  context.save();
  context.lineWidth = 1;
  context.setLineDash?.([3, 5]);
  context.strokeStyle = canvasTheme().grid;
  for (let value = Math.ceil(minimum / interval) * interval; value < maximum; value += interval) {
    const y = yFor(value);
    context.beginPath();
    context.moveTo(0, y);
    context.lineTo(width, y);
    context.stroke();
  }

  const y = yFor(GENOME_WIDE_THRESHOLD);
  context.setLineDash?.([7, 5]);
  context.strokeStyle = canvasTheme().threshold;
  context.beginPath();
  context.moveTo(0, y);
  context.lineTo(width, y);
  context.stroke();
  context.setLineDash?.([]);
  context.fillStyle = canvasTheme().thresholdText;
  context.font = "600 10px Arial, sans-serif";
  context.textAlign = "right";
  const labelX = width >= 210 ? width - 128 : width - 7;
  context.fillText("P = 5×10⁻⁸", Math.max(72, labelX), Math.max(11, y - 4));
  context.restore();
}

function symbolPath(context, x, y, radius, shape) {
  context.beginPath();
  if (shape === "diamond") {
    context.moveTo(x, y - radius * 1.25);
    context.lineTo(x + radius, y);
    context.lineTo(x, y + radius * 1.25);
    context.lineTo(x - radius, y);
    context.closePath();
  } else if (shape === "triangle-up") {
    context.moveTo(x, y - radius * 1.25);
    context.lineTo(x + radius * 1.12, y + radius);
    context.lineTo(x - radius * 1.12, y + radius);
    context.closePath();
  } else if (shape === "triangle-down") {
    context.moveTo(x - radius * 1.12, y - radius);
    context.lineTo(x + radius * 1.12, y - radius);
    context.lineTo(x, y + radius * 1.25);
    context.closePath();
  } else {
    context.arc(x, y, radius, 0, Math.PI * 2);
  }
}

function drawPoint(context, x, y, style, shape, alpha) {
  context.save();
  context.globalAlpha = clamp(alpha, 0.15, 1);
  symbolPath(context, x, y, style.radius, shape);
  context.fillStyle = style.fill;
  context.fill();
  context.globalAlpha = clamp(alpha + 0.08, 0.2, 1);
  context.strokeStyle = style.stroke;
  context.lineWidth = 0.85;
  context.stroke();
  context.restore();
}

function featureLabel(feature) {
  if (feature?.genomeCanvasLDId) return feature.genomeCanvasLDId;
  const label = featureField(feature, ["rsid", "snp", "variant", "name"]);
  if (label && label !== ".") return label;
  return feature?.chr && Number.isFinite(feature?.start) ? `${feature.chr}:${feature.start + 1}` : "";
}

function drawLeadLabel(context, feature, value, width) {
  const label = featureLabel(feature);
  if (!label || value < GENOME_WIDE_THRESHOLD) return;
  const maximumX = width >= 210 ? width - 148 : width - 42;
  const x = clamp(feature.px, 42, Math.max(42, maximumX));
  const y = Math.max(13, feature.py - 9);
  context.save();
  context.font = "600 11px Arial, sans-serif";
  context.textAlign = "center";
  context.lineWidth = 3;
  context.strokeStyle = canvasTheme().halo;
  context.strokeText(label, x, y);
  context.fillStyle = canvasTheme().labelText;
  context.fillText(label, x, y);
  context.restore();
}

const LD_LEGEND_WIDTH = 114;
const LD_LEGEND_HEIGHT = 192;
const viewportLegends = new WeakMap();

function drawLDLegend(...args) {
  return withoutCanvasAdapter(() => drawLDLegendPanel(...args));
}

function drawLDLegendPanel(context, width, status, x = width - LD_LEGEND_WIDTH - 8, y = 8) {
  if (width < 210) return;
  const boxWidth = LD_LEGEND_WIDTH;
  const boxHeight = LD_LEGEND_HEIGHT;
  const colors = [LD_COLORS.high, LD_COLORS.mediumHigh, LD_COLORS.medium, LD_COLORS.lowMedium, LD_COLORS.low];
  context.save();
  const palette = canvasTheme();
  context.fillStyle = palette.panel;
  context.strokeStyle = palette.panelBorder;
  context.lineWidth = 1;
  context.fillRect(x, y, boxWidth, boxHeight);
  context.strokeRect(x + 0.5, y + 0.5, boxWidth - 1, boxHeight - 1);
  context.fillStyle = palette.panelTitle;
  context.font = "600 12px Arial, sans-serif";
  context.textAlign = "left";
  context.fillText("LD (r²)", x + 10, y + 17);
  context.fillStyle = palette.panelMuted;
  context.font = "9px Arial, sans-serif";
  const label = String(status?.label || "LD").replace("1000 Genomes", "1000G");
  context.fillText(status?.available ? label : "LD unavailable", x + 10, y + 30, boxWidth - 20);
  const barX = x + 14;
  const barY = y + 39;
  const segmentHeight = 14;
  colors.forEach((color, index) => {
    context.fillStyle = color;
    context.fillRect(barX, barY + index * segmentHeight, 19, segmentHeight + 0.5);
  });
  context.strokeStyle = palette.panelBorder;
  context.strokeRect(barX + 0.5, barY + 0.5, 18, segmentHeight * 5 - 1);
  context.fillStyle = palette.panelText;
  context.font = "11px Arial, sans-serif";
  ["1", "0.8", "0.6", "0.4", "0.2", "0"].forEach((label, index) => {
    context.fillText(label, barX + 25, barY + index * segmentHeight + 4);
  });
  drawPoint(context, x + 18, y + 126, manhattanPointStyle(1, true), "diamond", 1);
  context.fillStyle = palette.panelText;
  context.font = "11px Arial, sans-serif";
  context.fillText("LD ref", x + 31, y + 130);
  drawPoint(context, x + 17, y + 148, { fill: "#777", stroke: "#555", radius: 3.5 }, "triangle-up", 1);
  context.fillText("β > 0", x + 29, y + 152);
  drawPoint(context, x + 17, y + 166, { fill: "#777", stroke: "#555", radius: 3.5 }, "triangle-down", 1);
  context.fillText("β < 0", x + 29, y + 170);
  drawPoint(context, x + 17, y + 184, { fill: "#777", stroke: "#555", radius: 3.5 }, "circle", 1);
  context.fillText("no β", x + 29, y + 188);
  context.restore();
}

function updateViewportLegend(track, viewport) {
  const element = viewport.viewportElement;
  let legend = viewportLegends.get(viewport);
  if (!legend) {
    const canvas = element.ownerDocument.createElement("canvas");
    canvas.className = "genome-canvas-ld-legend";
    canvas.setAttribute("aria-hidden", "true");
    Object.assign(canvas.style, {
      position: "absolute", top: "0", right: "0", zIndex: "2", pointerEvents: "none",
      width: `${LD_LEGEND_WIDTH + 8}px`, height: `${LD_LEGEND_HEIGHT + 8}px`,
    });
    const redraw = () => {
      const width = element.clientWidth;
      canvas.style.display = width >= 210 ? "block" : "none";
      const ratio = element.ownerDocument.defaultView?.devicePixelRatio || 1;
      canvas.width = Math.round((LD_LEGEND_WIDTH + 8) * ratio);
      canvas.height = Math.round((LD_LEGEND_HEIGHT + 8) * ratio);
      const context = canvas.getContext("2d");
      context.scale(ratio, ratio);
      drawLDLegend(context, width, track.__genomeCanvasLDStatus, 0);
    };
    const Observer = element.ownerDocument.defaultView?.ResizeObserver;
    const observer = Observer ? new Observer(redraw) : undefined;
    observer?.observe(element);
    legend = { canvas, redraw, observer };
    viewportLegends.set(viewport, legend);
    element.appendChild(canvas);
    const originalDispose = viewport.dispose;
    viewport.dispose = function dispose(...args) {
      observer?.disconnect();
      canvas.remove();
      viewportLegends.delete(this);
      return originalDispose?.apply(this, args);
    };
  }
  legend.redraw();
}

function drawManhattan(track, options) {
  const context = options.context;
  const height = Math.max(1, options.pixelHeight - 1);
  const range = ensureTrackRange(track);
  const minimum = range.min;
  const maximum = range.max;

  if (track.background) {
    context.save();
    context.fillStyle = track.background;
    context.fillRect(0, 0, options.pixelWidth, options.pixelHeight);
    context.restore();
  }
  drawHorizontalGuides(context, options.pixelWidth, height, range);

  const bpEnd = options.bpStart + options.pixelWidth * options.bpPerPixel + 1;
  let leadFeature;
  let leadValue = Number.NEGATIVE_INFINITY;
  for (const feature of options.features || []) {
    if (feature.start < options.bpStart) continue;
    if (feature.start > bpEnd) break;
    const value = manhattanValue(track, feature);
    if (!Number.isFinite(value)) continue;
    const x = Math.round((feature.start - options.bpStart) / options.bpPerPixel);
    const rSquared = featureR2(feature);
    const isReference = feature.genomeCanvasIsLDReference === true;
    const style = manhattanPointStyle(rSquared, isReference);
    const y = clamp(height - ((value - minimum) / (maximum - minimum)) * height, style.radius + 1, height - style.radius);
    drawPoint(context, x, y, style, manhattanShape(feature, isReference), Number.isFinite(options.alpha) ? options.alpha : 0.92);
    feature.px = x;
    feature.py = y;
    const recordType = String(featureField(feature, ["record_type"]) || "").toLowerCase();
    const currentIsReference = leadFeature?.genomeCanvasIsLDReference === true;
    if (recordType !== "proxy" && ((isReference && !currentIsReference) || (isReference === currentIsReference && value > leadValue))) {
      leadFeature = feature;
      leadValue = value;
    }
  }

  if (leadFeature) drawLeadLabel(context, leadFeature, leadValue, options.pixelWidth);
  context.save();
  context.strokeStyle = canvasTheme().axisLine;
  context.lineWidth = 1;
  context.beginPath();
  context.moveTo(0, height);
  context.lineTo(options.pixelWidth, height);
  context.stroke();
  context.restore();
  // IGV pans its cached canvas without redrawing. A separate viewport overlay
  // stays fixed during dragging; SVG/PNG exports still draw the same legend.
  if (options.viewport?.viewportElement && typeof context.getSerializedSvg !== "function") {
    updateViewportLegend(track, options.viewport);
  } else {
    context.save();
    context.translate(-(Number(options.pixelXOffset) || 0), Number(options.contentTop) || 0);
    drawLDLegend(context, Number(options.viewportWidth) || options.pixelWidth, track.__genomeCanvasLDStatus);
    context.restore();
  }
}

export function installManhattanRenderer(track) {
  const format = String(track?.format || track?.config?.format || "").toLowerCase();
  if ((track?.type !== "gwas" && format !== "gwas") || track.__genomeCanvasManhattanRenderer) return false;
  if (typeof track.draw !== "function") return false;

  const originalAutoscale = track.doAutoscale;
  if (typeof originalAutoscale === "function") {
    track.doAutoscale = function doAutoscale(features) {
      const range = originalAutoscale.call(this, features);
      this.dataRange = normalizedManhattanRange(range);
      return this.dataRange;
    };
  }
  const originalGetFeatures = track.getFeatures;
  if (typeof originalGetFeatures === "function") {
    track.getFeatures = async function getFeatures(...args) {
      const features = await originalGetFeatures.apply(this, args);
      return fetchLDForFeatures(this, features);
    };
  }
  track.dataRange = normalizedManhattanRange(track.dataRange);
  track.draw = function draw(options) {
    return drawManhattan(this, options);
  };
  track.__genomeCanvasManhattanRenderer = true;
  if (track.config) track.config.genomeCanvasManhattan = true;
  return true;
}
