import { css } from '../common';
// Coverage tracks under the single-cell viewer: chromatin accessibility around a gene for each visible
// category of the annotation chosen in the side panel (its colors), plus the current selection.
// Recomputed from per-cell insertions whenever the viewer changes, so it always follows the side panel.
import type { Atac, Entries, GeneModel, Locus, Peak } from './atac';
import type { Viewer } from './viewer';

const MAX_TRACKS = 12;
const TRACK_H = 38, RULER_H = 22, GENE_ROW = 28, LABEL_W = 170, PAD_R = 46, PEAK_ROW = 12, PEAK_HEAD = 16;
const selectedColor = () => css('--text');

interface Track { name: string; color: string; cells: number; cov: Float32Array; max: number }

export const fmtLocus = ({ chrom, start, end }: Locus) =>
  `${chrom}:${Math.round(start + 1).toLocaleString('en-US')}-${Math.round(end).toLocaleString('en-US')}`;

export function parseLocus(s: string): Locus | null {
  const m = s.trim().replace(/,/g, '').match(/^(chr[\w]+):(\d+)-(\d+)$/i);
  if (!m) return null;
  const start = +m[2] - 1, end = +m[3];
  return end > start ? { chrom: m[1], start, end } : null;
}

export class AtacPanel {
  locus: Locus | null = null;
  shared = true;
  /** peaks drawn: -log10 q at least this */
  peakMinQ = 5;
  /** a region marked by dragging over the tracks, in bp */
  mark: [number, number] | null = null;
  onMark: (mark: [number, number] | null) => void = () => {};
  onInfo: (text: string) => void = () => {};
  private entries: Entries | null = null;
  private models: GeneModel[] = [];
  private peaks: Peak[] = [];
  /** where the peak rows were drawn: y of the first row and the group shown in each row */
  private peakRows: { top: number; groups: number[] } = { top: 0, groups: [] };
  private tracks: Track[] = [];
  private hidden = 0; // visible categories beyond MAX_TRACKS
  private loadId = 0;
  private drag: number | null = null;
  private ctx: CanvasRenderingContext2D;

  constructor(private canvas: HTMLCanvasElement, private tip: HTMLElement, private v: Viewer, readonly atac: Atac) {
    this.ctx = canvas.getContext('2d')!;
    canvas.addEventListener('pointerdown', (e) => this.pointerDown(e));
    canvas.addEventListener('pointermove', (e) => this.pointerMove(e));
    canvas.addEventListener('pointerup', (e) => this.pointerUp(e));
    canvas.addEventListener('pointerleave', () => (this.tip.hidden = true));
    new ResizeObserver(() => this.draw()).observe(canvas.parentElement!);
  }

  // ------------------------------------------------------------- navigation

  /** Center on a gene: its body plus `flank` bp on each side. */
  async goToGene(symbol: string, flank = 50_000) {
    const g = await this.atac.locate(symbol);
    if (!g) { this.onInfo(`${symbol} is not in the ${this.atac.meta.assembly} gene annotation`); return null; }
    await this.goTo({ chrom: g.chrom, start: Math.max(0, g.start - flank), end: g.end + flank });
    return g;
  }

  async goTo(locus: Locus) {
    const c = this.atac.chrom(locus.chrom);
    if (!c) { this.onInfo(`${locus.chrom} is not in this dataset`); return; }
    const width = Math.min(Math.max(locus.end - locus.start, 2_000), 2_000_000);
    const mid = (locus.start + locus.end) / 2;
    const start = Math.max(0, Math.min(c.length - width, mid - width / 2));
    this.locus = { chrom: c.name, start: Math.round(start), end: Math.round(start + width) };
    this.setMark(null);
    const id = ++this.loadId;
    this.onInfo('Loading…');
    try {
      const [entries, models, peaks] = await Promise.all([this.atac.entries(this.locus), this.atac.geneModels(this.locus), this.atac.peaks(this.locus)]);
      if (id !== this.loadId) return;
      this.entries = entries;
      this.models = models;
      this.peaks = peaks;
      this.refresh();
    } catch (e) {
      if (id === this.loadId) this.onInfo((e as Error).message);
    }
  }

  move(fraction: number) {
    if (!this.locus) return;
    const w = this.locus.end - this.locus.start, d = w * fraction;
    this.goTo({ ...this.locus, start: this.locus.start + d, end: this.locus.end + d });
  }

  zoom(factor: number) {
    if (!this.locus) return;
    const mid = (this.locus.start + this.locus.end) / 2, w = (this.locus.end - this.locus.start) * factor;
    this.goTo({ ...this.locus, start: mid - w / 2, end: mid + w / 2 });
  }

  // ------------------------------------------------------------- tracks from the viewer state

  /** Rebuild the tracks from the viewer's annotation, visible categories, colors and selection. */
  refresh() {
    const e = this.entries;
    if (!e || !this.locus) { this.draw(); return; }
    const v = this.v, n = v.slide.n, totals = this.atac.totals;
    const spec = v.slide.spec(v.groupBy);
    const sel = v.selection, cropped = v.cropped && !!sel;
    const tracks: Track[] = [];
    const trackOf = new Int16Array(n).fill(-1);

    if (spec?.type === 'category') {
      const codes = v.slide.obs(spec.key), vis = v.catVisible.get(spec.key)!, colors = v.catColors.get(spec.key)!;
      const counts = v.categoryCounts(spec.key, cropped);
      const shown = spec.categories!.map((_, i) => i).filter((i) => vis[i] && counts[i] > 0).sort((a, b) => counts[b] - counts[a]);
      this.hidden = Math.max(0, shown.length - MAX_TRACKS);
      const slot = new Int16Array(spec.categories!.length).fill(-1);
      shown.slice(0, MAX_TRACKS).forEach((c, t) => {
        slot[c] = t;
        tracks.push({ name: spec.categories![c], color: colors[c], cells: counts[c], cov: new Float32Array(e.nBins), max: 0 });
      });
      for (let i = 0; i < n; i++) if (!cropped || sel![i]) trackOf[i] = slot[codes[i]];
    }
    let selTrack: Track | null = null;
    if (sel) {
      let k = 0;
      for (let i = 0; i < n; i++) k += sel[i];
      selTrack = { name: 'Selected cells', color: selectedColor(), cells: k, cov: new Float32Array(e.nBins), max: 0 };
    }

    // insertions per track and bin, then per million insertions of the track's cells
    const sum = new Float64Array(tracks.length);
    let selSum = 0;
    for (let i = 0; i < n; i++) {
      if (trackOf[i] >= 0) sum[trackOf[i]] += totals[i];
      if (selTrack && sel![i]) selSum += totals[i];
    }
    for (let k = 0; k < e.cells.length; k++) {
      const c = e.cells[k], t = trackOf[c];
      if (t >= 0) tracks[t].cov[e.bins[k]] += e.counts[k];
      if (selTrack && sel![c]) selTrack.cov[e.bins[k]] += e.counts[k];
    }
    const norm = (t: Track, total: number) => {
      const f = total ? 1e6 / total : 0;
      for (let b = 0; b < t.cov.length; b++) { t.cov[b] *= f; t.max = Math.max(t.max, t.cov[b]); }
    };
    tracks.forEach((t, i) => norm(t, sum[i]));
    if (selTrack) norm(selTrack, selSum);
    this.tracks = selTrack ? [selTrack, ...tracks] : tracks;
    this.onInfo(`${fmtLocus(this.locus)} · ${this.atac.meta.assembly} · insertions per million, ${this.atac.meta.binSize} bp bins${this.hidden ? ` · ${this.hidden} more categories (hide some to choose)` : ''}`);
    this.draw();
  }

  // ------------------------------------------------------------- drawing

  private geneRows() {
    const rows: number[] = [], placed: { g: GeneModel; row: number }[] = [];
    const L = this.locus!, w = this.plotW(), bp = (L.end - L.start) / w;
    for (const g of this.models) {
      const x0 = (g[1] - L.start) / bp, x1 = (g[2] - L.start) / bp + g[0].length * 6.5 + 8;
      let row = rows.findIndex((end) => end < x0);
      if (row < 0) { row = rows.length; rows.push(0); }
      rows[row] = x1;
      placed.push({ g, row });
    }
    return { placed, n: Math.max(1, Math.min(rows.length, 6)) };
  }

  /** Peak rows: every group of the peaks' annotation, minus categories hidden in the legend when that
   *  annotation is the one shown; colored like that annotation's legend. */
  private peakGroups() {
    const meta = this.atac.meta.peaks;
    if (!meta) return [];
    const spec = this.v.slide.spec(meta.annotation);
    const colors = spec ? this.v.catColors.get(spec.key)! : [];
    const vis = spec && this.v.groupBy === spec.key ? this.v.catVisible.get(spec.key)! : null;
    return meta.groups.map((name, g) => {
      const c = spec?.categories!.indexOf(name) ?? -1;
      return { g, name, color: c >= 0 ? colors[c] : css('--text-3'), visible: !vis || c < 0 || vis[c] };
    }).filter((r) => r.visible);
  }

  private plotW() { return Math.max(100, this.canvas.parentElement!.clientWidth - LABEL_W - PAD_R); }

  draw() {
    const box = this.canvas.parentElement!;
    const W = box.clientWidth, dpr = window.devicePixelRatio || 1, ctx = this.ctx;
    if (!this.locus || !W) { this.canvas.height = 0; return; }
    const genes = this.geneRows();
    const peakGroups = this.peakGroups();
    const peaksH = peakGroups.length ? PEAK_HEAD + peakGroups.length * PEAK_ROW + 4 : 0;
    const H = RULER_H + Math.max(1, this.tracks.length) * TRACK_H + peaksH + genes.n * GENE_ROW + 10;
    this.canvas.width = W * dpr; this.canvas.height = H * dpr;
    this.canvas.style.width = `${W}px`; this.canvas.style.height = `${H}px`;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);
    const L = this.locus, pw = this.plotW(), span = L.end - L.start, x = (bp: number) => LABEL_W + ((bp - L.start) / span) * pw;
    ctx.font = '11px "Manrope Variable", system-ui, sans-serif';

    // ruler
    ctx.fillStyle = css('--text-3'); ctx.strokeStyle = css('--border');
    const step = 10 ** Math.floor(Math.log10(span / 4)), tick = [5, 2, 1].map((k) => k * step).find((s) => span / s >= 4) ?? step;
    for (let t = Math.ceil(L.start / tick) * tick; t < L.end; t += tick) {
      const px = x(t);
      ctx.beginPath(); ctx.moveTo(px, RULER_H - 6); ctx.lineTo(px, H - genes.n * GENE_ROW - 8); ctx.stroke();
      ctx.fillText(t >= 1e6 ? `${(t / 1e6).toFixed(tick < 1e6 ? 3 : 0)} Mb` : `${(t / 1e3).toFixed(0)} kb`, px + 3, 12);
    }
    ctx.fillStyle = css('--text');
    ctx.fillText(L.chrom, 8, 12);

    // marked region
    if (this.mark) {
      ctx.fillStyle = css('--amber-soft');
      ctx.fillRect(x(this.mark[0]), RULER_H, Math.max(1, x(this.mark[1]) - x(this.mark[0])), H - RULER_H);
    }

    // coverage tracks
    const sharedMax = Math.max(...this.tracks.map((t) => t.max), 1e-9);
    const binSize = this.atac.meta.binSize, bin0 = this.entries?.bin0 ?? 0;
    this.tracks.forEach((t, i) => {
      const top = RULER_H + i * TRACK_H, base = top + TRACK_H - 6, h = TRACK_H - 10;
      const max = this.shared ? sharedMax : Math.max(t.max, 1e-9);
      ctx.fillStyle = t.color;
      ctx.fillRect(8, top + 8, 10, 10);
      ctx.fillStyle = css('--text');
      ctx.fillText(clip(ctx, t.name, LABEL_W - 34), 24, top + 17);
      ctx.fillStyle = css('--text-3');
      ctx.fillText(`${t.cells.toLocaleString()} cells`, 24, top + 30);
      // one bar per screen pixel: the highest bin under it
      const cols = new Float32Array(Math.ceil(pw));
      for (let b = 0; b < t.cov.length; b++) {
        const v = t.cov[b];
        if (!v) continue;
        const p0 = Math.floor(x((bin0 + b) * binSize) - LABEL_W), p1 = Math.max(p0 + 1, Math.floor(x((bin0 + b + 1) * binSize) - LABEL_W));
        for (let p = Math.max(0, p0); p < Math.min(cols.length, p1); p++) cols[p] = Math.max(cols[p], v);
      }
      ctx.fillStyle = t.color;
      for (let p = 0; p < cols.length; p++) if (cols[p]) {
        const bh = Math.max(1, Math.min(1, cols[p] / max) * h);
        ctx.fillRect(LABEL_W + p, base - bh, 1, bh);
      }
      ctx.fillStyle = css('--border'); ctx.fillRect(LABEL_W, base, pw, 1);
      ctx.fillStyle = css('--text-3');
      ctx.fillText(fmtNum(max), LABEL_W + pw + 4, top + 12);
    });
    if (!this.tracks.length) {
      ctx.fillStyle = css('--text-3');
      ctx.fillText('No visible cells — show some categories in the legend', LABEL_W, RULER_H + 22);
    }

    // MACS3 peaks, one row per group; opacity by -log10 q
    const pTop = RULER_H + Math.max(1, this.tracks.length) * TRACK_H;
    this.peakRows = { top: pTop + PEAK_HEAD, groups: peakGroups.map((r) => r.g) };
    if (peakGroups.length) {
      ctx.fillStyle = css('--text-3');
      // MACS3 peaks carry q values; an object's own peak set (minNlog10q 0) does not
      ctx.fillText(this.atac.meta.peaks!.minNlog10q === 0 ? 'Peaks (peak set of the dataset)' : `Peaks (MACS3, q < 1e-${this.peakMinQ})`, 8, pTop + 12);
      const row = new Map(peakGroups.map((r, i) => [r.g, i]));
      peakGroups.forEach((r, i) => {
        ctx.fillStyle = r.color;
        ctx.fillRect(8, pTop + PEAK_HEAD + i * PEAK_ROW + 2, 8, 7);
        ctx.fillStyle = css('--text-2');
        ctx.fillText(clip(ctx, r.name, LABEL_W - 30), 22, pTop + PEAK_HEAD + i * PEAK_ROW + 9);
      });
      const minQ = this.atac.meta.peaks!.minNlog10q === 0 ? 0 : this.peakMinQ;
      for (const [s, e, g, , q] of this.peaks) {
        const i = row.get(g);
        if (i === undefined || q < minQ) continue;
        const a = Math.max(LABEL_W, x(s)), b = Math.min(LABEL_W + pw, x(e));
        if (b <= a - 1) continue;
        ctx.globalAlpha = Math.min(1, 0.35 + q / 20);
        ctx.fillStyle = peakGroups[i].color;
        ctx.fillRect(a, pTop + PEAK_HEAD + i * PEAK_ROW + 1, Math.max(1.5, b - a), PEAK_ROW - 3);
      }
      ctx.globalAlpha = 1;
    }

    // genes
    const gTop = pTop + peaksH + 4;
    for (const { g, row } of genes.placed) {
      if (row >= genes.n) continue;
      const y = gTop + row * GENE_ROW + 7, x0 = Math.max(LABEL_W, x(g[1])), x1 = Math.min(LABEL_W + pw, x(g[2]));
      if (x1 < LABEL_W || x0 > LABEL_W + pw) continue;
      ctx.fillStyle = g[4] === 'p' ? css('--accent') : css('--border-strong');
      ctx.fillRect(x0, y, Math.max(1, x1 - x0), 1);
      for (const [s, e] of g[5]) {
        const a = Math.max(LABEL_W, x(s)), b = Math.min(LABEL_W + pw, x(e));
        if (b > a) ctx.fillRect(a, y - 4, Math.max(1, b - a), 9);
      }
      // strand arrows along the gene body
      for (let px = x0 + 6; px < x1 - 4; px += 14) {
        ctx.beginPath();
        const d = g[3] === '+' ? 2.5 : -2.5;
        ctx.moveTo(px - d, y - 3); ctx.lineTo(px + d, y); ctx.lineTo(px - d, y + 3);
        ctx.strokeStyle = ctx.fillStyle as string; ctx.stroke();
      }
      ctx.fillStyle = css('--text');
      ctx.fillText(g[0], Math.min(x0, LABEL_W + pw - 40) + 2, y + 17);
    }
    ctx.fillStyle = css('--text-3');
    ctx.fillText('Genes', 8, gTop + 10);
  }

  // ------------------------------------------------------------- interaction

  private bpAt(e: PointerEvent) {
    const r = this.canvas.getBoundingClientRect(), L = this.locus!;
    const px = Math.min(Math.max(e.clientX - r.left - LABEL_W, 0), this.plotW());
    return L.start + (px / this.plotW()) * (L.end - L.start);
  }

  private setMark(m: [number, number] | null) { this.mark = m; this.onMark(m); }

  private pointerDown(e: PointerEvent) {
    if (!this.locus) return;
    try { this.canvas.setPointerCapture(e.pointerId); } catch { /* not a live pointer (e.g. synthetic) */ }
    this.drag = this.bpAt(e);
  }

  private pointerMove(e: PointerEvent) {
    if (!this.locus) return;
    const bp = this.bpAt(e);
    if (this.drag != null) {
      this.mark = [Math.min(this.drag, bp), Math.max(this.drag, bp)];
      this.draw();
    }
    this.showTip(e, bp);
  }

  private pointerUp(e: PointerEvent) {
    if (this.drag == null) return;
    const bp = this.bpAt(e), span = this.locus!.end - this.locus!.start;
    const m: [number, number] = [Math.min(this.drag, bp), Math.max(this.drag, bp)];
    this.drag = null;
    const peak = m[1] - m[0] <= span / 500 ? this.peakAt(e) : null;
    if (peak) {
      this.setMark([peak[0], peak[1]]);
      this.onInfo(`Peak ${fmtLocus({ chrom: this.locus!.chrom, start: peak[0], end: peak[1] })} · ${this.atac.meta.peaks!.groups[peak[2]]} · −log10 q ${peak[4]}`);
    } else this.setMark(m[1] - m[0] > span / 500 ? m : null); // a click elsewhere clears the mark
    this.draw();
  }

  /** The peak under the pointer, if it is over a peak row. */
  private peakAt(e: PointerEvent): Peak | null {
    const r = this.canvas.getBoundingClientRect(), y = e.clientY - r.top;
    const i = Math.floor((y - this.peakRows.top) / PEAK_ROW), g = this.peakRows.groups[i];
    if (g === undefined || y < this.peakRows.top || !this.locus) return null;
    const bp = this.bpAt(e), slack = ((this.locus.end - this.locus.start) / this.plotW()) * 2;
    return this.peaks.find((k) => k[2] === g && k[4] >= this.peakMinQ && k[0] - slack <= bp && bp <= k[1] + slack) ?? null;
  }

  private showTip(e: PointerEvent, bp: number) {
    const r = this.canvas.getBoundingClientRect(), y = e.clientY - r.top;
    const peak = this.peakAt(e);
    if (peak) {
      this.tip.textContent = `${this.atac.meta.peaks!.groups[peak[2]]} peak · ${(peak[1] - peak[0]).toLocaleString()} bp · −log10 q ${peak[4]} · click to mark`;
      this.tip.style.left = `${e.clientX - r.left + 12}px`;
      this.tip.style.top = `${y + 10}px`;
      this.tip.hidden = false;
      return;
    }
    const i = Math.floor((y - RULER_H) / TRACK_H), t = this.tracks[i];
    if (!t || !this.entries || e.clientX - r.left < LABEL_W) { this.tip.hidden = true; return; }
    const b = Math.floor(bp / this.atac.meta.binSize) - this.entries.bin0;
    this.tip.textContent = `${t.name} · ${this.locus!.chrom}:${Math.round(bp).toLocaleString('en-US')} · ${fmtNum(t.cov[b] ?? 0)}`;
    this.tip.style.left = `${e.clientX - r.left + 12}px`;
    this.tip.style.top = `${y + 10}px`;
    this.tip.hidden = false;
  }
}

function fmtNum(x: number) { return x >= 100 ? x.toFixed(0) : x >= 1 ? x.toFixed(1) : x.toPrecision(2); }

function clip(ctx: CanvasRenderingContext2D, s: string, w: number) {
  if (ctx.measureText(s).width <= w) return s;
  while (s.length > 1 && ctx.measureText(`${s}…`).width > w) s = s.slice(0, -1);
  return `${s}…`;
}
