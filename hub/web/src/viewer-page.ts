
  // Spatial / single-cell viewer page: the <script> of QuertermousLab/athena src/components/SpatialViewer.astro,
  // adapted to the hub (dataset in the URL path, /hub/api/projects, no Cloudflare Access sign-in).
  import { css, esc, initChrome, loadWorkspace } from './common';
  import { mountManager } from './manager';
  import { bindShared, sharedCard } from './shared';
  import { colormapGradient } from './spatial/colors';
  import { listProjects, Slide, type Kind, type Project } from './spatial/data';
  import { Viewer, type Region, type Tool } from './spatial/viewer';
  import type { AtacPanel } from './spatial/atacPanel';

  const $ = <T extends HTMLElement = HTMLElement>(s: string) => document.querySelector<T>(`[data-${s}]`)!;
  const params = new URLSearchParams(location.search);
  const kind = ($('sv').dataset.kind ?? 'spatial') as Kind;
  const kindPath = kind === 'spatial' ? 'spatial' : 'single-cell';
  // hub permalinks: /spatial/<dataset>/?s=<slide>&gene=<gene>
  const projectId = decodeURIComponent(location.pathname.split('/')[2] ?? '') || (params.get('p') ?? '');
  let slideId = params.get('s') ?? '';
  let assayKey = params.get('assay') ?? '';
  const query = (gene?: string) => `?s=${encodeURIComponent(slideId)}${assayKey && v?.slide.assays.length > 1 ? `&assay=${encodeURIComponent(assayKey)}` : ''}${gene ? `&gene=${encodeURIComponent(gene)}` : ''}`;
  initChrome(kindPath);
  const viewerName = kind === 'spatial' ? 'Spatial viewer' : 'Single-cell viewer';
  const canvas = $('canvas'), msg = $('msg');
  let v: Viewer;
  let panel: AtacPanel | null = null; // chromatin accessibility, for scATAC datasets with fragments

  const fail = (html: string) => { msg.innerHTML = html; msg.hidden = false; };
  const debounce = <A extends unknown[]>(fn: (...a: A) => void, ms = 60) => {
    let t = 0;
    return (...a: A) => { clearTimeout(t); t = window.setTimeout(() => fn(...a), ms); };
  };
  const save = (blob: Blob, name: string) => {
    const a = Object.assign(document.createElement('a'), { href: URL.createObjectURL(blob), download: name });
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  };
  const fmt = (x: number) => (Math.abs(x) >= 100 ? x.toFixed(0) : Math.abs(x) >= 1 ? x.toFixed(2) : x.toPrecision(2));
  const storeKey = (what: string) => `vizhub-${kind}:${projectId}/${slideId}:${what}`;

  // ------------------------------------------------------------- legend

  function renderLegend() {
    const spec = v.slide.spec(v.groupBy);
    const legend = $('legend');
    if (!spec) { legend.innerHTML = ''; return; }
    const colors = v.catColors.get(spec.key)!, vis = v.catVisible.get(spec.key)!;
    const counts = v.categoryCounts(spec.key);
    const order = spec.categories!.map((_, i) => i).sort((a, b) => counts[b] - counts[a]);
    legend.innerHTML = order.map((i) => `<li class="${vis[i] ? '' : 'off'}" data-i="${i}">
        <label class="swatch" style="--c:${colors[i]}" title="Change color"><input type="color" value="${colors[i]}" data-c="${i}"></label>
        <button type="button" class="name" data-t="${i}" title="Click to show/hide · Alt-click to show only this">${esc(spec.categories![i])}</button>
        <span class="n">${counts[i].toLocaleString()}</span>
      </li>`).join('');
    $('color-group').hidden = v.colorBy.kind === 'group';
  }

  function saveColors() {
    const spec = v.slide.spec(v.groupBy);
    if (spec) localStorage.setItem(storeKey(`colors:${spec.key}`), JSON.stringify(v.catColors.get(spec.key)));
  }

  $('legend').addEventListener('input', (e) => {
    const el = e.target as HTMLInputElement;
    if (el.dataset.c == null) return;
    v.catColors.get(v.groupBy)![+el.dataset.c] = el.value;
    (el.parentElement as HTMLElement).style.setProperty('--c', el.value);
    debouncedUpdate();
    saveColors();
  });
  const debouncedUpdate = debounce(() => v.update(), 40);
  $('legend').addEventListener('click', (e) => {
    const el = (e.target as HTMLElement).closest<HTMLElement>('[data-t]');
    if (!el) return;
    const i = +el.dataset.t!, vis = v.catVisible.get(v.groupBy)!;
    if (e.altKey) vis.fill(false), (vis[i] = true);
    else vis[i] = !vis[i];
    v.update();
  });
  $('all').onclick = () => { v.catVisible.get(v.groupBy)?.fill(true); v.update(); };
  $('none').onclick = () => { v.catVisible.get(v.groupBy)?.fill(false); v.update(); };
  $('color-group').onclick = () => clearValue();
  $<HTMLSelectElement>('group').onchange = (e) => { v.groupBy = (e.target as HTMLSelectElement).value; v.update(); };

  // ------------------------------------------------------------- gene / numeric coloring

  const lo = $<HTMLInputElement>('lo'), hi = $<HTMLInputElement>('hi');
  const loS = $<HTMLInputElement>('lo-slider'), hiS = $<HTMLInputElement>('hi-slider');
  const toSlider = (x: number) => { const [a, b] = v.valueExtent; return String(Math.round(((x - a) / (b - a || 1)) * 1000)); };
  const fromSlider = (s: string) => { const [a, b] = v.valueExtent; return a + (+s / 1000) * (b - a); };

  function renderScale() {
    const on = v.colorBy.kind !== 'group';
    $('scale').hidden = !on;
    if (!on) return;
    $('scale-name').textContent = v.colorBy.kind === 'gene' ? v.slide.label(v.colorBy.gene) : v.valueLabel();
    $<HTMLSelectElement>('norm').parentElement!.hidden = v.colorBy.kind !== 'gene' || v.slide.floatValues;
    lo.value = fmt(v.range[0]); hi.value = fmt(v.range[1]);
    loS.value = toSlider(v.range[0]); hiS.value = toSlider(v.range[1]);
    $('gradient').style.background = colormapGradient(v.colormap);
    $<HTMLSelectElement>('below').value = v.belowMin;
    drawHistogram();
  }

  function drawHistogram() {
    const c = $<HTMLCanvasElement>('hist'), ctx = c.getContext('2d')!;
    ctx.clearRect(0, 0, c.width, c.height);
    if (!v.values) return;
    const sparse = v.colorBy.kind === 'gene' || v.colorBy.kind === 'values';
    const [a, b] = v.valueExtent, bins = new Array(80).fill(0);
    let zeros = 0;
    for (const x of v.values) {
      if (!Number.isFinite(x)) continue;
      if (sparse && x === 0) { zeros++; continue; }
      bins[Math.min(79, Math.floor(((x - a) / (b - a || 1)) * 80))]++;
    }
    const max = Math.max(...bins, 1), w = c.width / 80;
    const [r0, r1] = v.range.map((x) => ((x - a) / (b - a || 1)) * c.width);
    ctx.fillStyle = css('--accent-soft');
    ctx.fillRect(r0, 0, r1 - r0, c.height);
    bins.forEach((n, i) => {
      const x = i * w, inRange = x + w / 2 >= r0 && x + w / 2 <= r1;
      ctx.fillStyle = inRange ? css('--accent') : css('--border-strong');
      const h = (Math.sqrt(n / max)) * (c.height - 16);
      ctx.fillRect(x + 1, c.height - h, w - 2, h);
    });
    ctx.fillStyle = css('--text-3');
    ctx.font = '20px sans-serif';
    ctx.fillText(`${fmt(a)} – ${fmt(b)}${sparse ? ` · ${(zeros / v.values.length * 100).toFixed(0)}% of cells are 0` : ''}`, 4, 18);
  }

  // ------------------------------------------------------------- assays (multiome: RNA, peaks, gene activity, chromVAR)

  const ASSAY_TITLE: Record<string, string> = { rna: 'Gene expression', peaks: 'Peak accessibility', activity: 'Gene activity',
    chromvar: 'chromVAR motif deviations', protein: 'Protein' };
  const PEAK_NAME = /^(chr[\w.]+|[0-9XYM]+)[-:_](\d+)[-_](\d+)$/;
  const peakLocus = (name: string) => { const m = name.match(PEAK_NAME); return m ? { chrom: m[1], start: +m[2], end: +m[3] } : null; };
  const DATALIST_MAX = 20000;

  /** Find a feature of the current assay by name, display label (motif name) or, for peaks, chr:start-end. */
  function findFeature(text: string) {
    const s = v.slide, q = text.trim(), ql = q.toLowerCase();
    if (!q) return null;
    const byName = s.features.find((f) => f.toLowerCase() === ql);
    if (byName) return byName;
    if (s.labels) {
      const i = s.labels.findIndex((l) => l.toLowerCase() === ql || l.toLowerCase().startsWith(`${ql} (`));
      if (i >= 0) return s.features[i];
    }
    if (s.assay.type === 'peaks') {
      const want = peakLocus(q.replace(/,/g, ''));
      if (want) return s.features.find((f) => { const p = peakLocus(f); return p && p.chrom === want.chrom && p.start === want.start && p.end === want.end; }) ?? null;
    }
    return null;
  }

  function fillDatalist(filter = '') {
    const s = v.slide, names = s.labels ?? s.features, f = filter.trim().toLowerCase();
    const list = names.length <= DATALIST_MAX ? names : f.length < 2 ? [] : names.filter((n) => n.toLowerCase().includes(f)).slice(0, 100);
    document.getElementById('sv-genes')!.innerHTML = list.map((g) => `<option value="${esc(g)}">`).join('');
  }

  /** Title, placeholder and feature list for the current assay. */
  function renderAssay() {
    const s = v.slide, a = s.assay, n = s.features.length.toLocaleString(), multi = s.assays.length > 1;
    $('feature-title').textContent = multi ? ASSAY_TITLE[a.type] ?? a.label : s.meta.feature ?? 'Gene expression';
    $<HTMLInputElement>('gene').placeholder = a.type === 'peaks' ? `Peak chr-start-end, or a gene for its region (${n} peaks)`
      : a.type === 'chromvar' ? `Motif name or ID (${n} motifs)` : `Gene symbol (${n} ${s.meta.panel ? 'on this panel' : 'in this dataset'})`;
    fillDatalist();
    $('assay-row').hidden = !multi;
    $<HTMLSelectElement>('assay').value = a.key;
  }

  async function switchAssay(key: string) {
    $('status').textContent = 'Loading assay…';
    await v.slide.setAssay(key);
    assayKey = key;
    renderAssay();
    clearValue();
    updatePanel();
    renderStatus();
  }

  // the accessibility tracks follow every assay with a genomic position: genes (RNA, SCT, gene activity) open
  // their region, peaks are marked; chromVAR motifs have no single locus, so the tracks hide for them
  const panelWanted = () => !!v.slide.meta.atac && v.slide.assay.type !== 'chromvar';
  let panelLoading: Promise<void> | null = null;
  function updatePanel() {
    const want = panelWanted();
    if (want && !panelLoading) panelLoading = setupAtac().catch((e) => { $('atac-info').textContent = (e as Error).message; });
    $('atac').hidden = !want;
  }

  // ------------------------------------------------------------- chromVAR motifs

  const LOGO_COLORS = ['#3c9a5f', '#3b6ea8', '#e0a526', '#cc4b3c']; // A C G T
  let motifShown: [number, number, number, number][] | null = null;

  function drawLogo() {
    const c = $<HTMLCanvasElement>('motif-logo'), pwm = motifShown;
    if (!pwm) return;
    const dpr = window.devicePixelRatio || 1, W = c.clientWidth || 280, H = c.clientHeight || 86;
    c.width = W * dpr; c.height = H * dpr;
    const ctx = c.getContext('2d')!;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);
    const L = 22, B = 14, plotH = H - B - 4, colW = (W - L) / pwm.length;
    ctx.fillStyle = css('--text-3'); ctx.strokeStyle = css('--border-strong');
    ctx.font = '10px "Manrope Variable", sans-serif';
    ctx.textAlign = 'right';
    for (const bits of [0, 1, 2]) {
      const y = 4 + plotH - (bits / 2) * plotH;
      ctx.fillText(String(bits), L - 6, y + 3);
      ctx.beginPath(); ctx.moveTo(L - 3, y); ctx.lineTo(L, y); ctx.stroke();
    }
    ctx.save(); ctx.translate(8, 4 + plotH / 2); ctx.rotate(-Math.PI / 2); ctx.textAlign = 'center'; ctx.fillText('bits', 0, 0); ctx.restore();
    ctx.textAlign = 'center';
    ctx.font = 'bold 100px "JetBrains Mono Variable", monospace';
    pwm.forEach((col, x) => {
      if (pwm.length <= 30 || x % 5 === 0) { ctx.save(); ctx.font = '9px "Manrope Variable", sans-serif'; ctx.fillStyle = css('--text-3'); ctx.fillText(String(x + 1), L + (x + 0.5) * colW, H - 2); ctx.restore(); }
      const ic = 2 + col.reduce((t, p) => t + (p > 0 ? p * Math.log2(p) : 0), 0);
      let y = 4 + plotH;
      [0, 1, 2, 3].sort((a, b) => col[a] - col[b]).forEach((k) => {
        const h = (col[k] * ic / 2) * plotH;
        if (h < 0.5) return;
        const letter = 'ACGT'[k], mtr = ctx.measureText(letter);
        const gh = mtr.actualBoundingBoxAscent + mtr.actualBoundingBoxDescent || 72;
        ctx.save();
        ctx.translate(L + (x + 0.5) * colW, y);
        ctx.scale((colW * 0.94) / mtr.width, h / gh);
        ctx.fillStyle = LOGO_COLORS[k];
        ctx.fillText(letter, 0, -mtr.actualBoundingBoxDescent);
        ctx.restore();
        y -= h;
      });
    });
  }

  async function showMotif(id: string | null) {
    const motifs = id ? await v.slide.motifs() : {};
    const mo = id ? motifs[id] : undefined;
    $('motif').hidden = !mo;
    motifShown = mo?.pwm ?? null;
    if (!mo) return;
    $('motif-name').textContent = mo.name;
    $('motif-id').textContent = `${id} · ${mo.pwm.length} bp`;
    drawLogo();
  }

  async function colorByGene(gene: string) {
    const s = v.slide, match = findFeature(gene);
    if (!match) {
      const q = gene.trim();
      if (q && panel && panelWanted() && s.assay.type === 'peaks') {
        // a gene name in the peaks assay: open its region in the tracks; click a peak there to color the cells
        const g = await panel.goToGene(q);
        if (g) { $<HTMLInputElement>('atac-locus').value = g.name; $('status').textContent = `${g.name}: click a peak in the tracks below, then “Color cells by this region”`; return; }
      }
      if (q) $('status').textContent = `${q} is not in this assay`;
      return;
    }
    $<HTMLInputElement>('gene').value = s.label(match);
    $<HTMLSelectElement>('metric').value = '';
    $('status').textContent = `Loading ${s.label(match)}…`;
    try {
      await v.setColorBy({ kind: 'gene', gene: match });
      if (s.assay.type === 'chromvar') {
        // deviation z-scores: symmetric range on a diverging scale, every cell colored
        const abs = Array.from(v.values!, Math.abs).filter(Number.isFinite).sort((a, b) => a - b);
        const q = abs[Math.floor(abs.length * 0.99)] || 1;
        v.range = [-q, q];
        v.belowMin = 'color';
        v.colormap = 'rdbu';
        $<HTMLSelectElement>('cmap').value = 'rdbu';
        v.update();
        showMotif(match);
      } else {
        showMotif(null);
      }
      if (panel && panelWanted()) {
        const pk = s.assay.type === 'peaks' ? peakLocus(match) : null;
        if (pk) {
          const pad = Math.max(20_000, (pk.end - pk.start) * 10);
          await panel.goTo({ chrom: pk.chrom, start: Math.max(0, pk.start - pad), end: pk.end + pad });
          panel.mark = [pk.start, pk.end];
          $('atac-color').hidden = false;
          panel.draw();
          $<HTMLInputElement>('atac-locus').value = `${pk.chrom}:${pk.start + 1}-${pk.end}`;
        } else if (s.assay.type !== 'chromvar') {
          $<HTMLInputElement>('atac-locus').value = match;
          panel.goToGene(match);
        }
      }
      history.replaceState(null, '', query(match));
    } catch (e) {
      $('status').textContent = (e as Error).message;
    }
  }

  function clearValue() {
    showMotif(null);
    $<HTMLInputElement>('gene').value = '';
    $<HTMLSelectElement>('metric').value = '';
    v.setColorBy({ kind: 'group' });
    history.replaceState(null, '', query());
  }

  $<HTMLInputElement>('gene').addEventListener('change', (e) => colorByGene((e.target as HTMLInputElement).value));
  $<HTMLInputElement>('gene').addEventListener('input', debounce((e: Event) => { if (v.slide.features.length > DATALIST_MAX) fillDatalist((e.target as HTMLInputElement).value); }, 150));
  $<HTMLSelectElement>('assay').onchange = (e) => switchAssay((e.target as HTMLSelectElement).value);
  $<HTMLInputElement>('gene').addEventListener('keydown', (e) => { if (e.key === 'Enter') colorByGene((e.target as HTMLInputElement).value); });
  $<HTMLSelectElement>('metric').onchange = (e) => {
    const key = (e.target as HTMLSelectElement).value;
    $<HTMLInputElement>('gene').value = '';
    if (key) v.setColorBy({ kind: 'obs', key }); else clearValue();
  };
  $('clear-value').onclick = clearValue;
  $<HTMLSelectElement>('norm').onchange = (e) => {
    v.norm = (e.target as HTMLSelectElement).value as 'log' | 'raw';
    if (v.colorBy.kind === 'gene') v.setColorBy({ ...v.colorBy });
  };
  $<HTMLSelectElement>('cmap').onchange = (e) => { v.colormap = (e.target as HTMLSelectElement).value; v.update(); };
  $<HTMLSelectElement>('below').onchange = (e) => { v.belowMin = (e.target as HTMLSelectElement).value as typeof v.belowMin; v.update(); };
  const setRange = (a: number, b: number) => { v.range = [Math.min(a, b), Math.max(a, b)]; debouncedUpdate(); };
  lo.onchange = () => setRange(+lo.value, v.range[1]);
  hi.onchange = () => setRange(v.range[0], +hi.value);
  loS.oninput = () => setRange(fromSlider(loS.value), v.range[1]);
  hiS.oninput = () => setRange(v.range[0], fromSlider(hiS.value));

  // ------------------------------------------------------------- regions

  const userRegions = (): Region[] => JSON.parse(localStorage.getItem(storeKey('regions')) ?? '[]');
  const allRegions = () => [...v.slide.meta.regions.map((r) => ({ ...r, preset: true })), ...userRegions()];

  function renderRegions() {
    const sel = v.selection;
    $('selection').hidden = !sel;
    if (sel) {
      const n = v.selectedIndices().length;
      $('sel-count').textContent = `${n.toLocaleString()} cells selected${v.cropped ? ' · cropped' : ''}`;
      $('crop').textContent = v.cropped ? 'Show all cells' : 'Crop to selection';
      const spec = v.slide.spec(v.groupBy);
      if (spec) {
        const counts = v.categoryCounts(spec.key, true), colors = v.catColors.get(spec.key)!;
        const top = counts.map((c, i) => [c, i]).filter(([c]) => c).sort((a, b) => b[0] - a[0]).slice(0, 8);
        $('composition').innerHTML = top.map(([c, i]) => `<li><span style="background:${colors[i]};width:${(c / n) * 100}%"></span>
          <em>${esc(spec.categories![i])}</em><b>${((c / n) * 100).toFixed(1)}%</b></li>`).join('');
      }
    }
    $('regions').innerHTML = allRegions().map((r, k) => `<li>
        <button type="button" class="name" data-r="${k}">${esc(r.name)}</button>
        <span class="n">${r.cells.length.toLocaleString()}${r.preset ? ' · preset' : ''}</span>
        ${r.preset ? '' : `<button type="button" class="x" data-del="${k}" title="Delete">×</button>`}
      </li>`).join('');
  }

  $('regions').addEventListener('click', (e) => {
    const t = e.target as HTMLElement, regions = allRegions();
    if (t.dataset.r) { v.selectRegion(regions[+t.dataset.r]); v.fitSelection(); }
    if (t.dataset.del) {
      const r = regions[+t.dataset.del];
      localStorage.setItem(storeKey('regions'), JSON.stringify(userRegions().filter((u) => u.name !== r.name)));
      renderRegions();
    }
  });
  $('crop').onclick = () => v.setCrop(!v.cropped);
  $('invert').onclick = () => v.invertSelection();
  $('clear').onclick = () => v.setSelection(null);
  $('save').onclick = () => {
    const name = prompt('Name this region', `Region ${userRegions().length + 1}`)?.trim();
    if (!name) return;
    const rest = userRegions().filter((r) => r.name !== name);
    localStorage.setItem(storeKey('regions'), JSON.stringify([...rest, { name, cells: v.selectedIndices() }]));
    renderRegions();
  };
  $('export').onclick = async () => save(await v.exportCsv(), `${projectId}_${slideId}${v.selection ? '_selection' : ''}.csv`);

  // ------------------------------------------------------------- image channels

  function renderChannels() {
    const img = v.slide.meta.image;
    $('image-section').hidden = !img;
    if (!img) return;
    $('channels').innerHTML = img.channels.map((c, i) => {
      const s = v.channels[i];
      // a brightfield image (H&E, IHC) is drawn in its own colors: no tint, but an opacity to see the spots over it
      return `<div class="channel" data-ch="${i}">
        <label class="check"><input type="checkbox" data-k="visible" ${s.visible ? 'checked' : ''}>
          ${s.rgb ? '' : `<input type="color" value="${s.color}" data-k="color">`} ${esc(c.name)}</label>
        <div class="ch-sliders">
          <label>Black <input type="range" min="0" max="254" value="${s.lo}" data-k="lo"></label>
          <label>White <input type="range" min="1" max="255" value="${s.hi}" data-k="hi"></label>
          <label>Gamma <input type="range" min="0.3" max="2.5" step="0.05" value="${s.gamma}" data-k="gamma"></label>
          ${s.rgb ? `<label>Opacity <input type="range" min="0.05" max="1" step="0.05" value="${s.opacity}" data-k="opacity"></label>` : ''}
        </div>
      </div>`;
    }).join('');
  }
  $('channels').addEventListener('input', (e) => {
    const el = e.target as HTMLInputElement, i = +el.closest<HTMLElement>('[data-ch]')!.dataset.ch!, ch = v.channels[i];
    const k = el.dataset.k!;
    if (k === 'visible') { ch.visible = el.checked; v.updateImage(true); return; }
    if (k === 'color') ch.color = el.value;
    else (ch as unknown as Record<string, number>)[k] = +el.value;
    if (ch.hi <= ch.lo) ch.hi = ch.lo + 1;
    imageUpdate();
  });
  const imageUpdate = debounce(() => v.updateImage(), 30);
  $<HTMLInputElement>('show-image').onchange = (e) => { v.showImage = (e.target as HTMLInputElement).checked; v.render(); };

  // ------------------------------------------------------------- display + toolbar

  $<HTMLSelectElement>('layout').onchange = (e) => { v.setLayout((e.target as HTMLSelectElement).value); v.update(); };
  $<HTMLSelectElement>('shape').onchange = (e) => { v.shape = (e.target as HTMLSelectElement).value as typeof v.shape; v.render(); };
  $<HTMLInputElement>('size').oninput = (e) => { v.pointScale = +(e.target as HTMLInputElement).value; v.render(); };
  $<HTMLInputElement>('opacity').oninput = (e) => { v.opacity = +(e.target as HTMLInputElement).value; v.render(); };
  // Plot background: Genome Canvas's canvas color for the theme (--canvas), or a color of the viewer's own
  // (remembered in this browser). Scale bar, outlines and "below min" cells pick a contrasting ink.
  const BG_KEY = 'vizhub-canvas-bg';
  const customBg = () => { try { return localStorage.getItem(BG_KEY); } catch { return null; } };
  const toHex = (c: string) => {
    const x = document.createElement('canvas').getContext('2d')!;
    x.fillStyle = c;
    return x.fillStyle.startsWith('#') ? x.fillStyle : '#000000';
  };
  let ink = '#eceff4';
  function applyBackground() {
    const color = toHex(customBg() || css('--canvas') || '#ffffff');
    const [r, g, b] = [1, 3, 5].map((i) => parseInt(color.slice(i, i + 2), 16) / 255);
    const dark = 0.2126 * r + 0.7152 * g + 0.0722 * b < 0.5;
    ink = dark ? '#eceff4' : '#2e3440';
    canvas.style.background = color;
    canvas.style.setProperty('--sv-ink', ink);
    canvas.style.setProperty('--sv-halo', dark ? 'rgba(0, 0, 0, .7)' : 'rgba(255, 255, 255, .85)');
    $<HTMLInputElement>('bg').value = color;
    $<HTMLInputElement>('bg-theme').checked = !customBg();
    if (v) {
      v.belowGray = dark ? [96, 103, 115] : [200, 205, 213];
      v.update();
    }
  }
  $<HTMLInputElement>('bg').oninput = (e) => {
    try { localStorage.setItem(BG_KEY, (e.target as HTMLInputElement).value); } catch { /* private window */ }
    applyBackground();
  };
  $<HTMLInputElement>('bg-theme').onchange = (e) => {
    if ((e.target as HTMLInputElement).checked) { try { localStorage.removeItem(BG_KEY); } catch { /* private window */ } }
    else { try { localStorage.setItem(BG_KEY, $<HTMLInputElement>('bg').value); } catch { /* private window */ } }
    applyBackground();
  };
  window.addEventListener('vizhub:theme', () => applyBackground());
  window.addEventListener('storage', (e) => { if (e.key === BG_KEY) applyBackground(); });
  applyBackground();
  document.querySelectorAll<HTMLButtonElement>('[data-tool]').forEach((b) => (b.onclick = () => {
    document.querySelectorAll('[data-tool]').forEach((x) => x.classList.toggle('on', x === b));
    v.setTool(b.dataset.tool as Tool);
  }));
  $('fit').onclick = () => v.fit();
  $('shot').onclick = async () => save(await v.screenshot(ink), `${projectId}_${slideId}.png`);
  const slideHref = (p: string, s: string) => `/${kindPath}/${encodeURIComponent(p)}/?s=${encodeURIComponent(s)}`;
  $<HTMLSelectElement>('slide').onchange = (e) => {
    const [p, s] = (e.target as HTMLSelectElement).value.split('\n');
    location.href = slideHref(p, s);
  };

  function fillSlides(projects: Project[]) {
    $('slide').innerHTML = (projectId ? '' : '<option value="" selected disabled>Choose a dataset…</option>') +
      projects.map((p) => `<optgroup label="${esc(p.title)}${p.mine === false && p.ownerName ? ` · shared by ${esc(p.ownerName)}` : ''}">${p.slides.map((s) =>
        `<option value="${esc(`${p.id}\n${s.id}`)}" ${p.id === projectId && s.id === slideId ? 'selected' : ''}>${esc(s.title ?? s.id)}</option>`).join('')}</optgroup>`).join('');
  }

  /** No slide chosen yet (the Visualize entry): list the datasets to load. */
  function showPicker(projects: Project[]) {
    $('sv').classList.add('sv--empty');
    msg.hidden = false;
    msg.innerHTML = projects.length ? `<div class="picker">
        <h2>Choose a dataset</h2>
        ${projects.map((p) => `<section>
          <h3>${esc(p.title)}</h3>
          <p>${esc([p.technology, p.species, p.panel].filter(Boolean).join(' · '))}</p>
          ${p.citation ? `<p class="cite">${esc(p.citation)}${p.sourceUrl ? ` · <a href="${esc(p.sourceUrl)}" target="_blank" rel="noopener">source data</a>` : ''}</p>` : ''}
          <div>${p.slides.map((s) => `<a href="${slideHref(p.id, s.id)}"><strong>${esc(s.title ?? s.id)}</strong>${s.description ? `<span>${esc(s.description)}</span>` : ''}</a>`).join('')}</div>
        </section>`).join('')}
      </div>` : `<p>No ${kind === 'spatial' ? 'spatial' : 'single-cell'} datasets in the hub yet.<br><a class="btn btn--primary btn--sm" href="/hub/#add">Add data in the hub</a></p>`;
  }

  function renderStatus() {
    const shown = v.visibleCount();
    $('status').textContent = `${shown.toLocaleString()} of ${v.slide.n.toLocaleString()} cells shown`;
  }

  // ------------------------------------------------------------- start

  async function start() {
    const back = document.querySelector<HTMLAnchorElement>('.sv-head .back')!;
    back.href = `/${kindPath}/`;
    back.textContent = `← Your ${kind === 'spatial' ? 'spatial' : 'single-cell'} datasets`;
    if (!projectId) {  // /spatial/ or /single-cell/: the data management page
      $('sv').hidden = true;
      const m = document.querySelector<HTMLElement>('[data-manager]')!;
      m.hidden = false;
      mountManager(m, kind);
      return;
    }
    await loadWorkspace();
    // other workspaces' datasets: add one and open it here (shared.ts)
    const head = document.querySelector<HTMLElement>('.sv-head')!;
    head.insertAdjacentHTML('beforeend', sharedCard(kind));
    bindShared(head, kind, { addLabel: 'Add & open', onAdded: (added) => { location.href = added[0].url; } });
    let projects: Project[];
    try {
      projects = await listProjects(kind, projectId);
    } catch (e) {
      fail(esc((e as Error).message));
      return;
    }
    const project = projects.find((p) => p.id === projectId);
    if (projectId && !project) {
      fail(`There is no ${kind === 'spatial' ? 'spatial' : 'single-cell'} dataset “${esc(projectId)}”.<br><a class="btn btn--primary btn--sm" href="/${kindPath}/">Your datasets</a>`);
      return;
    }
    const slideMeta = project ? project.slides.find((s) => s.id === slideId) ?? project.slides[0] : undefined;
    slideId = slideMeta?.id ?? '';
    fillSlides(projects);
    if (!project || !slideMeta) { showPicker(projects); return; }
    try {
      const slide = await new Slide(slideMeta.base!).load(assayKey);
      document.title = `${slide.meta.title} · ${viewerName} · Omics Canvas`;
      msg.hidden = true;
      v = new Viewer(canvas, slide);
      applyBackground();
    } catch (e) {
      fail(esc((e as Error).message));
      return;
    }
    const m = v.slide.meta;
    const tissue = m.spatial !== false;
    $('facts').textContent = [m.technology, m.species, m.panel, m.preservation?.replace(/_/g, ' '), `${m.nCells.toLocaleString()} ${m.technology?.startsWith('Slide-seq') ? 'beads' : 'cells'}`, v.slide.assays.length > 1 ? v.slide.assays.map((a) => `${a.label} ${(a.nFeatures ?? 0).toLocaleString()}`).join(' / ') : `${v.slide.features.length.toLocaleString()} genes`]
      .filter(Boolean).join(' · ');
    if (m.source?.url) {
      $('source').innerHTML = `Published data · <a href="${esc(m.source.url)}" target="_blank" rel="noopener">${esc(m.source.name)}</a>`;
      $('source').hidden = false;
    }
    $('assay').innerHTML = v.slide.assays.map((a) => `<option value="${esc(a.key)}">${esc(a.label)} · ${(a.nFeatures ?? 0).toLocaleString()}</option>`).join('');
    assayKey = v.slide.assay.key;
    renderAssay();
    $('surface').textContent = m.image ? 'image' : 'plot';

    const groups = [...new Set(m.obs.filter((o) => !o.hidden).map((o) => o.group ?? ''))];
    const optgroups = (type: string) => groups.map((g) => {
      const opts = m.obs.filter((o) => o.type === type && !o.hidden && (o.group ?? '') === g);
      return opts.length ? `<optgroup label="${esc(g || 'Other')}">${opts.map((o) => `<option value="${esc(o.key)}">${esc(o.label)}</option>`).join('')}</optgroup>` : '';
    }).join('');
    $('group').innerHTML = optgroups('category');
    $<HTMLSelectElement>('group').value = v.groupBy;
    $('metric').innerHTML = '<option value="">—</option>' + optgroups('number');
    $('layout').innerHTML = (tissue ? '<option value="spatial">Spatial</option>' : '') + m.embeddings.map((e) => `<option value="${esc(e.key)}">${esc(e.label)}</option>`).join('');
    $<HTMLSelectElement>('layout').value = v.layout;
    if (!m.boundaries) $('shape-row').hidden = true;

    // custom colors chosen earlier on this slide
    for (const o of m.obs) if (o.type === 'category') {
      const saved = localStorage.getItem(storeKey(`colors:${o.key}`));
      if (saved) try { const c = JSON.parse(saved); if (c.length === o.categories!.length) v.catColors.set(o.key, c); } catch { /* ignore */ }
    }

    const panelRefresh = debounce(() => panel?.refresh(), 80);
    v.onChange = () => { renderLegend(); renderScale(); renderRegions(); renderStatus(); panelRefresh(); };
    updatePanel();
    renderChannels();
    v.update();
    const gene = params.get('gene');
    if (gene) colorByGene(gene);
    new ResizeObserver(() => v.render()).observe(canvas);
    window.addEventListener('vizhub:theme', () => { renderScale(); panel?.draw(); drawLogo(); });
    new ResizeObserver(() => drawLogo()).observe($('motif'));
  }
  /** The coverage panel under the plot; it follows the side panel through v.onChange. */
  // first of these found in the dataset's gene annotation (looked up case-insensitively)
  const DEFAULT_ATAC_GENES = ['Myh11', 'Acta2', 'Gapdh', 'Actb'];

  async function setupAtac() {
    const [{ Atac }, { AtacPanel, parseLocus, fmtLocus }] = await Promise.all([import('./spatial/atac'), import('./spatial/atacPanel')]);
    $('atac').hidden = !panelWanted();  // the assay may have changed while the modules loaded
    const hint = $('atac-info').textContent!;
    $('atac-info').textContent = 'Loading chromatin accessibility…';
    const atac = await new Atac(v.slide, v.slide.meta.atac!.file).load();
    $('atac-info').textContent = hint;
    const p = new AtacPanel($<HTMLCanvasElement>('atac-canvas'), $('atac-tip'), v, atac);
    p.onInfo = (t) => { $('atac-info').textContent = t; };
    p.onMark = (mark) => { $('atac-color').hidden = !mark; };
    panel = p;
    const input = $<HTMLInputElement>('atac-locus');
    const go = async (text: string) => {
      const locus = parseLocus(text);
      if (locus) return p.goTo(locus);
      const g = text.trim() && await p.goToGene(text.trim());
      if (g) input.value = g.name;
    };
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter') go(input.value); });
    document.querySelectorAll<HTMLButtonElement>('[data-atac-move]').forEach((b) => (b.onclick = () => p.move(+b.dataset.atacMove!)));
    document.querySelectorAll<HTMLButtonElement>('[data-atac-zoom]').forEach((b) => (b.onclick = () => p.zoom(+b.dataset.atacZoom!)));
    $<HTMLInputElement>('atac-shared').onchange = (e) => { p.shared = (e.target as HTMLInputElement).checked; p.draw(); };
    if (atac.meta.peaks && atac.meta.peaks.minNlog10q !== 0) {  // MACS3 peaks have q values; an object's peak set does not
      $('atac-peaks-row').hidden = false;
      $<HTMLSelectElement>('atac-peakq').onchange = (e) => { p.peakMinQ = +(e.target as HTMLSelectElement).value; p.draw(); };
    }
    $('atac-color').onclick = async () => {
      if (!p.mark || !p.locus) return;
      const locus = { chrom: p.locus.chrom, start: p.mark[0], end: p.mark[1] }, label = `Accessibility ${fmtLocus(locus)}`;
      $('status').textContent = `Loading ${label}…`;
      const values = await atac.regionValues(locus);
      $<HTMLInputElement>('gene').value = '';
      $<HTMLSelectElement>('metric').value = '';
      await v.setColorBy({ kind: 'values', label, values });
    };
    $('atac-fold').onclick = () => {
      const folded = $('atac').classList.toggle('folded');
      $('atac-fold').textContent = folded ? '▸' : '▾';
    };
    if (v.colorBy.kind === 'gene' || params.get('gene')) {
      const g = v.colorBy.kind === 'gene' ? v.colorBy.gene : params.get('gene')!;
      const pk = peakLocus(g);
      if (pk) { await p.goTo({ chrom: pk.chrom, start: Math.max(0, pk.start - 20_000), end: pk.end + 20_000 }); p.mark = [pk.start, pk.end]; $('atac-color').hidden = false; p.draw(); return; }
      input.value = g; await go(g); return;
    }
    // nothing chosen yet: open at a smooth muscle marker so the tracks show right away
    for (const marker of DEFAULT_ATAC_GENES) {
      const g = await p.atac.locate(marker);
      if (g) { input.value = g.name; await go(g.name); return; }
    }
  }

  start();
