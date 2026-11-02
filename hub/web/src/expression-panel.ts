// "Export & plots" in the spatial and single-cell viewers: the expression of chosen genes in chosen cells as a file
// (hub: /hub/api/datasets/<id>/matrix, expression.py), and one gene's or module score's distribution per group as a
// violin or ridge plot (/hub/api/datasets/<id>/distribution: densities computed on the server, drawn here as SVG).
import { esc } from './common';
import type { Viewer } from './spatial/viewer';

export interface ExpressionContext {
  viewer: () => Viewer;
  dataset: string;
  sample: () => string;
  headers: (json?: boolean) => Record<string, string>;
  ink: () => string;
  save: (blob: Blob, name: string) => void;
  savedModules: () => { id: string; name: string; method: string }[];
  /** the module score computed last (not saved), to plot it */
  lastModule: () => { genes: string; method: string } | null;
}

interface Group { code: number; name: string; cells: number; n: number; expressing: number; mean?: number; min?: number; q1?: number; median?: number; q3?: number; max?: number; density?: number[]; points?: number[] }
interface Distribution { label: string; sample: string; cells: number; kind: string; value?: string; method?: string; group: { key: string; label: string } | null; range: [number, number]; groups: Group[]; droppedGroups: number; nonzero: boolean; converted?: Record<string, string> }

const $ = <T extends HTMLElement = HTMLElement>(s: string) => document.querySelector<T>(`[data-expr-${s}]`)!;
const SPLIT = /[\s,;|]+/;
const QUOTES = /["'`‘’“”«»[\](){}<>]/g;
const parseGenes = (text: string) => [...new Map(text.replace(QUOTES, ' ').split(SPLIT).map((t) => t.replace(/^[.:]+|[.:]+$/g, '')).filter(Boolean).map((t) => [t.toLowerCase(), t])).values()];
const STORE = 'vizhub-expr-genes';

function maskB64(sel: ArrayLike<number>, n: number) {
  const bytes = new Uint8Array(Math.ceil(n / 8));
  for (let i = 0; i < n; i++) if (sel[i]) bytes[i >> 3] |= 1 << (i & 7);
  let s = '';
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(s);
}

/** Round axis ticks over [lo, hi]. */
function ticks(lo: number, hi: number, count = 5) {
  const span = hi - lo || 1, step0 = span / count, mag = 10 ** Math.floor(Math.log10(step0));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= step0) ?? mag * 10;
  const out: number[] = [];
  for (let t = Math.ceil(lo / step) * step; t <= hi + step * 1e-9; t += step) out.push(+t.toPrecision(12));
  return out;
}
const num = (x: number) => (Math.abs(x) >= 1000 ? x.toFixed(0) : Math.abs(x) >= 10 ? x.toFixed(1) : +x.toPrecision(3) + '');

/** Seeded jitter, so a plot looks the same when drawn again. */
function rng(seed: number) {
  let s = seed >>> 0 || 1;
  return () => ((s = (s * 1664525 + 1013904223) >>> 0) / 4294967296);
}

/** The plot as SVG: violins (one per group, scaled to the same width, as Seurat's VlnPlot) or ridges (ggridges). */
export function plotSvg(d: Distribution, type: 'violin' | 'ridge', colors: string[], ink: string, points: boolean) {
  const [lo, hi] = d.range, groups = d.groups.filter((g) => g.density);
  const grid = (i: number, m: number) => lo + ((hi - lo) * i) / (m - 1);
  const title = `<text x="16" y="22" font-size="15" font-weight="650" fill="${ink}">${esc(d.label)}</text>`;
  const sub = [d.kind === 'gene' ? (d.value === 'counts' ? 'counts' : 'log-normalized') : d.kind === 'module' ? `${d.method === 'seurat' ? 'Seurat' : 'AUC'} module score` : '',
    `${d.cells.toLocaleString()} cells`, d.group ? `by ${d.group.label}` : '', d.nonzero ? 'cells above 0' : '',
    d.droppedGroups ? `${d.droppedGroups} smaller groups left out` : ''].filter(Boolean).join(' · ');
  const subtitle = `<text x="16" y="40" font-size="11.5" fill="${ink}" fill-opacity=".65">${esc(sub)}</text>`;
  const font = `font-family="Inter, system-ui, -apple-system, Segoe UI, sans-serif"`;
  const color = (g: Group) => colors[g.code] ?? '#8fbcbb';
  const tip = (g: Group) => `<title>${esc(`${g.name} · ${g.cells.toLocaleString()} cells · ${g.cells ? Math.round((100 * g.expressing) / g.cells) : 0}% above 0 · median ${num(g.median ?? 0)} · mean ${num(g.mean ?? 0)}`)}</title>`;
  const jitter = rng(7);
  if (type === 'violin') {
    const longest = Math.max(...groups.map((g) => g.name.length), 4);
    const w = Math.max(26, Math.min(70, 900 / Math.max(1, groups.length))), left = 58, top = 56, h = 300;
    const bottom = Math.min(160, 14 + longest * 5.6), W = left + w * groups.length + 24, H = top + h + bottom;
    const y = (v: number) => top + h - ((v - lo) / (hi - lo)) * h;
    const axis = ticks(lo, hi).map((t) => `<line x1="${left - 4}" x2="${W - 16}" y1="${y(t)}" y2="${y(t)}" stroke="${ink}" stroke-opacity=".12"/>
      <text x="${left - 8}" y="${y(t) + 4}" font-size="11" text-anchor="end" fill="${ink}" fill-opacity=".75">${num(t)}</text>`).join('');
    const body = groups.map((g, k) => {
      const cx = left + w * (k + 0.5), dens = g.density!, m = dens.length, peak = Math.max(...dens) || 1, half = w * 0.42;
      const idx = dens.map((_, i) => i).filter((i) => dens[i] > 0);
      if (!idx.length) return '';
      const right = idx.map((i) => `${(cx + (dens[i] / peak) * half).toFixed(2)},${y(grid(i, m)).toFixed(2)}`);
      const leftSide = idx.slice().reverse().map((i) => `${(cx - (dens[i] / peak) * half).toFixed(2)},${y(grid(i, m)).toFixed(2)}`);
      const yMin = y(g.min!), yMax = y(g.max!);
      const shape = `<path d="M${cx},${yMin} L${right.join(' L')} L${cx},${yMax} L${leftSide.join(' L')} Z" fill="${color(g)}" fill-opacity=".85" stroke="${ink}" stroke-opacity=".55" stroke-width=".7"/>`;
      const dots = points && g.points ? g.points.map((p) => {
        const i = Math.round(((p - lo) / (hi - lo)) * (m - 1));
        const spread = ((dens[Math.max(0, Math.min(m - 1, i))] ?? 0) / peak) * half * 0.85;
        return `<circle cx="${(cx + (jitter() * 2 - 1) * spread).toFixed(1)}" cy="${y(p).toFixed(1)}" r="1.1"/>`;
      }).join('') : '';
      const box = `<rect x="${cx - 2.5}" y="${y(g.q3!)}" width="5" height="${Math.max(0.8, y(g.q1!) - y(g.q3!))}" fill="${ink}" fill-opacity=".7"/>
        <circle cx="${cx}" cy="${y(g.median!)}" r="2.6" fill="#fff" stroke="${ink}" stroke-width=".8"/>`;
      const lx = cx + 3, ly = top + h + 12;
      return `<g>${tip(g)}${shape}${dots ? `<g fill="${ink}" fill-opacity=".35">${dots}</g>` : ''}${box}
        <text x="${lx}" y="${ly}" font-size="11" text-anchor="end" fill="${ink}" transform="rotate(-45 ${lx} ${ly})">${esc(g.name)}</text></g>`;
    }).join('');
    return `<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" ${font}>${title}${subtitle}${axis}
      <line x1="${left}" x2="${left}" y1="${top}" y2="${top + h}" stroke="${ink}" stroke-opacity=".5"/>${body}</svg>`;
  }
  // ridges: one row per group (first on top), heights on one scale, overlapping the row above
  const longest = Math.max(...groups.map((g) => g.name.length), 4);
  const row = 30, scale = row * 2.2, left = Math.min(220, 20 + longest * 6.4), plotW = 560, W = left + plotW + 30;
  const top = 56 + scale - row + 4;  // the first ridge at full height stays under the title
  const H = top + row * groups.length + 46;
  const x = (v: number) => left + ((v - lo) / (hi - lo)) * plotW;
  const peak = Math.max(...groups.map((g) => Math.max(...g.density!))) || 1;
  const axis = ticks(lo, hi, 6).map((t) => `<line x1="${x(t)}" x2="${x(t)}" y1="${top + row - 4 - scale}" y2="${top + row * groups.length}" stroke="${ink}" stroke-opacity=".12"/>
    <text x="${x(t)}" y="${top + row * groups.length + 16}" font-size="11" text-anchor="middle" fill="${ink}" fill-opacity=".75">${num(t)}</text>`).join('');
  const body = groups.map((g, k) => {
    const base = top + row * (k + 1) - 4, dens = g.density!, m = dens.length;
    const idx = dens.map((_, i) => i).filter((i) => dens[i] > 0);
    if (!idx.length) return '';
    // a peak taller than the global one would only happen with rounding; keep the ridge within its band
    const pts = idx.map((i) => `${x(grid(i, m)).toFixed(2)},${(base - Math.min(1, dens[i] / peak) * scale).toFixed(2)}`);
    const shape = `<path d="M${x(g.min!)},${base} L${pts.join(' L')} L${x(g.max!)},${base} Z" fill="${color(g)}" fill-opacity=".82" stroke="${ink}" stroke-opacity=".6" stroke-width=".7"/>`;
    const dots = points && g.points ? g.points.map((p) => `<circle cx="${x(p).toFixed(1)}" cy="${(base + 1.5 + jitter() * 5).toFixed(1)}" r=".9"/>`).join('') : '';
    const med = `<line x1="${x(g.median!)}" x2="${x(g.median!)}" y1="${base}" y2="${base - 7}" stroke="${ink}" stroke-width="1.4"/>`;
    return `<g>${tip(g)}<line x1="${left}" x2="${left + plotW}" y1="${base}" y2="${base}" stroke="${ink}" stroke-opacity=".18"/>${shape}${med}
      ${dots ? `<g fill="${ink}" fill-opacity=".3">${dots}</g>` : ''}
      <text x="${left - 8}" y="${base - 3}" font-size="11.5" text-anchor="end" fill="${ink}">${esc(g.name)}</text></g>`;
  }).join('');
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" ${font}>${title}${subtitle}${axis}${body}</svg>`;
}

export function mountExpression(ctx: ExpressionContext) {
  const v = () => ctx.viewer();
  let busyPlot = false;
  let dlg: HTMLDialogElement | null = null;
  let last: { d: Distribution; type: 'violin' | 'ridge'; points: boolean; colors: string[] } | null = null;

  const categoryObs = () => v().slide.meta.obs.filter((o) => o.type === 'category' && !o.hidden);
  const numberObs = () => v().slide.meta.obs.filter((o) => o.type === 'number' && !o.hidden);

  function counts(key: string) {
    const spec = v().slide.spec(key)!, codes = v().slide.obs(key), out = new Array(spec.categories!.length).fill(0);
    for (let i = 0; i < codes.length; i++) out[codes[i]]++;
    return out;
  }

  function renderCats() {
    const key = $<HTMLSelectElement>('col').value, spec = v().slide.spec(key);
    if (!spec) { $('cats').innerHTML = ''; return; }
    const n = counts(key), colors = v().catColors.get(key) ?? [];
    $('cats').innerHTML = spec.categories!.map((c, i) => n[i] ? `<li><label class="check"><input type="checkbox" value="${esc(c)}" data-i="${i}">
      <span class="sw" style="background:${colors[i] ?? '#ccc'}"></span><span class="nm">${esc(c)}</span><span class="n">${n[i].toLocaleString()}</span></label></li>` : '').join('');
    renderCount();
  }

  /** The request's cell criteria and how many cells they keep. */
  function chosenCells(): { body: Record<string, unknown>; n: number; what: string } {
    const mode = $<HTMLSelectElement>('cells').value, view = v(), n = view.slide.n;
    if (mode === 'shown') {
      const m = view.shownMask();
      let k = 0; for (const x of m) k += x;
      return { body: k === n ? {} : { mask: maskB64(m, n) }, n: k, what: 'cells shown' };
    }
    if (mode === 'selection' && view.selection) {
      let k = 0; for (const x of view.selection) k += x;
      return { body: { mask: maskB64(view.selection, n) }, n: k, what: 'selected cells' };
    }
    if (mode === 'groups') {
      const key = $<HTMLSelectElement>('col').value;
      const boxes = [...document.querySelectorAll<HTMLInputElement>('[data-expr-cats] input:checked')];
      const c = counts(key);
      return { body: boxes.length ? { obs: key, values: boxes.map((b) => b.value) } : { obs: key }, n: boxes.length ? boxes.reduce((s, b) => s + c[+b.dataset.i!], 0) : n,
        what: boxes.length ? `cells in ${boxes.length} of ${v().slide.spec(key)?.label ?? key}` : 'all cells' };
    }
    return { body: {}, n, what: 'all cells' };
  }

  function renderCount() {
    const genes = parseGenes($<HTMLTextAreaElement>('genes').value);
    const c = chosenCells(), nGenes = genes.length || v().slide.features.length;
    $('count').textContent = `${c.n.toLocaleString()} ${c.what} × ${nGenes.toLocaleString()} ${genes.length ? (nGenes > 1 ? 'genes' : 'gene') : 'features (all)'}`;
    $('groups').hidden = $<HTMLSelectElement>('cells').value !== 'groups';
  }

  function request(extra: Record<string, unknown> = {}) {
    const c = chosenCells();
    const meta = $<HTMLSelectElement>('meta').value;
    return { sample: ctx.sample(), assay: v().slide.assay.key, genes: parseGenes($<HTMLTextAreaElement>('genes').value), value: $<HTMLSelectElement>('value').value,
      format: $<HTMLSelectElement>('format').value, orient: $<HTMLSelectElement>('orient').value, ...(meta ? { meta: [meta] } : {}), ...c.body, ...extra };
  }

  const note = (html: string) => { $('note').innerHTML = html; $('note').hidden = !html; };

  async function exportMatrix() {
    const go = $<HTMLButtonElement>('export');
    try { localStorage.setItem(STORE, $<HTMLTextAreaElement>('genes').value); } catch { /* private window */ }
    go.disabled = true;
    note('Preparing the matrix…');
    try {
      const res = await fetch(`/hub/api/datasets/${encodeURIComponent(ctx.dataset)}/matrix`, { method: 'POST', headers: ctx.headers(true), body: JSON.stringify(request()) });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error ?? `Export failed (${res.status})`);
      const info = JSON.parse(res.headers.get('x-matrix') || '{}');
      const name = /filename="([^"]+)"/.exec(res.headers.get('content-disposition') ?? '')?.[1] ?? 'matrix.csv';
      note(`Downloading ${info.cells?.toLocaleString()} cells × ${info.genes?.toLocaleString()} features…`);
      const blob = await res.blob();
      ctx.save(blob, name);
      const conv = Object.entries((info.converted ?? {}) as Record<string, string>);
      note([`<b>${name}</b> · ${info.cells.toLocaleString()} cells × ${info.genes.toLocaleString()} ${info.value === 'counts' ? 'counts' : 'normalized values'} · ${(blob.size / 1e6).toFixed(1)} MB`,
        conv.length ? `<span class="warn">Matched by another spelling: ${esc(conv.slice(0, 10).map(([a, b]) => `${a} → ${b}`).join(', '))}</span>` : '',
        info.missing?.length ? `<span class="warn">Not in this dataset: ${esc(info.missing.slice(0, 12).join(', '))}${info.missing.length > 12 ? ' …' : ''}</span>` : '',
        info.missingCells ? `<span class="warn">${info.missingCells} cell ids not found</span>` : ''].filter(Boolean).join('<br>'));
    } catch (e) {
      note(`<span class="warn">${esc((e as Error).message)}</span>`);
    } finally {
      go.disabled = false;
    }
  }

  async function copyApi() {
    const body = request();
    const url = `${location.origin}/hub/api/datasets/${encodeURIComponent(ctx.dataset)}/matrix`;
    let cmd: string;
    if (!('mask' in body)) {
      const q = new URLSearchParams();
      for (const [k, val] of Object.entries(body)) {
        if (Array.isArray(val)) { if (k === 'genes') { if (val.length) q.set(k, val.join(',')); } else val.forEach((x) => q.append(k, String(x))); }
        else if (val !== '' && val != null) q.set(k, String(val));
      }
      cmd = `curl -o '${ctx.dataset}.${body.format === 'mtx' ? 'zip' : body.format}' '${url}?${q.toString().replace(/'/g, '%27')}'`;
    } else {
      cmd = `curl -o '${ctx.dataset}.${body.format === 'mtx' ? 'zip' : body.format}' -H 'content-type: application/json' -d '${JSON.stringify(body).replace(/'/g, "'\\''")}' '${url}'`;
    }
    try {
      await navigator.clipboard.writeText(cmd);
      note(`Copied the API call (${'mask' in body ? 'POST with the cells as a bit mask' : 'GET'}) — see the tutorial for every parameter.`);
    } catch {
      note(`<code class="mono">${esc(cmd.length > 600 ? cmd.slice(0, 600) + '…' : cmd)}</code>`);
    }
  }

  function renderTargets() {
    const sel = $<HTMLSelectElement>('target'), was = sel.value;
    const genes = parseGenes($<HTMLTextAreaElement>('genes').value);
    const view = v(), cur = view.colorBy.kind === 'gene' ? view.colorBy.gene : '';
    const list = [...new Set([...(cur ? [cur] : []), ...genes])].slice(0, 500);
    const mods = ctx.savedModules(), lastMod = ctx.lastModule();
    sel.innerHTML = (list.length ? `<optgroup label="Genes">${list.map((g) => `<option value="gene:${esc(g)}">${esc(g)}</option>`).join('')}</optgroup>` : '<option value="" disabled>Type genes above, or color by a gene</option>')
      + (mods.length || lastMod ? `<optgroup label="Module scores">${lastMod ? '<option value="current">Module score just computed</option>' : ''}${mods.map((m) => `<option value="module:${esc(m.id)}">${esc(m.name)}</option>`).join('')}</optgroup>` : '')
      + (numberObs().length ? `<optgroup label="Numeric annotations">${numberObs().map((o) => `<option value="metric:${esc(o.key)}">${esc(o.label)}</option>`).join('')}</optgroup>` : '');
    if ([...sel.options].some((o) => o.value === was)) sel.value = was;
    else if (list.length) sel.value = `gene:${list[0]}`;
  }

  function target(): Record<string, unknown> | null {
    const t = $<HTMLSelectElement>('target').value;
    if (t.startsWith('gene:')) return { gene: t.slice(5) };
    if (t.startsWith('module:')) return { module: t.slice(7) };
    if (t.startsWith('metric:')) return { metric: t.slice(7) };
    const m = ctx.lastModule();
    if (t === 'current' && m) {
      const genes = parseGenes(m.genes);
      return { moduleGenes: m.genes, method: m.method, label: `${m.method === 'seurat' ? 'Seurat' : 'AUC'} score · ${genes.slice(0, 4).join(', ')}${genes.length > 4 ? ` +${genes.length - 4}` : ''}` };
    }
    return null;
  }

  function openDialog() {
    if (!dlg) {
      dlg = document.createElement('dialog');
      dlg.className = 'ws-dialog';
      dlg.innerHTML = `<form method="dialog" class="ws-card expr-plot">
        <div class="expr-plot__fig" data-expr-fig></div>
        <div class="ws-foot"><button type="button" class="btn btn--light btn--sm" data-expr-svg>SVG</button>
          <button type="button" class="btn btn--light btn--sm" data-expr-png>PNG</button>
          <button type="button" class="btn btn--light btn--sm" data-expr-stats title="Cells, cells above 0, mean and quartiles per group">Table (CSV)</button>
          <button type="submit" class="btn btn--quiet btn--sm">Close</button></div></form>`;
      document.body.append(dlg);
      const name = () => `${ctx.dataset}_${ctx.sample()}_${last!.d.label.replace(/[^\w.-]+/g, '_')}_${last!.type}`;
      $('svg').onclick = () => last && ctx.save(new Blob([plotSvg(last.d, last.type, last.colors, ctx.ink(), last.points)], { type: 'image/svg+xml' }), `${name()}.svg`);
      $('png').onclick = async () => {
        if (!last) return;
        const svg = plotSvg(last.d, last.type, last.colors, ctx.ink(), last.points);
        const img = new Image();
        img.src = URL.createObjectURL(new Blob([svg], { type: 'image/svg+xml' }));
        await img.decode();
        const c = Object.assign(document.createElement('canvas'), { width: img.width * 3, height: img.height * 3 });
        const g = c.getContext('2d')!;
        g.scale(3, 3);
        g.drawImage(img, 0, 0);
        URL.revokeObjectURL(img.src);
        c.toBlob((b) => b && ctx.save(b, `${name()}.png`), 'image/png');
      };
      $('stats').onclick = () => {
        if (!last) return;
        const rows = [['group', 'cells', 'cells_above_0', 'pct_above_0', 'n_plotted', 'mean', 'min', 'q1', 'median', 'q3', 'max'].join(',')].concat(last.d.groups.map((g) =>
          [`"${g.name.replace(/"/g, '""')}"`, g.cells, g.expressing, g.cells ? ((100 * g.expressing) / g.cells).toFixed(2) : 0, g.n, g.mean ?? '', g.min ?? '', g.q1 ?? '', g.median ?? '', g.q3 ?? '', g.max ?? ''].join(',')));
        ctx.save(new Blob([rows.join('\n') + '\n'], { type: 'text/csv' }), `${name()}.csv`);
      };
    }
    if (!dlg.open) dlg.showModal();
  }

  async function plot() {
    if (busyPlot) return;  // one plot at a time
    const t = target();
    if (!t) { note('<span class="warn">Pick a gene or module score to plot</span>'); return; }
    const go = $<HTMLButtonElement>('plot');
    busyPlot = true;
    go.disabled = true;
    const group = $<HTMLSelectElement>('group').value, type = $<HTMLSelectElement>('kind').value as 'violin' | 'ridge';
    const points = $<HTMLInputElement>('points').checked;
    note('Plotting…');
    try {
      const c = chosenCells();
      for (;;) {
        const res = await fetch(`/hub/api/datasets/${encodeURIComponent(ctx.dataset)}/distribution`, { method: 'POST', headers: ctx.headers(true),
          body: JSON.stringify({ sample: ctx.sample(), assay: v().slide.assay.key, value: $<HTMLSelectElement>('value').value, group, nonzero: $<HTMLInputElement>('nonzero').checked,
            points: points ? 400 : 0, ...c.body, ...t }) });
        if (res.status === 202) {
          const p = await res.json();
          note(`Ranking the genes of every cell for the module score: ${Math.round((p.progress || 0) * 100)}%`);
          await new Promise((r) => setTimeout(r, 2000));
          continue;
        }
        const d = await res.json();
        if (!res.ok) throw new Error(d.error ?? `Plot failed (${res.status})`);
        const colors = group ? (v().catColors.get(group) ?? []) : ['#88c0d0'];
        last = { d, type, points, colors };
        openDialog();
        $('fig').innerHTML = plotSvg(d, type, colors, ctx.ink(), points);
        const conv = Object.entries(d.converted ?? {});
        note(conv.length ? `<span class="warn">Plotted ${esc(String(conv[0][1]))} (for ${esc(conv[0][0])})</span>` : '');
        break;
      }
    } catch (e) {
      note(`<span class="warn">${esc((e as Error).message)}</span>`);
    } finally {
      busyPlot = false;
      go.disabled = false;
    }
  }

  /** After a sample or assay is opened, or the selection or saved scores change. */
  function refresh() {
    const view = v();
    const cats = categoryObs();
    const opts = (sel: string, none: string) => (none ? `<option value="">${none}</option>` : '') + cats.map((o) => `<option value="${esc(o.key)}"${o.key === sel ? ' selected' : ''}>${esc(o.label)}</option>`).join('');
    const colSel = $<HTMLSelectElement>('col'), grpSel = $<HTMLSelectElement>('group'), metaSel = $<HTMLSelectElement>('meta');
    const col = colSel.value || view.groupBy, grp = grpSel.dataset.touched ? grpSel.value : view.groupBy, meta = metaSel.value;
    const had = colSel.value;
    colSel.innerHTML = opts(col, '');
    grpSel.innerHTML = opts(grp, 'No grouping (all together)');
    metaSel.innerHTML = opts(meta, 'None');
    if (had !== colSel.value || !$('cats').children.length) renderCats();
    const counts = !view.slide.floatValues;
    const value = $<HTMLSelectElement>('value');
    value.querySelector<HTMLOptionElement>('option[value=counts]')!.disabled = !counts;
    if (!counts) value.value = 'normalized';
    value.title = counts ? 'Counts as stored, or log1p(counts / total × 10,000) as the viewer shows them' : 'This assay keeps its values as stored (no counts)';
    $<HTMLSelectElement>('cells').querySelector<HTMLOptionElement>('option[value=selection]')!.disabled = !view.selection;
    if (!view.selection && $<HTMLSelectElement>('cells').value === 'selection') $<HTMLSelectElement>('cells').value = 'all';
    renderTargets();
    renderCount();
  }

  try { $<HTMLTextAreaElement>('genes').value = localStorage.getItem(STORE) ?? ''; } catch { /* private window */ }
  $('genes').addEventListener('input', () => { renderCount(); renderTargets(); });
  $('cells').onchange = () => { if ($<HTMLSelectElement>('cells').value === 'groups' && !$('cats').children.length) renderCats(); renderCount(); };
  $('col').onchange = renderCats;
  $('cats').addEventListener('change', renderCount);
  $('all').onclick = () => { document.querySelectorAll<HTMLInputElement>('[data-expr-cats] input').forEach((b) => (b.checked = true)); renderCount(); };
  $('none').onclick = () => { document.querySelectorAll<HTMLInputElement>('[data-expr-cats] input').forEach((b) => (b.checked = false)); renderCount(); };
  $('group').onchange = () => { $('group').dataset.touched = '1'; };
  $('format').onchange = () => { $<HTMLSelectElement>('orient').disabled = $<HTMLSelectElement>('format').value === 'mtx'; };
  $('export').onclick = exportMatrix;
  $('api').onclick = copyApi;
  $('plot').onclick = plot;
  return { refresh };
}
