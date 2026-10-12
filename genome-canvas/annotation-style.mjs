export const REFSEQ_ALL_COLOR = "rgb(69, 74, 72)";
export const REFSEQ_LABEL_COLOR = "rgb(0, 0, 0)";
export const REFSEQ_LABEL_SCALE = 1.5;
export const REFSEQ_LINE_WIDTH = 2;
export const REFSEQ_ARROW_SPACING = 22;

function normalized(value) {
  return String(value || "").toLowerCase().replace(/[^a-z0-9]+/g, "");
}

export function isRefSeqAllTrack(track) {
  const name = normalized(track?.name || track?.config?.name);
  const id = normalized(track?.id || track?.config?.id);
  return name === "refseqall" || id === "refseqall";
}

function featureLabel(track, feature) {
  const labelField = track.config?.labelField || "name";
  return feature?.[labelField]
    ?? feature?.gene?.name
    ?? feature?.id
    ?? feature?.ID;
}

function scaledFont(font, scale = REFSEQ_LABEL_SCALE) {
  const source = String(font || "10px sans-serif");
  if (/\d+(?:\.\d+)?px/i.test(source)) {
    return source.replace(/\d+(?:\.\d+)?px/i, (size) => `${Number.parseFloat(size) * scale}px`);
  }
  return `${10 * scale}px sans-serif`;
}

function restoreMethod(context, method, original, owned) {
  if (owned) context[method] = original;
  else delete context[method];
}

export function installRefSeqAllStyle(track) {
  if (!isRefSeqAllTrack(track)) return false;

  track.color = REFSEQ_ALL_COLOR;
  track._initialColor = REFSEQ_ALL_COLOR;
  if (track.config) {
    track.config.color = REFSEQ_ALL_COLOR;
    track.config.genomeCanvasRefSeqStyle = true;
  }
  track.arrowSpacing = REFSEQ_ARROW_SPACING;

  if (track.__genomeCanvasRefSeqStyle || typeof track.render !== "function") return true;

  const originalRender = track.render;
  track.render = function renderRefSeqFeature(feature, bpStart, bpPerPixel, pixelHeight, context, options) {
    const label = featureLabel(this, feature);
    if (!context) {
      return originalRender.call(this, feature, bpStart, bpPerPixel, pixelHeight, context, options);
    }

    const styleLabel = Boolean(label && options?.drawLabel && context.fillText && context.measureText);
    const hasOwnFillText = styleLabel && Object.prototype.hasOwnProperty.call(context, "fillText");
    const hasOwnMeasureText = styleLabel && Object.prototype.hasOwnProperty.call(context, "measureText");
    const hasOwnStroke = typeof context.stroke === "function" && Object.prototype.hasOwnProperty.call(context, "stroke");
    const originalFillText = context.fillText;
    const originalMeasureText = context.measureText;
    const originalStroke = context.stroke;
    const isGeneLabel = (text) => String(text) === String(label);

    if (styleLabel) {
      context.measureText = function measureRefSeqLabel(text) {
        if (!isGeneLabel(text)) return originalMeasureText.call(this, text);
        const previousFont = this.font;
        this.font = scaledFont(previousFont);
        try {
          return originalMeasureText.call(this, text);
        } finally {
          this.font = previousFont;
        }
      };

      context.fillText = function fillRefSeqLabel(text, ...coordinates) {
        if (!isGeneLabel(text)) return originalFillText.call(this, text, ...coordinates);
        const previousFont = this.font;
        const previousFillStyle = this.fillStyle;
        const previousStrokeStyle = this.strokeStyle;
        this.font = scaledFont(previousFont);
        this.fillStyle = REFSEQ_LABEL_COLOR;
        this.strokeStyle = REFSEQ_LABEL_COLOR;
        try {
          return originalFillText.call(this, text, ...coordinates);
        } finally {
          this.font = previousFont;
          this.fillStyle = previousFillStyle;
          this.strokeStyle = previousStrokeStyle;
        }
      };
    }

    if (typeof originalStroke === "function") {
      context.stroke = function strokeRefSeqBackbone(...argumentsList) {
        const previousLineWidth = this.lineWidth;
        const previousLineCap = this.lineCap;
        const previousLineJoin = this.lineJoin;
        this.lineWidth = Math.max(Number(previousLineWidth) || 1, REFSEQ_LINE_WIDTH);
        this.lineCap = "round";
        this.lineJoin = "round";
        try {
          return originalStroke.apply(this, argumentsList);
        } finally {
          this.lineWidth = previousLineWidth;
          this.lineCap = previousLineCap;
          this.lineJoin = previousLineJoin;
        }
      };
    }

    try {
      return originalRender.call(this, feature, bpStart, bpPerPixel, pixelHeight, context, options);
    } finally {
      if (styleLabel) {
        restoreMethod(context, "fillText", originalFillText, hasOwnFillText);
        restoreMethod(context, "measureText", originalMeasureText, hasOwnMeasureText);
      }
      if (typeof originalStroke === "function") restoreMethod(context, "stroke", originalStroke, hasOwnStroke);
    }
  };

  track.__genomeCanvasRefSeqStyle = true;
  return true;
}
