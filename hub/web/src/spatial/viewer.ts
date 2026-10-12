// deck.gl renderer for one spatial slide: morphology image tiles, cells as points or
// segmentation polygons, colored by an annotation, a numeric column or a gene, plus
// rectangle/lasso selection. The page (src/pages/data/spatial/view.astro) owns the controls.
import { Deck, OrthographicView, type OrthographicViewState, type PickingInfo } from '@deck.gl/core';
import { BitmapLayer, PathLayer, ScatterplotLayer, SolidPolygonLayer } from '@deck.gl/layers';
import { TileLayer } from '@deck.gl/geo-layers';
import { DataFilterExtension } from '@deck.gl/extensions';
import { categoryColors, colormapLut, hexToRgb } from './colors';
import type { Slide } from './data';

export type ColorBy = { kind: 'group' } | { kind: 'obs'; key: string } | { kind: 'gene'; gene: string }
  /** values computed by the page, e.g. the accessibility of a genomic region (sparse like a gene) */
  | { kind: 'values'; label: string; values: Float32Array };
export type Tool = 'pan' | 'rect' | 'lasso';
export interface Channel { visible: boolean; color: string; lo: number; hi: number; gamma: number }
export interface Region { name: string; cells: number[]; preset?: boolean }

const POLYGON_ZOOM = -2.2; // "auto" shape: segmentation polygons above this zoom
const DIM_ALPHA = 38;      // cells outside the current selection
const filterExt = [new DataFilterExtension({ filterSize: 1 })];

interface TileContent { channels: number[]; pixels: (Uint8Array | null)[] }

export class Viewer {
  readonly slide: Slide;
  readonly deck: Deck<OrthographicView>;
  view: OrthographicViewState;

  // settings (the page sets these, then calls update())
  groupBy: string;
  catColors = new Map<string, string[]>();
  catVisible = new Map<string, boolean[]>();
  colorBy: ColorBy = { kind: 'group' };
  colormap = 'viridis';
  range: [number, number] = [0, 1];
  belowMin: 'gray' | 'hide' | 'color' = 'gray';
  /** color of cells at or below the minimum ("show in gray"); the page picks one that reads on the background */
  belowGray: [number, number, number] = [72, 72, 72];
  norm: 'log' | 'raw' = 'log';
  pointScale = 1;
  opacity = 0.9;
  shape: 'auto' | 'points' | 'polygons' = 'auto';
  layout: 'spatial' | string = 'spatial';
  showImage = true;
  channels: Channel[];
  tool: Tool = 'pan';

  // derived state
  values: Float32Array | null = null;
  valueExtent: [number, number] = [0, 1];
  selection: Uint8Array | null = null;
  cropped = false;
  private colors!: Uint8Array;
  private filter!: Float32Array;
  private order: Uint32Array | null = null;
  private orderFor: Float32Array | null = null;
  private pointData: Record<string, unknown> | null = null;
  private polyData: Record<string, unknown> | null = null;
  private polyVersion = -1;
  private colorVersion = 0;
  private imageVersion = 0;
  private tileImages = new WeakMap<TileContent, { version: number; canvas: HTMLCanvasElement | null }>();
  private boundaries: { starts: Uint32Array; xy: Float32Array } | null = null;
  private layoutPos!: Float32Array;
  private layoutBounds: [number, number, number, number] = [0, 0, 1, 1];
  private ids: string[] | null = null;
  private overlay: SVGSVGElement;
  private scalebar: HTMLElement;
  private draft: [number, number][] | null = null;

  /** Called after anything the side panel shows (selection, counts) may have changed. */
  onChange: () => void = () => {};

  constructor(private container: HTMLElement, slide: Slide) {
    this.slide = slide;
    const m = slide.meta;
    this.groupBy = m.defaultObs ?? m.obs.find((o) => o.type === 'category')?.key ?? '';
    for (const o of m.obs) if (o.type === 'category') {
      this.catColors.set(o.key, o.colors?.length === o.categories!.length ? [...o.colors] : categoryColors(o.categories!));
      this.catVisible.set(o.key, o.categories!.map(() => true));
    }
    this.channels = (m.image?.channels ?? []).map((c) => ({ visible: c.visible, color: c.color, lo: 0, hi: 255, gamma: 1 }));
    this.setLayout(m.spatial === false ? m.embeddings[0]?.key ?? 'spatial' : 'spatial', false);
    this.view = this.fitView();

    this.overlay = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    this.overlay.classList.add('sv-overlay');
    this.scalebar = Object.assign(document.createElement('div'), { className: 'sv-scalebar' });
    container.append(this.overlay, this.scalebar);
    this.bindDrawing();

    this.deck = new Deck({
      parent: container as HTMLDivElement,
      views: new OrthographicView({ id: 'ortho', flipY: true }),
      viewState: this.view,
      controller: { doubleClickZoom: true, inertia: false },
      onViewStateChange: ({ viewState }) => {
        const before = this.usePolygons();
        this.view = viewState as OrthographicViewState;
        this.deck.setProps({ viewState: this.view });
        this.updateScalebar();
        if (before !== this.usePolygons()) this.render();
      },
      getTooltip: (info) => this.tooltip(info),
      getCursor: ({ isDragging }) => (this.tool !== 'pan' ? 'crosshair' : isDragging ? 'grabbing' : 'grab'),
      layers: [],
    });
    slide.cellIds().then((ids) => (this.ids = ids)).catch(() => {});
    this.update();
  }

  destroy() {
    this.deck.finalize();
    this.overlay.remove();
    this.scalebar.remove();
  }

  // ------------------------------------------------------------- layout and view

  setLayout(layout: string, refit = true) {
    const emb = layout === 'spatial' ? null : this.slide.embedding(layout);
    this.layout = emb ? layout : 'spatial';
    if (!emb) {
      this.layoutPos = this.slide.positions;
      this.layoutBounds = [0, 0, this.slide.meta.width, this.slide.meta.height];
    } else {
      // scale the embedding to a canvas comparable to the tissue so zoom and point sizes feel alike
      let [x0, y0, x1, y1] = [Infinity, Infinity, -Infinity, -Infinity];
      for (let i = 0; i < emb.length; i += 2) {
        if (!Number.isFinite(emb[i])) continue;
        x0 = Math.min(x0, emb[i]); x1 = Math.max(x1, emb[i]);
        y0 = Math.min(y0, emb[i + 1]); y1 = Math.max(y1, emb[i + 1]);
      }
      const s = 10000 / Math.max(x1 - x0, y1 - y0);
      const pos = new Float32Array(emb.length);
      for (let i = 0; i < emb.length; i += 2) {
        pos[i] = (emb[i] - x0) * s;
        pos[i + 1] = (y1 - emb[i + 1]) * s; // y up, like a plot
      }
      this.layoutPos = pos;
      this.layoutBounds = [0, 0, (x1 - x0) * s, (y1 - y0) * s];
    }
    this.pointData = null;
    if (refit) this.fit();
  }

  private fitView(bounds = this.layoutBounds): OrthographicViewState {
    const w = this.container.clientWidth || 800, h = this.container.clientHeight || 600;
    const [x0, y0, x1, y1] = bounds;
    const zoom = Math.log2(Math.min(w / Math.max(x1 - x0, 1), h / Math.max(y1 - y0, 1)) * 0.92);
    return { target: [(x0 + x1) / 2, (y0 + y1) / 2, 0], zoom, minZoom: zoom - 2, maxZoom: 6 };
  }

  fit(bounds?: [number, number, number, number]) {
    this.view = { ...this.fitView(bounds), minZoom: this.fitView().minZoom } as OrthographicViewState;
    this.deck?.setProps({ viewState: this.view });
    this.updateScalebar();
    this.render();
  }

  fitSelection() {
    if (!this.selection) return this.fit();
    const p = this.layoutPos;
    let [x0, y0, x1, y1] = [Infinity, Infinity, -Infinity, -Infinity];
    for (let i = 0; i < this.selection.length; i++) if (this.selection[i]) {
      x0 = Math.min(x0, p[i * 2]); x1 = Math.max(x1, p[i * 2]);
      y0 = Math.min(y0, p[i * 2 + 1]); y1 = Math.max(y1, p[i * 2 + 1]);
    }
    if (x0 < x1) this.fit([x0 - 100, y0 - 100, x1 + 100, y1 + 100]);
  }

  private usePolygons() {
    if (this.layout !== 'spatial' || !this.slide.meta.boundaries) return false;
    return this.shape === 'polygons' || (this.shape === 'auto' && (this.view?.zoom as number) > POLYGON_ZOOM);
  }

  private updateScalebar() {
    // no scale bar where the physical size of a coordinate unit is unknown (e.g. non-Visium spot layouts)
    if (this.layout !== 'spatial' || this.slide.meta.scaleUnknown) { this.scalebar.hidden = true; return; }
    this.scalebar.hidden = false;
    const umPerPx = this.slide.meta.pixelSize / 2 ** (this.view.zoom as number);
    const target = umPerPx * 120;
    const pow = 10 ** Math.floor(Math.log10(target));
    const nice = [5, 2, 1].map((k) => k * pow).find((v) => v <= target) ?? pow;
    this.scalebar.style.width = `${nice / umPerPx}px`;
    this.scalebar.textContent = nice >= 1000 ? `${nice / 1000} mm` : `${nice} µm`;
  }

  // ------------------------------------------------------------- colors

  /** Load the values for the current colorBy (gene or numeric column). */
  async setColorBy(c: ColorBy, keepRange = false) {
    this.colorBy = c;
    if (c.kind === 'group') {
      this.values = null;
    } else {
      let values: Float32Array;
      if (c.kind === 'gene') values = (await this.slide.expression(c.gene, this.norm)).values;
      else if (c.kind === 'values') values = c.values;
      else values = Float32Array.from(this.slide.obs(c.key));
      if (this.colorBy !== c) return; // a newer choice arrived meanwhile
      this.values = values;
      let lo = Infinity, hi = -Infinity;
      for (const v of values) if (Number.isFinite(v)) { lo = Math.min(lo, v); hi = Math.max(hi, v); }
      this.valueExtent = lo <= hi ? [lo, hi] : [0, 1];
      if (!keepRange) {
        // start at the 99th percentile of expressing cells so a few outliers do not wash out the map
        const sparse = c.kind === 'gene' || c.kind === 'values';
        const pos = values.filter((v) => (sparse ? v > 0 : Number.isFinite(v))).sort();
        const p99 = pos.length ? pos[Math.min(pos.length - 1, Math.floor(pos.length * 0.99))] : 1;
        this.range = sparse ? [0, p99 || this.valueExtent[1] || 1] : [this.valueExtent[0], p99];
        this.belowMin = sparse ? 'gray' : 'color';
      }
    }
    this.update();
  }

  /** Recompute per-cell colors and visibility, then redraw. */
  update() {
    const n = this.slide.n;
    const colors = new Uint8Array(n * 4);
    const filter = new Float32Array(n);
    const codes = this.groupBy ? this.slide.obs(this.groupBy) : null;
    const visible = this.catVisible.get(this.groupBy) ?? [];
    const rgb = (this.catColors.get(this.groupBy) ?? []).map(hexToRgb);
    const lut = colormapLut(this.colormap);
    const [lo, hi] = this.range;
    const span = hi - lo || 1;
    const vals = this.colorBy.kind === 'group' ? null : this.values;
    const sel = this.selection;
    for (let i = 0; i < n; i++) {
      let show = codes ? visible[codes[i]] !== false : true;
      if (this.cropped && sel && !sel[i]) show = false;
      let r: number, g: number, b: number;
      if (!vals) {
        [r, g, b] = rgb[codes ? codes[i] : 0] ?? [200, 200, 200];
      } else {
        const v = vals[i];
        if (!Number.isFinite(v) || (v <= lo && this.belowMin !== 'color')) {
          if (this.belowMin === 'hide') show = false;
          [r, g, b] = this.belowGray;
        } else {
          const k = Math.max(0, Math.min(255, Math.round(((v - lo) / span) * 255))) * 3;
          r = lut[k]; g = lut[k + 1]; b = lut[k + 2];
        }
      }
      colors[i * 4] = r; colors[i * 4 + 1] = g; colors[i * 4 + 2] = b;
      colors[i * 4 + 3] = sel && !this.cropped && !sel[i] ? DIM_ALPHA : 255;
      filter[i] = show ? 1 : 0;
    }
    this.colors = colors;
    this.filter = filter;
    // draw high values last so expressing cells are not hidden under the rest
    if (!vals) {
      this.order = null;
    } else if (this.orderFor !== vals) {
      const idx = new Uint32Array(n).map((_, i) => i);
      this.order = idx.sort((a, b) => (vals[a] || 0) - (vals[b] || 0));
    }
    this.orderFor = vals;
    this.pointData = null;
    this.colorVersion++;
    this.render();
    this.onChange();
  }

  /** Name of what the cells are colored by (gene, region or numeric annotation). */
  valueLabel() {
    const c = this.colorBy;
    return c.kind === 'gene' ? c.gene : c.kind === 'values' ? c.label : c.kind === 'obs' ? this.slide.spec(c.key)?.label ?? c.key : '';
  }

  /** Cells currently drawn (after annotation filter, threshold and crop). */
  visibleCount() { let k = 0; for (const f of this.filter) k += f; return k; }

  categoryCounts(key = this.groupBy, onlySelected = false) {
    const spec = this.slide.spec(key)!;
    const codes = this.slide.obs(key);
    const counts = new Array(spec.categories!.length).fill(0);
    for (let i = 0; i < codes.length; i++) if (!onlySelected || this.selection?.[i]) counts[codes[i]]++;
    return counts;
  }

  // ------------------------------------------------------------- selection

  setTool(tool: Tool) {
    this.tool = tool;
    this.overlay.classList.toggle('active', tool !== 'pan');
    this.deck.setProps({ controller: tool === 'pan' ? { doubleClickZoom: true, inertia: false } : false });
  }

  private bindDrawing() {
    const ov = this.overlay;
    const pt = (e: PointerEvent): [number, number] => {
      const r = ov.getBoundingClientRect();
      return [e.clientX - r.left, e.clientY - r.top];
    };
    ov.addEventListener('pointerdown', (e) => {
      if (this.tool === 'pan') return;
      ov.setPointerCapture(e.pointerId);
      this.draft = [pt(e)];
    });
    ov.addEventListener('pointermove', (e) => {
      if (!this.draft) return;
      const p = pt(e);
      if (this.tool === 'rect') this.draft = [this.draft[0], p];
      else this.draft.push(p);
      this.drawDraft();
    });
    const finish = () => {
      const d = this.draft;
      this.draft = null;
      ov.innerHTML = '';
      if (!d || d.length < 2) return;
      let poly = d;
      if (this.tool === 'rect') {
        const [[ax, ay], [bx, by]] = d;
        if (Math.abs(ax - bx) < 3 || Math.abs(ay - by) < 3) return;
        poly = [[ax, ay], [bx, ay], [bx, by], [ax, by]];
      } else if (d.length < 3) return;
      const vp = this.deck.getViewports()[0];
      this.selectPolygon(poly.map((p) => vp.unproject(p) as [number, number]));
    };
    ov.addEventListener('pointerup', finish);
    ov.addEventListener('pointercancel', () => { this.draft = null; ov.innerHTML = ''; });
  }

  private drawDraft() {
    const d = this.draft!;
    let pts = d;
    if (this.tool === 'rect' && d.length === 2) {
      const [[ax, ay], [bx, by]] = d;
      pts = [[ax, ay], [bx, ay], [bx, by], [ax, by]];
    }
    this.overlay.innerHTML = `<polygon points="${pts.map((p) => p.join(',')).join(' ')}" />`;
  }

  /** Select the drawn cells (world coordinates); holding the result until cleared. */
  selectPolygon(poly: [number, number][]) {
    const p = this.layoutPos, n = this.slide.n;
    let [x0, y0, x1, y1] = [Infinity, Infinity, -Infinity, -Infinity];
    for (const [x, y] of poly) { x0 = Math.min(x0, x); x1 = Math.max(x1, x); y0 = Math.min(y0, y); y1 = Math.max(y1, y); }
    const sel = new Uint8Array(n);
    for (let i = 0; i < n; i++) {
      const x = p[i * 2], y = p[i * 2 + 1];
      if (x < x0 || x > x1 || y < y0 || y > y1 || !this.filter[i]) continue;
      let inside = false;
      for (let a = 0, b = poly.length - 1; a < poly.length; b = a++) {
        const [xa, ya] = poly[a], [xb, yb] = poly[b];
        if ((ya > y) !== (yb > y) && x < ((xb - xa) * (y - ya)) / (yb - ya) + xa) inside = !inside;
      }
      if (inside) sel[i] = 1;
    }
    this.setSelection(sel);
  }

  setSelection(sel: Uint8Array | null) {
    this.selection = sel && sel.some((v) => v) ? sel : null;
    if (!this.selection) this.cropped = false;
    this.update();
  }

  selectRegion(r: Region) {
    const sel = new Uint8Array(this.slide.n);
    for (const i of r.cells) sel[i] = 1;
    this.setSelection(sel);
  }

  selectedIndices() {
    const out: number[] = [];
    this.selection?.forEach((v, i) => v && out.push(i));
    return out;
  }

  invertSelection() {
    if (!this.selection) return;
    this.setSelection(this.selection.map((v, i) => (this.filter[i] || this.cropped ? 1 - v : 0)));
  }

  setCrop(on: boolean) {
    this.cropped = on && !!this.selection;
    this.update();
    if (this.cropped) this.fitSelection();
  }

  // ------------------------------------------------------------- export

  async exportCsv() {
    const ids = await this.slide.cellIds();
    // tissue sections: positions in µm; single-cell data: the coordinates of the first embedding
    const m = this.slide.meta, tissue = m.spatial !== false;
    const px = tissue ? m.pixelSize : 1, pos = tissue ? this.slide.positions : this.slide.embedding(m.embeddings[0].key)!;
    const axis = tissue ? ['x_um', 'y_um'] : [1, 2].map((k) => `${m.embeddings[0].key.replace(/^X_/, '')}_${k}`);
    const spec = this.slide.spec(this.groupBy);
    const codes = spec ? this.slide.obs(this.groupBy) : null;
    const valueName = this.colorBy.kind === 'gene' ? `${this.colorBy.gene}_${this.norm === 'log' ? 'lognorm' : 'counts'}`
      : this.colorBy.kind === 'obs' ? this.colorBy.key : this.colorBy.kind === 'values' ? this.colorBy.label.replace(/[^\w.:-]+/g, '_') : null;
    const head = ['cell_id', ...axis, ...(spec ? [spec.key] : []), ...(valueName ? [valueName] : [])];
    const q = (s: string) => (/[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s);
    const rows = [head.map(q).join(',')];
    for (let i = 0; i < this.slide.n; i++) {
      if (this.selection ? !this.selection[i] : !this.filter[i]) continue;
      const row = [ids[i], (pos[i * 2] * px).toFixed(tissue ? 2 : 4), (pos[i * 2 + 1] * px).toFixed(tissue ? 2 : 4)];
      if (spec && codes) row.push(spec.categories![codes[i]]);
      if (valueName && this.values) row.push(String(+this.values[i].toFixed(4)));
      rows.push(row.map(q).join(','));
    }
    return new Blob([rows.join('\n') + '\n'], { type: 'text/csv' });
  }

  /** PNG of the plot on a transparent background; the scale bar is drawn in `ink`. */
  screenshot(ink: string) {
    this.deck.redraw('screenshot');
    const src = this.deck.getCanvas()!;
    const out = document.createElement('canvas');
    out.width = src.width; out.height = src.height;
    const ctx = out.getContext('2d')!;
    ctx.drawImage(src, 0, 0);
    if (!this.scalebar.hidden) {
      const dpr = out.width / src.clientWidth;
      const w = this.scalebar.offsetWidth * dpr, x = 20 * dpr, y = out.height - 24 * dpr;
      ctx.fillStyle = ink;
      ctx.fillRect(x, y, w, 3 * dpr);
      ctx.font = `${13 * dpr}px sans-serif`;
      ctx.fillText(this.scalebar.textContent ?? '', x, y - 6 * dpr);
    }
    return new Promise<Blob>((res) => out.toBlob((b) => res(b!), 'image/png'));
  }

  // ------------------------------------------------------------- image

  /** Call after changing channel colors/windows; `refetch` when the visible set changed. */
  updateImage(refetch = false) {
    this.imageVersion++;
    if (refetch) this.tileKey++;
    this.render();
  }
  private tileKey = 0;

  private composite(content: TileContent): HTMLCanvasElement | null {
    const memo = this.tileImages.get(content);
    if (memo && memo.version === this.imageVersion) return memo.canvas;
    const size = this.slide.meta.image!.tileSize;
    const active = content.channels.map((c, k) => [c, content.pixels[k]] as const)
      .filter(([c, px]) => px && this.channels[c].visible);
    let canvas: HTMLCanvasElement | null = null;
    if (active.length) {
      const img = new ImageData(size, size);
      const d = img.data;
      for (const [c, px] of active) {
        const ch = this.channels[c];
        const [cr, cg, cb] = hexToRgb(ch.color);
        const lr = new Uint8Array(256), lg = new Uint8Array(256), lb = new Uint8Array(256);
        const span = Math.max(1, ch.hi - ch.lo);
        for (let v = 0; v < 256; v++) {
          const t = Math.max(0, Math.min(1, (v - ch.lo) / span)) ** ch.gamma;
          lr[v] = cr * t; lg[v] = cg * t; lb[v] = cb * t;
        }
        const p = px!;
        for (let i = 0, j = 0; i < p.length; i++, j += 4) {
          const v = p[i];
          if (!v) continue;
          d[j] += lr[v]; d[j + 1] += lg[v]; d[j + 2] += lb[v]; // Uint8ClampedArray saturates at 255
        }
      }
      // alpha = brightest channel, color scaled up to match: identical to the additive image on black,
      // and empty tissue stays transparent on any other background (and in PNG exports)
      for (let j = 0; j < d.length; j += 4) {
        const m = Math.max(d[j], d[j + 1], d[j + 2]);
        if (!m) continue;
        const k = 255 / m;
        d[j] *= k; d[j + 1] *= k; d[j + 2] *= k; d[j + 3] = m;
      }
      canvas = document.createElement('canvas');
      canvas.width = canvas.height = size;
      canvas.getContext('2d')!.putImageData(img, 0, 0);
    }
    this.tileImages.set(content, { version: this.imageVersion, canvas });
    return canvas;
  }

  // ------------------------------------------------------------- layers

  private pointsData() {
    if (this.pointData) return this.pointData;
    const n = this.slide.n, o = this.order;
    const src = { pos: this.layoutPos, col: this.colors, fil: this.filter, rad: this.slide.radius };
    let pos = src.pos, col = src.col, fil = src.fil, rad = src.rad;
    if (o) {
      pos = new Float32Array(n * 2); col = new Uint8Array(n * 4); fil = new Float32Array(n); rad = new Float32Array(n);
      for (let k = 0; k < n; k++) {
        const i = o[k];
        pos[k * 2] = src.pos[i * 2]; pos[k * 2 + 1] = src.pos[i * 2 + 1];
        col.set(src.col.subarray(i * 4, i * 4 + 4), k * 4);
        fil[k] = src.fil[i]; rad[k] = src.rad[i];
      }
    }
    this.pointData = {
      length: n,
      attributes: {
        getPosition: { value: pos, size: 2 },
        getFillColor: { value: col, size: 4, normalized: true },
        // embeddings use one on-screen size for every cell; the tissue uses each cell's own radius
        ...(this.layout === 'spatial' ? { getRadius: { value: rad, size: 1 } } : {}),
        getFilterValue: { value: fil, size: 1 },
      },
    };
    return this.pointData;
  }

  private polygonsData() {
    const b = this.boundaries;
    if (!b) return null;
    if (this.polyData && this.polyVersion === this.colorVersion) return this.polyData;
    const nv = b.xy.length / 2, n = this.slide.n;
    const col = new Uint8Array(nv * 4), fil = new Float32Array(nv);
    for (let i = 0; i < n; i++) {
      const c = this.colors.subarray(i * 4, i * 4 + 4), f = this.filter[i];
      for (let v = b.starts[i]; v < b.starts[i + 1]; v++) { col.set(c, v * 4); fil[v] = f; }
    }
    this.polyData = {
      length: n,
      startIndices: b.starts.subarray(0, n),
      attributes: {
        getPolygon: { value: b.xy, size: 2 },
        getFillColor: { value: col, size: 4, normalized: true },
        getFilterValue: { value: fil, size: 1 },
      },
    };
    this.polyVersion = this.colorVersion;
    return this.polyData;
  }

  render() {
    if (!this.deck) return;
    const m = this.slide.meta;
    const layers: unknown[] = [];
    const spatial = this.layout === 'spatial';

    if (spatial && this.showImage && m.image && this.channels.some((c) => c.visible)) {
      const img = m.image;
      const visible = this.channels.map((c, i) => (c.visible ? i : -1)).filter((i) => i >= 0);
      const levels = img.levels.map((l) => l.level);
      layers.push(new TileLayer<TileContent>({
        id: 'image',
        tileSize: img.tileSize,
        minZoom: -Math.max(...levels),
        maxZoom: -Math.min(...levels),
        extent: [0, 0, m.width, m.height],
        maxRequests: 12,
        refinementStrategy: 'best-available',
        getTileData: async ({ index, signal }) => ({
          channels: visible,
          pixels: await Promise.all(visible.map((c) => this.slide.tile(c, -index.z, index.x, index.y, signal))),
        }),
        updateTriggers: { getTileData: this.tileKey, renderSubLayers: this.imageVersion },
        renderSubLayers: (props) => {
          const content = props.data as TileContent | null;
          const image = content && this.composite(content);
          if (!image) return null;
          const { left, top, right, bottom } = props.tile.bbox as { left: number; top: number; right: number; bottom: number };
          return new BitmapLayer({ id: props.id, image, bounds: [left, bottom, right, top] });
        },
      }));
    }

    const polygons = this.usePolygons();
    if (polygons && !this.boundaries) {
      this.slide.boundaries().then((b) => { this.boundaries = b; this.render(); });
    }
    const polyData = polygons ? this.polygonsData() : null;
    if (polyData) {
      layers.push(new SolidPolygonLayer({
        id: 'cells-poly',
        data: polyData as never,
        _normalize: false,
        opacity: this.opacity * 0.8,
        pickable: true,
        extensions: filterExt,
        filterRange: [0.5, 1.5],
      } as never));
    } else {
      layers.push(new ScatterplotLayer({
        id: 'cells',
        data: this.pointsData() as never,
        radiusUnits: spatial ? 'common' : 'pixels',
        radiusScale: spatial ? this.pointScale : 2.2 * this.pointScale,
        ...(spatial ? {} : { getRadius: 1 }),
        radiusMinPixels: 0.7,
        opacity: this.opacity,
        pickable: true,
        stroked: false,
        extensions: filterExt,
        filterRange: [0.5, 1.5],
      } as never));
    }

    this.deck.setProps({ layers: layers as never });
    this.updateScalebar();
  }

  private tooltip(info: PickingInfo) {
    if (info.index < 0 || info.layer == null) return null;
    const i = info.layer.id === 'cells' && this.order ? this.order[info.index] : info.index;
    const spec = this.slide.spec(this.groupBy);
    const parts: string[] = [];
    if (this.ids) parts.push(`<b>${this.ids[i]}</b>`);
    if (spec) parts.push(`${spec.label}: ${spec.categories![this.slide.obs(this.groupBy)[i]]}`);
    if (this.values && this.colorBy.kind !== 'group') {
      const name = this.valueLabel();
      parts.push(`${name}: ${+this.values[i].toFixed(3)}`);
    }
    return { html: parts.join('<br>'), className: 'sv-tooltip' };
  }
}
