// Palettes for the spatial viewer: categorical colors for annotations, colormaps for expression.

export type RGB = [number, number, number];

/** Distinct colors for categories; cycles past the end. Tuned to read on a dark background. */
export const CATEGORY_COLORS = [
  '#4e9eff', '#ff8c2e', '#3ec46d', '#ff4f5e', '#b07cff', '#c9855a', '#ff7ccb', '#9aa3ad',
  '#d4d433', '#2ed3d3', '#a3c8ff', '#ffc08a', '#9be59b', '#ffa3a8', '#d5bdff', '#e3b796',
  '#ffb3e3', '#d0d5da', '#eeee88', '#9aeded', '#2f6fd1', '#c45f0f', '#1f8f4a', '#c2263a',
];

export const NA_COLOR = '#555b61';

export const hexToRgb = (hex: string): RGB => {
  const v = parseInt(hex.replace('#', ''), 16);
  return [(v >> 16) & 255, (v >> 8) & 255, v & 255];
};
export const rgbToHex = ([r, g, b]: RGB) => '#' + [r, g, b].map((v) => v.toString(16).padStart(2, '0')).join('');

export function categoryColors(categories: string[]) {
  let k = 0;
  return categories.map((c) => (c === 'NA' ? NA_COLOR : CATEGORY_COLORS[k++ % CATEGORY_COLORS.length]));
}

// colormap control points (evenly spaced), interpolated to 256 entries
const STOPS: Record<string, string[]> = {
  viridis: ['#440154', '#482878', '#3e4989', '#31688e', '#26828e', '#1f9e89', '#35b779', '#6ece58', '#b5de2b', '#fde725'],
  magma: ['#000004', '#180f3d', '#440f76', '#721f81', '#9e2f7f', '#cd4071', '#f1605d', '#fd9668', '#feca8d', '#fcfdbf'],
  inferno: ['#000004', '#1b0c41', '#4a0c6b', '#781c6d', '#a52c60', '#cf4446', '#ed6925', '#fb9b06', '#f7d13d', '#fcffa4'],
  plasma: ['#0d0887', '#47039f', '#7301a8', '#9c179e', '#bd3786', '#d8576b', '#ed7953', '#fb9f3a', '#fdca26', '#f0f921'],
  cividis: ['#00224e', '#123570', '#3b496c', '#575d6d', '#707173', '#8a8779', '#a69d75', '#c4b56c', '#e4cf5b', '#fee838'],
  turbo: ['#30123b', '#4662d7', '#36aaf9', '#1ae4b6', '#72fe5e', '#c8ef34', '#faba39', '#f66b19', '#ca2a04', '#7a0403'],
  reds: ['#3b0a0a', '#5c1010', '#7e1717', '#a01e1e', '#c22525', '#e02f2f', '#f05a4a', '#f78a72', '#fbb9a0', '#fde3d4'],
  greens: ['#06260f', '#0b3d19', '#105424', '#166c2f', '#1e853b', '#2a9e48', '#4cb862', '#7acf86', '#aee3b1', '#e0f5df'],
  /** diverging (negative blue, 0 white, positive red), e.g. chromVAR deviation z-scores */
  rdbu: ['#2166ac', '#4393c3', '#92c5de', '#d1e5f0', '#f7f7f7', '#fddbc7', '#f4a582', '#d6604d', '#b2182b'],
};
export const COLORMAPS = Object.keys(STOPS);

const lutCache = new Map<string, Uint8Array>();
/** 256 x RGB lookup table. */
export function colormapLut(name: string): Uint8Array {
  let lut = lutCache.get(name);
  if (lut) return lut;
  const stops = (STOPS[name] ?? STOPS.viridis).map(hexToRgb);
  lut = new Uint8Array(256 * 3);
  for (let i = 0; i < 256; i++) {
    const t = (i / 255) * (stops.length - 1);
    const a = Math.floor(t), b = Math.min(a + 1, stops.length - 1), f = t - a;
    for (let c = 0; c < 3; c++) lut[i * 3 + c] = Math.round(stops[a][c] * (1 - f) + stops[b][c] * f);
  }
  lutCache.set(name, lut);
  return lut;
}

export const colormapGradient = (name: string) => `linear-gradient(90deg, ${(STOPS[name] ?? STOPS.viridis).join(', ')})`;
