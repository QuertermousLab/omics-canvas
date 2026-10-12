// Canvas scatter plots for the CRISPR screen explorer (volcano and rank plots): axes, hover, click,
// highlighted and labeled points. Thousands of points, redrawn whole on every change.
// Chrome colors come from the hub theme (css()), so a redraw follows a light/dark switch.
import { css } from '../common';

export interface ScatterData {
  x: ArrayLike<number>; y: ArrayLike<number>;
  /** CSS color per point */
  color: (i: number) => string;
  /** tooltip text per point */
  label: (i: number) => string;
  /** points drawn last and larger (e.g. the selection), with their name next to them */
  highlight?: Set<number>;
  /** points that get their name written next to them */
  named?: Set<number>;
  name?: (i: number) => string;
  xLabel: string; yLabel: string;
  /** dashed reference lines */
  xLines?: number[]; yLines?: number[];
  yLog?: boolean;
}

const M = { l: 52, r: 14, t: 12, b: 38 };

export class Scatter {
  data: ScatterData | null = null;
  onClick: (i: number | null) => void = () => {};
  private ctx: CanvasRenderingContext2D;
  private px = new Float32Array(0);
  private py = new Float32Array(0);

  constructor(private canvas: HTMLCanvasElement, private tip: HTMLElement) {
    this.ctx = canvas.getContext('2d')!;
    canvas.addEventListener('pointermove', (e) => this.hover(e));
    canvas.addEventListener('pointerleave', () => (this.tip.hidden = true));
    canvas.addEventListener('click', (e) => this.onClick(this.nearest(e)));
    new ResizeObserver(() => this.draw()).observe(canvas.parentElement!);
  }

  set(data: ScatterData) { this.data = data; this.draw(); }

  private extent(a: ArrayLike<number>, log = false) {
    let lo = Infinity, hi = -Infinity;
    for (let i = 0; i < a.length; i++) {
      const v = log ? Math.log10(1 + a[i]) : a[i];
      if (Number.isFinite(v)) { lo = Math.min(lo, v); hi = Math.max(hi, v); }
    }
    if (!(lo <= hi)) return [0, 1];
    const pad = (hi - lo || 1) * 0.04;
    return [lo - pad, hi + pad];
  }

  draw() {
    const d = this.data, box = this.canvas.parentElement!;
    const W = box.clientWidth, H = box.clientHeight || 300, dpr = window.devicePixelRatio || 1, ctx = this.ctx;
    this.canvas.width = W * dpr; this.canvas.height = H * dpr;
    this.canvas.style.width = `${W}px`; this.canvas.style.height = `${H}px`;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);
    if (!d || !W) return;
    const [x0, x1] = this.extent(d.x), [y0, y1] = this.extent(d.y, d.yLog);
    const ty = (v: number) => (d.yLog ? Math.log10(1 + v) : v);
    const sx = (v: number) => M.l + ((v - x0) / (x1 - x0)) * (W - M.l - M.r);
    const sy = (v: number) => H - M.b - ((ty(v) - y0) / (y1 - y0)) * (H - M.t - M.b);
    const n = d.x.length;
    this.px = new Float32Array(n); this.py = new Float32Array(n);
    for (let i = 0; i < n; i++) { this.px[i] = sx(d.x[i]); this.py[i] = sy(d.y[i]); }

    // axes and ticks
    ctx.font = '11px "Manrope Variable", system-ui, sans-serif';
    ctx.strokeStyle = css('--border'); ctx.fillStyle = css('--text-3'); ctx.lineWidth = 1;
    for (const t of ticks(x0, x1)) {
      const p = sx(t);
      ctx.beginPath(); ctx.moveTo(p, M.t); ctx.lineTo(p, H - M.b); ctx.stroke();
      ctx.fillText(fmt(t), p - 8, H - M.b + 14);
    }
    const yt = d.yLog ? logTicks(y1) : ticks(y0, y1);
    for (const t of yt) {
      const p = sy(t);
      if (p < M.t || p > H - M.b) continue;
      ctx.beginPath(); ctx.moveTo(M.l, p); ctx.lineTo(W - M.r, p); ctx.stroke();
      ctx.fillText(fmt(t), 6, p + 4);
    }
    ctx.fillStyle = css('--text-2');
    ctx.fillText(d.xLabel, (W - ctx.measureText(d.xLabel).width) / 2, H - 6);
    ctx.save(); ctx.translate(12, (H - M.b) / 2 + 30); ctx.rotate(-Math.PI / 2); ctx.fillText(d.yLabel, 0, 0); ctx.restore();
    ctx.setLineDash([4, 4]); ctx.strokeStyle = css('--border-strong');
    for (const v of d.xLines ?? []) { ctx.beginPath(); ctx.moveTo(sx(v), M.t); ctx.lineTo(sx(v), H - M.b); ctx.stroke(); }
    for (const v of d.yLines ?? []) { ctx.beginPath(); ctx.moveTo(M.l, sy(v)); ctx.lineTo(W - M.r, sy(v)); ctx.stroke(); }
    ctx.setLineDash([]);

    // points: plain first, highlighted on top
    for (let i = 0; i < n; i++) {
      if (d.highlight?.has(i)) continue;
      ctx.fillStyle = d.color(i);
      ctx.beginPath(); ctx.arc(this.px[i], this.py[i], 2.6, 0, Math.PI * 2); ctx.fill();
    }
    for (const i of d.highlight ?? []) {
      ctx.fillStyle = d.color(i); ctx.strokeStyle = css('--text'); ctx.lineWidth = 1.5;
      ctx.beginPath(); ctx.arc(this.px[i], this.py[i], 5, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
    }
    ctx.fillStyle = css('--text');
    for (const i of new Set([...(d.named ?? []), ...(d.highlight ?? [])])) {
      if (!d.name) break;
      const s = d.name(i), w = ctx.measureText(s).width;
      const x = this.px[i] + 6 + w > W - M.r ? this.px[i] - 6 - w : this.px[i] + 6;
      ctx.fillText(s, x, this.py[i] - 4);
    }
  }

  private nearest(e: MouseEvent) {
    const r = this.canvas.getBoundingClientRect(), x = e.clientX - r.left, y = e.clientY - r.top;
    let best = -1, bd = 64; // within 8 px
    for (let i = 0; i < this.px.length; i++) {
      const dd = (this.px[i] - x) ** 2 + (this.py[i] - y) ** 2;
      if (dd < bd) { bd = dd; best = i; }
    }
    return best >= 0 ? best : null;
  }

  private hover(e: PointerEvent) {
    const i = this.nearest(e);
    if (i == null || !this.data) { this.tip.hidden = true; this.canvas.style.cursor = ''; return; }
    const r = this.canvas.getBoundingClientRect();
    this.tip.textContent = this.data.label(i);
    this.tip.style.left = `${Math.min(e.clientX - r.left + 12, r.width - 220)}px`;
    this.tip.style.top = `${e.clientY - r.top + 12}px`;
    this.tip.hidden = false;
    this.canvas.style.cursor = 'pointer';
  }
}

function ticks(a: number, b: number, n = 6) {
  const step0 = (b - a) / n, mag = 10 ** Math.floor(Math.log10(step0 || 1));
  const step = [1, 2, 5, 10].map((k) => k * mag).find((s) => s >= step0) ?? mag;
  const out: number[] = [];
  for (let t = Math.ceil(a / step) * step; t <= b; t += step) out.push(+t.toFixed(10));
  return out;
}

function logTicks(maxLog: number) {
  const out = [0];
  for (let k = 0; 10 ** k - 1 <= 10 ** maxLog; k++) for (const m of [1, 3]) { const v = m * 10 ** k; if (Math.log10(1 + v) <= maxLog) out.push(v); }
  return out;
}

export const fmt = (v: number) => (Math.abs(v) >= 1000 ? `${(v / 1000).toFixed(v % 1000 ? 1 : 0)}k` : Math.abs(v) >= 10 || v === 0 ? v.toFixed(0) : Math.abs(v) >= 1 ? v.toFixed(1) : v.toFixed(2));
