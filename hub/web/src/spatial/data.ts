// Reads a slide written by ATHENA's xenium_to_viewer.py / h5ad_to_viewer.py or the hub's convert.py
// through the hub's /hub/api/data/<dataset>/… (adapted from QuertermousLab/athena src/lib/spatial/data.ts).
// Small files are fetched whole; expression and image tiles with HTTP range requests.

import { workspace } from '../common';

export interface ObsSpec {
  key: string; label: string; type: 'category' | 'number'; dtype: 'uint8' | 'uint16' | 'float32';
  offset: number; categories?: string[]; min?: number; max?: number; group?: string; hidden?: boolean;
  /** the authors' colors, one per category */
  colors?: string[];
}
/** One assay of a (multiome) dataset: its features, matrix folder and how values are shown. */
export interface AssayMeta {
  key: string; label: string;
  /** rna, peaks, activity (gene activity), chromvar (motif deviations), protein */
  type: string;
  /** folder of expr.bin/expr.idx ('' = the slide folder) */
  dir: string;
  /** features.json ({names, labels?}); absent for the first assay when slide.json lists the genes */
  features?: string;
  exprDtype?: 'uint16' | 'float32'; total?: string; nFeatures?: number; normalization?: string;
  /** motifs.json ({id: {name, pwm}}) for chromVAR assays */
  motifs?: string;
}
export interface Motif { name: string; pwm: [number, number, number, number][] }

export interface ImageLevel { level: number; width: number; height: number; rows: number; cols: number; start: number }
export interface ImageChannel { name: string; file: string; color: string; visible: boolean; window: [number, number] }
export interface SlideMeta {
  version: number; id: string; title: string; technology: string; panel?: string; preservation?: string; run?: string;
  /** false: no tissue coordinates, the viewer shows the embeddings only (default true) */
  spatial?: boolean;
  /** what the matrix measures, e.g. "Gene expression" or "Gene activity" */
  feature?: string;
  species?: string;
  /** published data this slide was converted from */
  source?: { name: string; url: string; citation?: string } | null;
  /** chromatin accessibility per cell (scripts/singlecell/fragments_to_viewer.py) */
  atac?: { file: string; assembly: string; binSize: number };
  /** "float32": expression values stored as they are (no raw counts in the source); default uint16 counts */
  exprDtype?: 'uint16' | 'float32';
  /** true: the µm size of a coordinate unit is unknown (no scale bar) */
  scaleUnknown?: boolean;
  /** multiome: every assay (the first is the slide folder's expr.bin and `genes`) */
  assays?: AssayMeta[];
  nCells: number; pixelSize: number; width: number; height: number; genes: string[]; obs: ObsSpec[];
  defaultObs: string | null; radiusOffset: number; normalization: string;
  embeddings: { key: string; label: string; offset: number }[];
  regions: { name: string; cells: number[] }[];
  boundaries: { file: string; vertices: number } | null;
  image: { tileSize: number; format: string; levels: ImageLevel[]; channels: ImageChannel[] } | null;
}
export interface Project {
  id: string; title: string; url?: string; base?: string; mine?: boolean; ownerName?: string; description?: string; technology?: string; species?: string; tissue?: string;
  panel?: string; date?: string; citation?: string; doi?: string; sourceUrl?: string;
  /** false: viewer only, not listed on the Data pages (e.g. published data shown for convenience) */
  dataPage?: boolean;
  slides: { id: string; title?: string; description?: string; base?: string }[];
  /** Order, titles and notes for the download subfolders (folder = subfolder name). */
  downloadGroups?: { folder: string; title?: string; description?: string }[];
  downloads: { path: string; name: string; group: string; size: number; modified?: string }[];
}

/** A file of a dataset folder served by the hub. */
export const fileUrl = (base: string, path: string) => `${base}/${path.split('/').map(encodeURIComponent).join('/')}`;

async function get(url: string, init?: RequestInit) {
  const res = await fetch(url, { credentials: 'same-origin', ...init });
  if (!res.ok) {
    const msg = await res.json().then((d: { error?: string }) => d.error).catch(() => null);
    throw new Error(msg ?? `${url}: ${res.status}`);
  }
  return res;
}

async function range(url: string, start: number, length: number): Promise<ArrayBuffer> {
  const res = await get(url, { headers: { range: `bytes=${start}-${start + length - 1}` } });
  const buf = await res.arrayBuffer();
  // a server that ignores Range sends the whole file
  return res.status === 200 && buf.byteLength > length ? buf.slice(start, start + length) : buf;
}

export type Kind = 'spatial' | 'singlecell';

/** The workspace's datasets of this viewer, plus `include` (a dataset opened by its link, maybe another workspace's). */
export const listProjects = (kind: Kind = 'spatial', include = '') => get(`/hub/api/projects?kind=${kind}&include=${encodeURIComponent(include)}`, {
  headers: workspace() ? { 'x-vizhub-workspace': workspace()!.id } : {},
}).then((r) => r.json() as Promise<Project[]>);

export class Slide {
  readonly base: string;
  meta!: SlideMeta;
  private cells!: ArrayBuffer;
  private geneIndex!: Float64Array;
  private geneCache = new Map<string, Promise<{ idx: Uint32Array; counts: Uint16Array | Float32Array }>>();
  /** the assay shown now, its feature names (and display labels, e.g. motif names) */
  assay!: AssayMeta;
  features: string[] = [];
  labels: string[] | null = null;
  private featureAt = new Map<string, number>();
  private assayData = new Map<string, Promise<{ index: Float64Array; names: string[]; labels: string[] | null }>>();
  private motifTable?: Promise<Record<string, Motif>>;
  private tileIndex = new Map<string, Promise<Float64Array>>();
  private ids?: Promise<string[]>;
  private outlines?: Promise<{ starts: Uint32Array; xy: Float32Array }>;

  /** base: the slide folder's URL, e.g. /hub/api/data/<dataset>[/<slide>] */
  constructor(base: string) {
    this.base = base;
  }

  url(file: string) { return fileUrl(this.base, file); }

  async load(assay?: string) {
    this.meta = await get(this.url('slide.json')).then((r) => r.json());
    // opened without ?assay=: expression first, chromVAR deviations last
    const rank = (a: AssayMeta) => ['rna', 'activity', 'peaks', 'protein', 'chromvar'].indexOf(a.type) + 1 || 9;
    const first = this.assays.find((a) => a.key === assay) ?? [...this.assays].sort((x, y) => rank(x) - rank(y))[0];
    [this.cells] = await Promise.all([get(this.url('cells.bin')).then((r) => r.arrayBuffer()), this.setAssay(first.key)]);
    return this;
  }

  /** The dataset's assays; a dataset written before multiome support has one. */
  get assays(): AssayMeta[] {
    return this.meta.assays?.length ? this.meta.assays : [{
      key: 'main', label: this.meta.feature ?? 'Gene expression', type: this.meta.atac ? 'activity' : 'rna', dir: '',
      exprDtype: this.meta.exprDtype, total: 'total_counts',
    }];
  }

  private file(a: AssayMeta, name: string) { return a.dir ? `${a.dir}/${name}` : name; }

  /** Switch the features and matrix that geneCounts/expression read. */
  async setAssay(key: string) {
    const a = this.assays.find((x) => x.key === key) ?? this.assays[0];
    let p = this.assayData.get(a.key);
    if (!p) {
      p = Promise.all([
        get(this.url(this.file(a, 'expr.idx'))).then((r) => r.arrayBuffer()).then((b) => new Float64Array(b)),
        a.features ? get(this.url(a.features)).then((r) => r.json() as Promise<{ names: string[]; labels?: string[] }>)
          : Promise.resolve({ names: this.meta.genes }),
      ]).then(([index, f]) => ({ index, names: f.names, labels: (f as { labels?: string[] }).labels ?? null }));
      p.catch(() => this.assayData.delete(a.key));
      this.assayData.set(a.key, p);
    }
    const d = await p;
    this.assay = a;
    this.geneIndex = d.index;
    this.features = d.names;
    this.labels = d.labels;
    this.featureAt = new Map(d.names.map((n, i) => [n, i]));
    return a;
  }

  /** {id: {name, pwm}} of the current chromVAR assay's motifs (empty without). */
  motifs(): Promise<Record<string, Motif>> {
    const f = this.assays.find((a) => a.motifs)?.motifs;
    return (this.motifTable ??= f ? get(this.url(f)).then((r) => r.json()) : Promise.resolve({}));
  }

  /** The display name of a feature (motif name for chromVAR). */
  label(feature: string) {
    const i = this.featureAt.get(feature);
    return i != null && this.labels ? this.labels[i] : feature;
  }

  get n() { return this.meta.nCells; }
  /** expression stored as float32 values (no counts): shown as they are, never log-normalized */
  get floatValues() { return (this.assay?.exprDtype ?? this.meta.exprDtype) === 'float32'; }
  get positions() { return new Float32Array(this.cells, 0, this.n * 2); }
  get radius() { return new Float32Array(this.cells, this.meta.radiusOffset, this.n); }
  embedding(key: string) {
    const e = this.meta.embeddings.find((x) => x.key === key);
    return e ? new Float32Array(this.cells, e.offset, this.n * 2) : null;
  }
  spec(key: string) { return this.meta.obs.find((o) => o.key === key); }
  obs(key: string): Uint8Array | Uint16Array | Float32Array {
    const s = this.spec(key)!;
    const T = s.dtype === 'uint8' ? Uint8Array : s.dtype === 'uint16' ? Uint16Array : Float32Array;
    return new T(this.cells, s.offset, this.n);
  }

  /** Raw counts (or stored values) of one gene: cell indices and values of the non-zero cells. */
  geneCounts(gene: string) {
    const key = `${this.assay.key}\n${gene}`;
    let p = this.geneCache.get(key);
    if (!p) {
      const g = this.featureAt.get(gene) ?? -1;
      if (g < 0) return Promise.reject(new Error(`Unknown feature ${gene}`));
      const [offset, nnz] = [this.geneIndex[g * 2], this.geneIndex[g * 2 + 1]];
      const float = this.floatValues, width = float ? 4 : 2;
      p = (nnz ? range(this.url(this.file(this.assay, 'expr.bin')), offset, nnz * (4 + width)) : Promise.resolve(new ArrayBuffer(0))).then((buf) => ({
        idx: new Uint32Array(buf, 0, nnz), counts: float ? new Float32Array(buf, nnz * 4, nnz) : new Uint16Array(buf, nnz * 4, nnz),
      }));
      p.catch(() => this.geneCache.delete(key));
      this.geneCache.set(key, p);
    }
    return p;
  }

  /** Per-cell expression: log-normalized (Seurat LogNormalize, scale 10,000) or raw counts. */
  async expression(gene: string, mode: 'log' | 'raw') {
    const { idx, counts } = await this.geneCounts(gene);
    const out = new Float32Array(this.n);
    const total = mode === 'log' && !this.floatValues ? this.obs(this.assay.total ?? 'total_counts') : null;
    for (let i = 0; i < idx.length; i++) {
      const c = idx[i];
      out[c] = total ? Math.log1p((counts[i] / (total[c] || 1)) * 1e4) : counts[i];
    }
    return { values: out, nonzero: idx };
  }

  cellIds() {
    return (this.ids ??= get(this.url('cell_ids.txt')).then((r) => r.text()).then((t) => t.split('\n').slice(0, this.n)));
  }

  boundaries() {
    return (this.outlines ??= get(this.url(this.meta.boundaries!.file)).then((r) => r.arrayBuffer()).then((buf) => ({
      starts: new Uint32Array(buf, 0, this.n + 1),
      xy: new Float32Array(buf, (this.n + 1) * 4),
    })));
  }

  /** 8-bit grey pixels of one image tile, or null where the tile is empty. */
  async tile(channel: number, level: number, x: number, y: number, signal?: AbortSignal): Promise<Uint8Array | null> {
    const img = this.meta.image!;
    const lv = img.levels.find((l) => l.level === level);
    if (!lv || x < 0 || y < 0 || x >= lv.cols || y >= lv.rows) return null;
    const file = img.channels[channel].file;
    let index = this.tileIndex.get(file);
    if (!index) {
      index = get(this.url(`${file}.idx`)).then((r) => r.arrayBuffer()).then((b) => new Float64Array(b));
      this.tileIndex.set(file, index);
    }
    const t = lv.start + y * lv.cols + x;
    const idx = await index;
    const [offset, length] = [idx[t * 2], idx[t * 2 + 1]];
    if (!length) return null;
    const res = await get(this.url(`${file}.bin`), { headers: { range: `bytes=${offset}-${offset + length - 1}` }, signal });
    const bitmap = await createImageBitmap(await res.blob());
    const size = img.tileSize;
    const canvas = new OffscreenCanvas(size, size);
    const ctx = canvas.getContext('2d', { willReadFrequently: true })!;
    ctx.drawImage(bitmap, 0, 0);
    bitmap.close();
    const rgba = ctx.getImageData(0, 0, size, size).data;
    const grey = new Uint8Array(size * size);
    for (let i = 0; i < grey.length; i++) grey[i] = rgba[i * 4];
    return grey;
  }
}

export const fmtBytes = (n: number) => {
  if (n < 1024) return `${n} B`;
  const u = ['KB', 'MB', 'GB', 'TB'];
  let i = -1;
  do { n /= 1024; i++; } while (n >= 1024 && i < u.length - 1);
  return `${n.toFixed(n < 10 ? 1 : 0)} ${u[i]}`;
};

/** Shared with atac.ts: a fetch that throws on HTTP errors, and a byte-range read. */
export { get as fetchOk, range as fetchRange };
