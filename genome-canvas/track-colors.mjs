const ASSAY_THEMES = Object.freeze({
  gene:       { hue: 153, saturation: 14, lightness: 36 },
  dnase:      { hue:   8, saturation: 87, lightness: 59 },
  atac:       { hue: 253, saturation: 69, lightness: 59 },
  histone:    { hue:  39, saturation: 81, lightness: 52 },
  rna:        { hue: 171, saturation: 75, lightness: 37 },
  methyl:     { hue: 199, saturation: 52, lightness: 46 },
  interaction:{ hue: 286, saturation: 30, lightness: 45 },
  copyNumber: { hue: 322, saturation: 42, lightness: 54 },
  variant:    { hue: 357, saturation: 46, lightness: 52 },
  alignment:  { hue: 212, saturation: 42, lightness: 50 },
});

const FALLBACK_THEMES = ["dnase", "atac", "histone", "rna", "methyl", "interaction", "copyNumber", "alignment"];
const SAMPLE_VARIANTS = [
  { saturation: 0, lightness: 0 },
  { saturation: -5, lightness: -10 },
  { saturation: -6, lightness: 10 },
  { saturation: 7, lightness: -5 },
  { saturation: -12, lightness: 16 },
  { saturation: 5, lightness: 7 },
  { saturation: -2, lightness: -15 },
  { saturation: -14, lightness: 5 },
  { saturation: 8, lightness: 13 },
  { saturation: -8, lightness: -4 },
  { saturation: 4, lightness: -11 },
  { saturation: -10, lightness: 12 },
];

const TECHNICAL_WORDS = new Set([
  "atac", "dnase", "dhs", "rna", "seq", "rnaseq", "histone", "chip", "cut", "tag", "run",
  "signal", "coverage", "track", "bigwig", "bw", "wig", "bedgraph", "bed", "bigbed", "bb",
  "bam", "cram", "vcf", "bcf", "gff", "gff3", "gtf", "tdf", "seg", "maf", "qtl", "gwas",
  "sorted", "sort", "rmdup", "dedup", "deduplicated", "normalized", "norm", "merged", "filtered",
  "poly", "polya", "gene", "genes", "refseq", "gencode", "annotation", "copy", "number", "cnv",
]);

function clamp(value, minimum, maximum) {
  return Math.max(minimum, Math.min(maximum, value));
}

function hslToRgb(hue, saturation, lightness) {
  const normalizedHue = ((hue % 360) + 360) % 360;
  const s = saturation / 100;
  const l = lightness / 100;
  const chroma = (1 - Math.abs(2 * l - 1)) * s;
  const x = chroma * (1 - Math.abs((normalizedHue / 60) % 2 - 1));
  const offset = l - chroma / 2;
  let red = 0;
  let green = 0;
  let blue = 0;

  if (normalizedHue < 60) [red, green] = [chroma, x];
  else if (normalizedHue < 120) [red, green] = [x, chroma];
  else if (normalizedHue < 180) [green, blue] = [chroma, x];
  else if (normalizedHue < 240) [green, blue] = [x, chroma];
  else if (normalizedHue < 300) [red, blue] = [x, chroma];
  else [red, blue] = [chroma, x];

  return `rgb(${Math.round((red + offset) * 255)}, ${Math.round((green + offset) * 255)}, ${Math.round((blue + offset) * 255)})`;
}

function normalize(value) {
  return String(value || "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

function sourceLabel(track = {}) {
  const config = track.config || {};
  const explicitSample = track.sample || track.sampleName || config.sample || config.sampleName;
  if (explicitSample) return String(explicitSample);

  const name = track.name || config.name || track.id || config.id;
  if (name) return String(name);

  const url = track.url || config.url;
  if (typeof url === "string") {
    const pathname = url.split(/[?#]/, 1)[0];
    const filename = pathname.split("/").pop();
    try { return decodeURIComponent(filename || pathname); }
    catch { return filename || pathname; }
  }
  return "track";
}

function descriptor(track = {}) {
  const config = track.config || {};
  return normalize([
    sourceLabel(track), track.assay, config.assay, track.type, config.type,
    track.format, config.format,
  ].filter(Boolean).join(" "));
}

function stableHash(value) {
  let hash = 2166136261;
  for (const character of String(value)) {
    hash ^= character.charCodeAt(0);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

export function assayTypeForTrack(track = {}) {
  const text = descriptor(track);
  const type = normalize(track.type || track.config?.type);

  if (/\bdnase\b|\bdhs\b/.test(text)) return "dnase";
  if (/\batac\b/.test(text)) return "atac";
  if (/\brna seq\b|\brnaseq\b|\bpoly a\b|\bpolya\b|\bgro seq\b|\bpro seq\b|\bcage\b/.test(text)) return "rna";
  if (/\bh[1-4][a-z0-9]*(?:ac|me[123])\b|\bhistone\b|\bchip seq\b|\bcut tag\b|\bcut run\b/.test(text)) return "histone";
  if (/\brefseq\b|\bgencode\b|\bgenes?\b|\btranscript(?:ome)?\b/.test(text)) return "gene";
  if (/\bmethyl|\bcpg\b|\bwgbs\b|\bbisulfite\b/.test(text)) return "methyl";
  if (/\bhi c\b|\bhic\b|\bbedpe\b|\binteract|\bloops?\b/.test(text) || type === "interact" || type === "arc") return "interaction";
  if (/\bcopy number\b|\bcnv\b/.test(text) || type === "seg") return "copyNumber";
  if (/\bvariants?\b|\bsnps?\b|\bindels?\b/.test(text) || type === "variant" || type === "gwas") return "variant";
  if (type === "alignment") return "alignment";
  return "other";
}

export function sampleKeyForTrack(track = {}) {
  const explicit = track.sample || track.sampleName || track.config?.sample || track.config?.sampleName;
  if (explicit) return normalize(explicit) || "sample";

  const original = normalize(sourceLabel(track));
  const words = original.split(" ").filter((word) => {
    if (!word || TECHNICAL_WORDS.has(word)) return false;
    if (/^h[1-4][a-z0-9]*(?:ac|me[123])$/.test(word)) return false;
    return true;
  });
  return words.join(" ") || original || "sample";
}

export function automaticTrackColor(track = {}) {
  const sampleKey = sampleKeyForTrack(track);
  const sampleHash = stableHash(sampleKey);
  const assayType = assayTypeForTrack(track);
  const themeKey = assayType === "other"
    ? FALLBACK_THEMES[sampleHash % FALLBACK_THEMES.length]
    : assayType;
  const theme = ASSAY_THEMES[themeKey];

  // Gene/reference annotations stay neutral; sample-bearing assays use
  // deterministic shade variants within their assay's color family.
  const variant = assayType === "gene"
    ? SAMPLE_VARIANTS[0]
    : SAMPLE_VARIANTS[(sampleHash >>> 3) % SAMPLE_VARIANTS.length];
  const saturation = clamp(theme.saturation + variant.saturation, 28, 92);
  const lightness = clamp(theme.lightness + variant.lightness, 28, 72);
  return hslToRgb(theme.hue, saturation, lightness);
}
