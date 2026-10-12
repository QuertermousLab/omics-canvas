export const PUBLIC_HUBS = Object.freeze([
  {
    id: "ucsc-encode-dna",
    source: "UCSC",
    kind: "ucsc",
    name: "ENCODE DNA Track Hub",
    description: "DNA-based ENCODE assays, including accessibility and transcription-factor binding tracks.",
    assemblies: ["hg19", "hg38", "mm10"],
    url: "https://storage.googleapis.com/gcp.wenglab.org/hubs/dna20/hub.txt",
  },
  {
    id: "ucsc-gtex-analysis",
    source: "UCSC",
    kind: "ucsc",
    name: "GTEx Analysis",
    description: "Allele-specific expression density and tissue annotations from the GTEx analysis hub.",
    assemblies: ["hg38"],
    trackCount: 55,
    url: "https://hgdownload.soe.ucsc.edu/hubs/gtexAnalysis/hub.txt",
  },
  {
    id: "washu-roadmap-states",
    source: "WashU",
    kind: "washu",
    name: "Roadmap Chromatin States",
    description: "Observed and imputed ChromHMM state annotations from the Roadmap Epigenomics Consortium.",
    assemblies: ["hg38"],
    trackCount: 352,
    url: "https://vizhub.wustl.edu/public/hg38/roadmap_hmm.json",
  },
  {
    id: "washu-encode-hic",
    source: "WashU",
    kind: "washu",
    name: "ENCODE Hi-C",
    description: "Public ENCODE chromatin-interaction matrices configured by the WashU Epigenome Browser.",
    assemblies: ["hg38"],
    trackCount: 20,
    url: "https://vizhub.wustl.edu/public/hg38/new/GRCh38_encode_human_hic_metadata_nov142018.json",
  },
  {
    id: "washu-4dn-mouse",
    source: "WashU",
    kind: "washu",
    name: "4DN Mouse Hi-C",
    description: "Mouse chromatin-interaction datasets from the 4D Nucleome Network.",
    assemblies: ["mm10"],
    trackCount: 23,
    url: "https://vizhub.wustl.edu/public/mm10/4dn_mm10.json",
  },
]);

const FORMAT_ALIASES = Object.freeze({
  bw: "bigwig",
  bigwig: "bigwig",
  wig: "wig",
  bedgraph: "bedgraph",
  bb: "bigbed",
  bigbed: "bigbed",
  biggenepred: "bigbed",
  bed: "bed",
  categorical: "bed",
  refbed: "bed",
  narrowpeak: "narrowpeak",
  broadpeak: "broadpeak",
  gff: "gff3",
  gff3: "gff3",
  gtf: "gtf",
  bam: "bam",
  cram: "cram",
  vcf: "vcf",
  vcftabix: "vcf",
  bedpe: "bedpe",
  longrange: "bedpe",
  hic: "hic",
  seg: "seg",
  qtl: "qtl",
  gwas: "gwas",
});

const SUPPORTED_FORMATS = new Set(Object.values(FORMAT_ALIASES));

function textValue(value) {
  if (Array.isArray(value)) return value.map(textValue).filter(Boolean).join(" · ");
  if (value && typeof value === "object") return Object.values(value).map(textValue).filter(Boolean).join(" · ");
  return value == null ? "" : String(value);
}

function lastMetadataValue(metadata, key) {
  const value = metadata?.[key] ?? metadata?.[key.toLowerCase()] ?? metadata?.[key.toUpperCase()];
  return Array.isArray(value) ? value.at(-1) : value;
}

function encodeFileAccession(url) {
  const match = String(url || "").match(/(?:\/files\/|\/)(ENCFF[A-Z0-9]+)(?:\/|\.|$)/i);
  return match ? match[1].toUpperCase() : "";
}

export function hubsForGenome(genome, query = "") {
  const needle = String(query).trim().toLowerCase();
  return PUBLIC_HUBS.filter((hub) => {
    if (!hub.assemblies.includes(genome)) return false;
    if (!needle) return true;
    return [hub.source, hub.name, hub.description, ...hub.assemblies]
      .some((value) => String(value).toLowerCase().includes(needle));
  });
}

export function inferHubKind(url) {
  const pathname = String(url || "").split(/[?#]/, 1)[0].toLowerCase();
  return pathname.endsWith(".json") ? "washu" : "ucsc";
}

export function inferTrackFormat(url = "", declaredType = "") {
  const declared = String(declaredType).toLowerCase().replace(/[^a-z0-9]/g, "");
  if (FORMAT_ALIASES[declared]) return FORMAT_ALIASES[declared];

  const pathname = String(url).split(/[?#]/, 1)[0].toLowerCase();
  const suffixes = [
    [".bedgraph.gz", "bedgraph"], [".bedgraph", "bedgraph"],
    [".narrowpeak.gz", "narrowpeak"], [".narrowpeak", "narrowpeak"],
    [".broadpeak.gz", "broadpeak"], [".broadpeak", "broadpeak"],
    [".vcf.gz", "vcf"], [".bedpe.gz", "bedpe"], [".bed.gz", "bed"],
    [".bigwig", "bigwig"], [".bw", "bigwig"], [".bigbed", "bigbed"], [".bb", "bigbed"],
    [".hic", "hic"], [".bam", "bam"], [".cram", "cram"], [".vcf", "vcf"],
    [".gff3", "gff3"], [".gff", "gff3"], [".gtf", "gtf"], [".bedpe", "bedpe"],
    [".bed", "bed"], [".wig", "wig"], [".seg", "seg"],
  ];
  return suffixes.find(([suffix]) => pathname.endsWith(suffix))?.[1] || "";
}

function collectWashUTrackObjects(payload) {
  if (Array.isArray(payload)) return payload.flatMap(collectWashUTrackObjects);
  if (!payload || typeof payload !== "object") return [];
  if (typeof payload.url === "string") return [payload];
  for (const key of ["tracks", "children", "data", "trackList"]) {
    if (Array.isArray(payload[key])) return collectWashUTrackObjects(payload[key]);
  }
  return [];
}

export function normalizeWashUHub(payload, hub = {}) {
  const seen = new Set();
  const tracks = [];
  let skipped = 0;
  const rawTracks = collectWashUTrackObjects(payload);
  const duplicateNames = new Map();

  for (const [index, raw] of rawTracks.entries()) {
    const format = inferTrackFormat(raw.url, raw.type || raw.filetype);
    if (!format || !SUPPORTED_FORMATS.has(format)) continue;
    const name = String(raw.name || raw.label || `Track ${index + 1}`).trim();
    const key = name.toLocaleLowerCase();
    duplicateNames.set(key, (duplicateNames.get(key) || 0) + 1);
  }

  for (const [index, raw] of rawTracks.entries()) {
    const format = inferTrackFormat(raw.url, raw.type || raw.filetype);
    if (!format || !SUPPORTED_FORMATS.has(format)) {
      skipped += 1;
      continue;
    }

    const baseName = String(raw.name || raw.label || `Track ${index + 1}`).trim();
    const accession = encodeFileAccession(raw.url);
    const name = duplicateNames.get(baseName.toLocaleLowerCase()) > 1 && accession
      ? `${baseName} · ${accession}`
      : baseName;
    const key = `${raw.url}\n${baseName}`;
    if (seen.has(key)) continue;
    seen.add(key);

    const sample = textValue(lastMetadataValue(raw.metadata, "Sample"));
    const assay = textValue(lastMetadataValue(raw.metadata, "Assay") || lastMetadataValue(raw.metadata, "Type"));
    const config = {
      id: `${hub.id || "washu"}-${index}`,
      name,
      url: raw.url,
      format,
      accession: accession || undefined,
      sample: sample || undefined,
      assay: assay || undefined,
      description: [sample, assay].filter(Boolean).join(" · ") || hub.description,
      hubGroup: textValue(raw.metadata?.Project || raw.metadata?.Assay) || hub.name || "WashU data hub",
      genomeCanvasAutoColor: true,
    };

    if (raw.indexURL || raw.indexUrl) config.indexURL = raw.indexURL || raw.indexUrl;
    if (Number.isFinite(raw.options?.height)) config.height = Math.max(24, Math.min(240, raw.options.height));
    if (format === "hic") config.type = "interact";
    tracks.push(config);
  }

  return { tracks, skipped };
}

export function flattenUCSCHubGroups(groups = [], hub = {}) {
  const tracks = [];
  const seen = new Set();

  const visit = (container, parents = []) => {
    if (!container) return;
    const label = String(container.label || container.name || "").trim();
    const path = label ? [...parents, label] : parents;
    for (const [index, track] of (container.tracks || []).entries()) {
      if (!track?.url) continue;
      const key = `${track.url}\n${track.name || track.id || index}`;
      if (seen.has(key)) continue;
      seen.add(key);
      const config = {
        ...track,
        id: track.id || `${hub.id || "ucsc"}-${tracks.length}`,
        name: track.name || track.id || `Track ${tracks.length + 1}`,
        hubGroup: path.filter(Boolean).join(" / ") || hub.name || "UCSC track hub",
        genomeCanvasAutoColor: true,
      };
      delete config.visible;
      tracks.push(config);
    }
    for (const child of container.children || []) visit(child, path);
  };

  for (const group of groups || []) visit(group);
  return tracks;
}

export function filterHubTracks(tracks, query = "") {
  const needle = String(query).trim().toLowerCase();
  if (!needle) return [...tracks];
  return tracks.filter((track) => [
    track.name, track.format, track.hubGroup, track.sample, track.assay, track.description,
  ].some((value) => String(value || "").toLowerCase().includes(needle)));
}
