// Chromatin accessibility of a single-cell dataset (scripts/singlecell/fragments_to_viewer.py):
// Tn5 insertions per cell in fixed genomic bins, chunked so a gene window needs a few range reads.
import { fetchOk, fetchRange, type Slide } from './data';

export interface AtacMeta {
  file: string; binSize: number; chunkBins: number; assembly: string; nCells: number; genes: string;
  chromosomes: { name: string; length: number; chunk: number; chunks: number }[]; insertions: number;
  /** MACS3 peaks per group of `annotation` (scripts/singlecell/peaks_to_viewer.py) */
  peaks?: { dir: string; annotation: string; groups: string[]; caller: string; count: number; minNlog10q?: number };
}
/** [start, end, group index, summit offset, -log10 q] — 0-based, end exclusive */
export type Peak = [number, number, number, number, number];
/** [symbol, start, end, strand, type letter (p = protein coding, l = lncRNA), exons] — 0-based, end exclusive */
export type GeneModel = [string, number, number, '+' | '-', string, [number, number][]];
export interface Locus { chrom: string; start: number; end: number }
/** Insertions of every cell with any in [bin0, bin1): bin offset, cell index and count per entry. */
export interface Entries { bin0: number; nBins: number; bins: Uint32Array; cells: Uint32Array; counts: Uint8Array }

interface Chunk { bins: Uint16Array; cells: Uint32Array; counts: Uint8Array; perBin: Uint32Array }

const MAX_CHUNKS = 40; // decoded chunks kept in memory (~1–3 MB each)

async function gunzip(buf: ArrayBuffer) {
  const stream = new Blob([buf]).stream().pipeThrough(new DecompressionStream('gzip'));
  return new Response(stream).arrayBuffer();
}

export class Atac {
  meta!: AtacMeta;
  /** total insertions per cell, for normalization */
  totals!: Float32Array;
  private index!: Float64Array;
  private chunks = new Map<number, Promise<Chunk>>();
  private genes = new Map<string, Promise<GeneModel[]>>();
  private symbols?: Promise<Record<string, [string, number, number, string]>>;
  private peakFiles = new Map<string, Promise<Peak[]>>();

  constructor(private slide: Slide, private file: string) {}

  async load() {
    this.meta = await fetchOk(this.slide.url(this.file)).then((r) => r.json());
    const [idx, totals] = await Promise.all([
      fetchOk(this.slide.url(`${this.meta.file}.idx`)).then((r) => r.arrayBuffer()),
      fetchRange(this.slide.url(`${this.meta.file}.bin`), 0, this.meta.nCells * 4),
    ]);
    this.index = new Float64Array(idx);
    this.totals = new Float32Array(totals);
    return this;
  }

  chrom(name: string) { return this.meta.chromosomes.find((c) => c.name.toLowerCase() === name.toLowerCase()); }

  /** Where a gene is, by symbol (case-insensitive). */
  async locate(symbol: string): Promise<(Locus & { strand: string; name: string }) | null> {
    const all = await (this.symbols ??= fetchOk(this.slide.url(`${this.meta.genes}/index.json`)).then((r) => r.json()));
    const key = all[symbol] ? symbol : Object.keys(all).find((k) => k.toLowerCase() === symbol.toLowerCase());
    if (!key) return null;
    const [chrom, start, end, strand] = all[key];
    return { name: key, chrom, start, end, strand };
  }

  /** Gene models overlapping a locus. */
  async geneModels({ chrom, start, end }: Locus) {
    let p = this.genes.get(chrom);
    if (!p) {
      p = fetchOk(this.slide.url(`${this.meta.genes}/${chrom}.json`)).then((r) => r.json()).catch(() => []);
      this.genes.set(chrom, p);
    }
    return (await p).filter((g) => g[2] > start && g[1] < end);
  }

  /** MACS3 peaks overlapping a locus (empty when the dataset has none). */
  async peaks({ chrom, start, end }: Locus): Promise<Peak[]> {
    const dir = this.meta.peaks?.dir;
    if (!dir) return [];
    let p = this.peakFiles.get(chrom);
    if (!p) {
      p = fetchOk(this.slide.url(`${dir}/${chrom}.json`)).then((r) => r.json()).then((d: { peaks: Peak[] }) => d.peaks).catch(() => []);
      this.peakFiles.set(chrom, p);
    }
    return (await p).filter((k) => k[1] > start && k[0] < end);
  }

  private chunk(global: number): Promise<Chunk> {
    let p = this.chunks.get(global);
    if (p) { this.chunks.delete(global); this.chunks.set(global, p); return p; } // most recently used last
    const [offset, length] = [this.index[global * 2], this.index[global * 2 + 1]];
    const B = this.meta.chunkBins;
    p = fetchRange(this.slide.url(`${this.meta.file}.bin`), offset, length).then(gunzip).then((buf) => {
      const perBin = new Uint32Array(buf, 0, B);
      let total = 0;
      for (const n of perBin) total += n;
      const delta = new Uint32Array(buf, B * 4, total);
      const counts = new Uint8Array(buf, B * 4 + total * 4, total);
      const cells = new Uint32Array(total), bins = new Uint16Array(total);
      for (let b = 0, k = 0; b < B; b++) {
        let cell = 0;
        for (let j = 0; j < perBin[b]; j++, k++) {
          cell = j === 0 ? delta[k] : cell + delta[k];
          cells[k] = cell; bins[k] = b;
        }
      }
      return { bins, cells, counts, perBin };
    });
    p.catch(() => this.chunks.delete(global));
    this.chunks.set(global, p);
    while (this.chunks.size > MAX_CHUNKS) this.chunks.delete(this.chunks.keys().next().value!);
    return p;
  }

  /** Every cell's insertions in a locus (bins overlapping [start, end)). */
  async entries({ chrom, start, end }: Locus): Promise<Entries> {
    const c = this.chrom(chrom);
    const { binSize: S, chunkBins: B } = this.meta;
    const bin0 = Math.max(0, Math.floor(start / S)), bin1 = Math.max(bin0 + 1, Math.ceil(end / S));
    if (!c) return { bin0, nBins: bin1 - bin0, bins: new Uint32Array(0), cells: new Uint32Array(0), counts: new Uint8Array(0) };
    const first = Math.floor(bin0 / B), last = Math.min(c.chunks - 1, Math.floor((bin1 - 1) / B));
    const parts = await Promise.all(Array.from({ length: Math.max(0, last - first + 1) }, (_, i) => this.chunk(c.chunk + first + i)));
    let total = 0;
    const ranges = parts.map((p, i) => {
      // entries are ordered by bin: find the slice inside [bin0, bin1)
      const base = (first + i) * B, lo = Math.max(0, bin0 - base), hi = Math.min(B, bin1 - base);
      let a = 0, b = 0;
      for (let k = 0; k < lo; k++) a += p.perBin[k];
      b = a;
      for (let k = lo; k < hi; k++) b += p.perBin[k];
      total += b - a;
      return [a, b, base] as const;
    });
    const bins = new Uint32Array(total), cells = new Uint32Array(total), counts = new Uint8Array(total);
    let k = 0;
    parts.forEach((p, i) => {
      const [a, b, base] = ranges[i];
      for (let j = a; j < b; j++, k++) { bins[k] = base + p.bins[j] - bin0; cells[k] = p.cells[j]; counts[k] = p.counts[j]; }
    });
    return { bin0, nBins: bin1 - bin0, bins, cells, counts };
  }

  /** Per-cell accessibility of a locus: log1p(insertions / total × 10,000), like gene expression. */
  async regionValues(locus: Locus) {
    const e = await this.entries(locus);
    const raw = new Float32Array(this.meta.nCells);
    for (let k = 0; k < e.cells.length; k++) raw[e.cells[k]] += e.counts[k];
    for (let i = 0; i < raw.length; i++) if (raw[i]) raw[i] = Math.log1p((raw[i] / (this.totals[i] || 1)) * 1e4);
    return raw;
  }
}
