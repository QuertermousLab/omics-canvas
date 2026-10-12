export const DEFAULT_HIGHLIGHT_COLOR = "#f2c94c";
export const HIGHLIGHT_FILL_ALPHA = 0.14;
export const HIGHLIGHT_OUTLINE_ALPHA = 0.9;

export function normalizeHexColor(value, fallback = DEFAULT_HIGHLIGHT_COLOR) {
  const source = String(value || "").trim();
  const short = /^#([0-9a-f]{3})$/i.exec(source);
  if (short) return `#${[...short[1]].map((character) => character.repeat(2)).join("").toLowerCase()}`;
  const full = /^#([0-9a-f]{6})$/i.exec(source);
  return full ? `#${full[1].toLowerCase()}` : fallback;
}

export function hexToRgba(value, alpha) {
  const color = normalizeHexColor(value);
  const red = Number.parseInt(color.slice(1, 3), 16);
  const green = Number.parseInt(color.slice(3, 5), 16);
  const blue = Number.parseInt(color.slice(5, 7), 16);
  return `rgba(${red}, ${green}, ${blue}, ${Math.max(0, Math.min(1, Number(alpha)))})`;
}

export function genomicExtentFromPixels(referenceFrame, firstX, secondX, viewportWidth) {
  if (!referenceFrame || !Number.isFinite(referenceFrame.start) || !Number.isFinite(referenceFrame.bpPerPixel)) return null;
  const width = Math.max(0, Number(viewportWidth) || 0);
  const left = Math.max(0, Math.min(width, Math.min(Number(firstX) || 0, Number(secondX) || 0)));
  const right = Math.max(0, Math.min(width, Math.max(Number(firstX) || 0, Number(secondX) || 0)));
  if (right - left < 2) return null;
  const start = Math.max(0, Math.floor(referenceFrame.start + left * referenceFrame.bpPerPixel));
  const end = Math.max(start + 1, Math.ceil(referenceFrame.start + right * referenceFrame.bpPerPixel));
  return { chr: referenceFrame.chr, start, end };
}

export function visibleHighlightPixels(highlight, referenceFrame, viewportWidth) {
  if (!highlight || !referenceFrame || highlight.chr !== referenceFrame.chr || !Number.isFinite(referenceFrame.bpPerPixel) || referenceFrame.bpPerPixel <= 0) return null;
  const viewStart = referenceFrame.start;
  const viewEnd = viewStart + referenceFrame.bpPerPixel * viewportWidth;
  const start = Math.max(highlight.start, viewStart);
  const end = Math.min(highlight.end, viewEnd);
  if (end <= start) return null;
  const left = (start - viewStart) / referenceFrame.bpPerPixel;
  const right = (end - viewStart) / referenceFrame.bpPerPixel;
  return { left, width: Math.max(1, right - left) };
}

export function normalizeHighlights(items) {
  if (!Array.isArray(items)) return [];
  return items.flatMap((item, index) => {
    const start = Number(item?.start);
    const end = Number(item?.end);
    if (!item?.chr || !Number.isFinite(start) || !Number.isFinite(end) || end <= start) return [];
    return [{
      id: String(item.id || `highlight-${index}`),
      chr: String(item.chr),
      start: Math.max(0, Math.floor(start)),
      end: Math.ceil(end),
      color: normalizeHexColor(item.color),
    }];
  });
}
