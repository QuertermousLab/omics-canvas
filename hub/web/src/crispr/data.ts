// CRISPR screen results written by ATHENA's igvf_to_viewer.py or the hub's crispr_to_viewer.py, read through
// /hub/api/data/<screen>/… (adapted from QuertermousLab/athena src/lib/crispr/data.ts).
import { workspace } from '../common';
import { fetchOk, fetchRange, fileUrl } from '../spatial/data';

export interface Screen {
  id: string; title: string; url?: string; base: string; fdr?: number; mine?: boolean; ownerName?: string; description?: string; kind: 'enhancer' | 'perturbseq'; species?: string; assembly?: string;
  cell?: string; citation?: string; method?: string; sources?: { accession: string; url: string | null; path?: string }[];
  stats?: Record<string, number>;
  /** a screen of a combined dataset: its id there (?s=), the combined dataset's name and description */
  sample?: string; group?: string; groupDescription?: string;
  /** /hub/api/arcs/<dataset>[/<screen>]: enhancer-gene loops */
  arcs?: string;
  /** interactive HTML reports of the screen (e.g. PerturbNMF gene program explorer): added in the hub or found */
  reports?: { title: string; url: string; found: boolean }[];
}

/** One enhancer–gene test (TAP-seq). p values as -log10. */
export interface Test {
  chrom: string; start: number; end: number; gene: string; geneId: string; log2fc: number;
  nlog10p: number; nlog10padj: number; significant: boolean; set: string; guides: number;
  /** element name (hub conversions); used when the element has no coordinates */
  element?: string;
}

export interface Target {
  name: string; geneId: string; chrom: string; start: number; end: number; guides: number;
  knockdown: { log2fc: number; nlog10padj: number; significant: boolean } | null;
  tested: number; up: number; down: number;
}

/** Rows of a Perturb-seq block: the other side's index, log2FC, -log10 p, -log10 adj p. */
export interface Effects { index: Uint32Array; log2fc: Float32Array; nlog10p: Float32Array; nlog10padj: Float32Array }

export const SIGNIFICANT = 1; // -log10(0.1): FDR (SCEPTRE adjusted P) < 0.1

export const listScreens = (include = '') => fetchOk(`/hub/api/projects?kind=crispr&include=${encodeURIComponent(include)}`, {
  headers: workspace() ? { 'x-vizhub-workspace': workspace()!.id } : {},
}).then((r) => r.json() as Promise<Screen[]>);

const url = (base: string, file: string) => fileUrl(base, file);

export async function loadTests(base: string): Promise<Test[]> {
  const t = await fetchOk(url(base, 'tests.json')).then((r) => r.json()) as { columns: string[]; rows: unknown[][] };
  return t.rows.map((r) => Object.fromEntries(t.columns.map((c, i) => [c, r[i]])) as unknown as Test);
}

export class PerturbSeq {
  genes: string[] = [];
  /** Ensembl ids of the readout genes when the source named them by id ('' where unknown) */
  geneIds: string[] = [];
  targets: Target[] = [];
  private idx: Record<'de' | 'bygene', Float64Array> = { de: new Float64Array(0), bygene: new Float64Array(0) };
  private cache = new Map<string, Promise<Effects>>();

  /** screen: the screen folder's URL (/hub/api/data/<screen>) */
  constructor(readonly screen: string) {}

  async load() {
    const [t, de, bygene] = await Promise.all([
      fetchOk(url(this.screen, 'targets.json')).then((r) => r.json()),
      fetchOk(url(this.screen, 'de.idx')).then((r) => r.arrayBuffer()),
      fetchOk(url(this.screen, 'bygene.idx')).then((r) => r.arrayBuffer()),
    ]);
    this.genes = t.genes;
    this.geneIds = t.geneIds ?? [];
    this.targets = t.targets;
    this.idx = { de: new Float64Array(de), bygene: new Float64Array(bygene) };
    return this;
  }

  private block(file: 'de' | 'bygene', i: number): Promise<Effects> {
    const key = `${file}:${i}`;
    let p = this.cache.get(key);
    if (!p) {
      const [offset, rows] = [this.idx[file][i * 2], this.idx[file][i * 2 + 1]];
      p = (rows ? fetchRange(url(this.screen, `${file}.bin`), offset, rows * 16) : Promise.resolve(new ArrayBuffer(0))).then((buf) => {
        const u = new Uint32Array(buf), f = new Float32Array(buf), n = buf.byteLength / 16;
        const e: Effects = { index: new Uint32Array(n), log2fc: new Float32Array(n), nlog10p: new Float32Array(n), nlog10padj: new Float32Array(n) };
        for (let k = 0; k < n; k++) { e.index[k] = u[k * 4]; e.log2fc[k] = f[k * 4 + 1]; e.nlog10p[k] = f[k * 4 + 2]; e.nlog10padj[k] = f[k * 4 + 3]; }
        return e;
      });
      p.catch(() => this.cache.delete(key));
      this.cache.set(key, p);
    }
    return p;
  }

  /** Every readout gene tested for one target (strongest first); index = gene. */
  effectsOfTarget(target: number) { return this.block('de', target); }

  /** Every target tested for one readout gene (strongest first); index = target. */
  effectsOnGene(gene: number) { return this.block('bygene', gene); }

  geneIndex(symbol: string) {
    const s = symbol.trim().toLowerCase();
    return this.genes.findIndex((g) => g.toLowerCase() === s);
  }
}

/** Genome Canvas genome id for a screen: its assembly (GRCh38, hg19, GRCm39 …) or, without one, its species. */
export function canvasGenome(species: string | undefined, assembly: string | undefined): string | null {
  const a = (assembly ?? '').toLowerCase().replace(/\.p\d+$/, '');
  const ids: Record<string, string> = { grch38: 'hg38', hg38: 'hg38', grch37: 'hg19', hg19: 'hg19', grcm39: 'mm39', mm39: 'mm39', grcm38: 'mm10', mm10: 'mm10' };
  if (ids[a]) return ids[a];
  return species === 'Mouse' ? 'mm39' : 'hg38';
}

/** /genome-canvas/?locus=…&genome=…&highlight=… — Genome Canvas opens there (regions are 1-based, inclusive).
 *  The Omics Canvas Mac app hands these links to Genome Canvas.app (genomecanvas://) when it is installed. */
export function canvasURL(o: { locus: string; genome: string | null; highlight?: string[]; color?: string; tracks?: { name: string; url: string }[] }) {
  const q = new URLSearchParams({ locus: o.locus });
  if (o.genome) q.set('genome', o.genome);
  for (const h of o.highlight ?? []) q.append('highlight', h);
  for (const t of o.tracks ?? []) q.append('track', `${t.name.replace(/\|/g, '/')}|${t.url}`);
  if (o.color) q.set('color', o.color.replace(/^#/, ''));
  return `/genome-canvas/?${q}`;
}

/** The screen's enhancer -> target-gene TSS links as two Genome Canvas loop tracks (hub /hub/api/arcs, hg38 gene
 *  table): significant pairs and all tested pairs, arc height = |log2FC| — the ATHENA locus view's CRISPR arcs. */
export const enhancerArcTracks = (screen: { id: string; title: string; arcs?: string }, genome: string | null) =>
  genome === 'hg38' ? (['significant', 'all'] as const).map((which) => ({
    name: `${screen.title} · ${which === 'significant' ? 'significant links' : 'all tested links'}`,
    url: `${screen.arcs ?? `/hub/api/arcs/${encodeURIComponent(screen.id)}`}/${which}.bedpe`,
  })) : [];

/** BED-style element (0-based start) → 1-based region text */
export const region = (chrom: string, start: number, end: number) =>
  `${/^chr/i.test(chrom) || !/^(\d+|[XYM]|MT)$/i.test(chrom) ? chrom : `chr${chrom}`}:${Math.max(1, start + 1)}-${end}`;

/** Gene → page links shared by both screen kinds: Genome Canvas (searches the gene in the screen's genome)
 *  and the first single-cell dataset in the hub of the same species. */
export function geneLinks(gene: string, species: string | undefined, assembly: string | undefined,
  singleCell: { url?: string; species?: string; slides: { id: string }[] }[], tracks: { name: string; url: string }[] = []) {
  const human = species !== 'Mouse';
  const sc = singleCell.find((p) => (p.species ?? '').toLowerCase().startsWith(human ? 'h' : 'm')) ?? null;
  return {
    locus: canvasURL({ locus: gene, genome: canvasGenome(species, assembly), tracks }),
    singleCell: sc?.url ? `${sc.url}?gene=${encodeURIComponent(gene)}` : null,
  };
}
