
  // CRISPR screen explorer page: the <script> of QuertermousLab/athena src/components/CrisprExplorer.astro,
  // adapted to the hub (screen in the URL path /crispr/<screen>/, /hub/api/projects, no sign-in).
  import { css, esc, initChrome, loadWorkspace } from './common';
  import { mountManager } from './manager';
  import { geneLinks, listScreens, loadTests, PerturbSeq, SIGNIFICANT, type Screen, type Test } from './crispr/data';
  import { Scatter } from './crispr/plots';
  import { listProjects, type Project } from './spatial/data';

  const $ = <T extends HTMLElement = HTMLElement>(s: string) => document.querySelector<T>(`[data-${s}]`)!;
  const params = new URLSearchParams(location.search);
  // data colors from the theme (--down/--up/--ns), read at draw time so plots follow light/dark
  const C = { get down() { return css('--down'); }, get up() { return css('--up'); }, get ns() { return css('--ns'); } };
  const plotA = new Scatter($<HTMLCanvasElement>('plot-a'), $('tip-a'));
  const plotB = new Scatter($<HTMLCanvasElement>('plot-b'), $('tip-b'));
  let screen: Screen;
  let singleCell: Project[] = [];
  const screenId = decodeURIComponent(location.pathname.split('/')[2] ?? '') || (params.get('s') ?? '');
  initChrome('crispr');
  window.addEventListener('vizhub:theme', () => { plotA.draw(); plotB.draw(); });

  /** a P value stored as -log10 P */
  const p10 = (nlog: number) => { const p = 10 ** -nlog; return nlog >= 300 ? '< 1e-300' : p >= 0.001 ? p.toPrecision(2) : p.toExponential(1); };
  const lfc = (v: number) => `${v > 0 ? '+' : ''}${v.toFixed(2)}`;
  const setUrl = (k: string, v: string | null) => {
    v ? params.set(k, v) : params.delete(k);
    history.replaceState(null, '', `?${params}`);
  };
  const fail = (html: string) => { $('msg').innerHTML = html; $('msg').hidden = false; };
  const save = (text: string, name: string) => {
    const a = Object.assign(document.createElement('a'), { href: URL.createObjectURL(new Blob([text], { type: 'text/csv' })), download: name });
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  };
  const linkButtons = (gene: string) => {
    const l = geneLinks(gene, screen.species, singleCell);
    return `${l.locus ? `<a class="btn btn--light btn--sm" href="/genome-canvas/" target="_blank" rel="noopener" data-copy="${esc(gene)}" title="Copies ${esc(gene)} so you can paste it into the Genome Canvas locus box">${esc(gene)} in Genome Canvas</a>` : ''}
      ${l.singleCell ? `<a class="btn btn--light btn--sm" href="${esc(l.singleCell)}">${esc(gene)} in single-cell viewer</a>` : ''}`;
  };
  // Genome Canvas has no locus URL parameter: put the gene or region on the clipboard on the way there
  document.addEventListener('click', (e) => {
    const a = (e.target as HTMLElement).closest<HTMLElement>('[data-copy]');
    if (a) navigator.clipboard?.writeText(a.dataset.copy!).catch(() => {});
  });

  // ============================================================= enhancer screens

  async function enhancer() {
    const tests = await loadTests(screen.base);
    // elements without coordinates (e.g. SCEPTRE grna_target names) are keyed by their name
    const key = (t: Test) => (t.chrom ? `${t.chrom}:${t.start + 1}-${t.end}` : t.element ?? t.set);
    let shown: Test[] = [], selected: string | null = params.get('t');
    $<HTMLInputElement>('q').placeholder = 'Gene, enhancer or chr:start-end';
    $('title-a').textContent = 'All enhancer–gene tests';
    $('hint-a').textContent = 'log2 fold change of the gene after CRISPRi of the enhancer · click a point';

    const matches = (t: Test, q: string) => {
      if (!q) return true;
      const m = q.replace(/,/g, '').match(/^(chr\w+):(\d+)-(\d+)$/i);
      if (m) return t.chrom === m[1] && t.end > +m[2] && t.start < +m[3];
      const s = q.toLowerCase();
      return t.gene.toLowerCase() === s || t.gene.toLowerCase().startsWith(s) || key(t).includes(s) || t.set.toLowerCase().includes(s);
    };

    function render() {
      const q = $<HTMLInputElement>('q').value.trim(), sig = $<HTMLInputElement>('sig').checked;
      shown = tests.filter((t) => (!sig || t.significant) && matches(t, q)).sort((a, b) => b.nlog10p - a.nlog10p);
      $('count').textContent = `${shown.length.toLocaleString()} of ${tests.length.toLocaleString()} tests · ${shown.filter((t) => t.significant).length} significant (FDR < ${screen.fdr ?? 0.1})`;
      $('table').innerHTML = `<thead><tr><th>Enhancer</th><th>Gene</th><th>log2FC</th><th>FDR</th></tr></thead><tbody>${shown.slice(0, 400).map((t, i) =>
        `<tr data-i="${i}" class="${key(t) === selected ? 'on' : ''}${t.significant ? ' sig' : ''}"><td><span class="mono">${esc(key(t))}</span><br><span class="muted">${esc(t.set)}</span></td>
          <td><b>${esc(t.gene)}</b></td><td>${lfc(t.log2fc)}</td><td>${p10(t.nlog10padj)}</td></tr>`).join('')}</tbody>`;
      const sel = new Set(shown.map((t, i) => (key(t) === selected ? i : -1)).filter((i) => i >= 0));
      const top = new Set(shown.map((t, i) => [t, i] as const).filter(([t]) => t.significant).slice(0, 10).map(([, i]) => i));
      plotA.set({
        x: shown.map((t) => t.log2fc), y: shown.map((t) => t.nlog10p),
        color: (i) => (shown[i].significant ? (shown[i].log2fc < 0 ? C.down : C.up) : C.ns),
        label: (i) => `${shown[i].gene} ← ${key(shown[i])} · log2FC ${lfc(shown[i].log2fc)} · FDR ${p10(shown[i].nlog10padj)}`,
        highlight: sel, named: top, name: (i) => shown[i].gene,
        xLabel: 'log2 fold change', yLabel: '−log10 P', xLines: [0],
      });
      detail();
    }

    function detail() {
      const rows = tests.filter((t) => key(t) === selected);
      if (!rows.length) { $('detail').innerHTML = '<p class="muted">Select an enhancer–gene test in the table or the plot.</p>'; return; }
      const t = rows[0], pad = 150_000;
      const locus = t.chrom ? `${t.chrom}:${Math.max(1, t.start - pad)}-${t.end + pad}` : '';
      $('detail').innerHTML = `<h3>Enhancer <span class="mono">${esc(key(t))}</span></h3>
        <p class="muted">${[t.set, `${t.guides} guides`, t.chrom ? `${(t.end - t.start).toLocaleString()} bp` : '', screen.cell ?? ''].filter(Boolean).map(esc).join(' · ')}</p>
        <table class="table"><thead><tr><th>Gene tested</th><th>log2FC</th><th>P</th><th>FDR</th><th></th></tr></thead><tbody>
        ${rows.sort((a, b) => b.nlog10p - a.nlog10p).map((r) => `<tr class="${r.significant ? 'sig' : ''}"><td><b>${esc(r.gene)}</b></td><td>${lfc(r.log2fc)}</td>
          <td>${p10(r.nlog10p)}</td><td>${p10(r.nlog10padj)}</td><td>${r.significant ? '<span class="pill--ok">significant</span>' : ''}</td></tr>`).join('')}
        </tbody></table>
        <div class="btns">${locus && screen.species !== 'Mouse' ? `<a class="btn btn--primary btn--sm" href="/genome-canvas/" target="_blank" rel="noopener" data-copy="${esc(locus)}" title="Copies ${esc(locus)} for the Genome Canvas locus box">Open ±150 kb in Genome Canvas</a>` : ''}
          ${linkButtons(rows.find((r) => r.significant)?.gene ?? t.gene)}</div>`;
    }

    const select = (t: Test | undefined) => { selected = t ? key(t) : null; setUrl('t', selected); render(); };
    $('table').onclick = (e) => { const tr = (e.target as HTMLElement).closest<HTMLElement>('tr[data-i]'); if (tr) select(shown[+tr.dataset.i!]); };
    plotA.onClick = (i) => i != null && select(shown[i]);
    $<HTMLInputElement>('q').oninput = render;
    $<HTMLInputElement>('sig').onchange = render;
    if (params.get('g')) $<HTMLInputElement>('q').value = params.get('g')!;
    render();
  }

  // ============================================================= Perturb-seq screens

  async function perturbseq() {
    const ps = await new PerturbSeq(screen.base).load();
    const T = ps.targets, deg = (i: number) => T[i].up + T[i].down;
    let order: number[] = [], current = Math.max(0, T.findIndex((t) => t.name === params.get('t')));
    if (!params.get('t')) current = T.map((_, i) => i).sort((a, b) => deg(b) - deg(a))[0];
    $<HTMLInputElement>('q').placeholder = 'Target gene';
    $('sig-label').textContent = 'Knockdown confirmed only';
    $('panel-b').hidden = false;
    $('title-a').textContent = 'Targets ranked by number of affected genes';
    $('hint-a').textContent = 'genes with FDR < 0.1 · colored = target knockdown confirmed · click a point';

    function renderList() {
      const q = $<HTMLInputElement>('q').value.trim().toLowerCase(), sig = $<HTMLInputElement>('sig').checked;
      order = T.map((_, i) => i).filter((i) => (!q || T[i].name.toLowerCase().startsWith(q)) && (!sig || T[i].knockdown?.significant))
        .sort((a, b) => deg(b) - deg(a) || a - b);
      $('count').textContent = `${order.length} of ${T.length} targets · ${ps.genes.length.toLocaleString()} readout genes`;
      $('table').innerHTML = `<thead><tr><th>Target</th><th>Knockdown</th><th>↓ / ↑ genes</th></tr></thead><tbody>${order.map((i) => {
        const t = T[i], k = t.knockdown;
        return `<tr data-i="${i}" class="${i === current ? 'on' : ''}"><td><b>${esc(t.name)}</b></td>
          <td>${k ? `${lfc(k.log2fc)} ${k.significant ? '✓' : '<span class="muted">n.s.</span>'}` : '—'}</td>
          <td>${t.down} / ${t.up}</td></tr>`;
      }).join('')}</tbody>`;
      const rank = [...order].sort((a, b) => deg(b) - deg(a));
      const pos = new Map(rank.map((ti, r) => [ti, r]));
      plotA.set({
        x: rank.map((_, r) => r + 1), y: rank.map((ti) => deg(ti)), yLog: true,
        color: (r) => (T[rank[r]].knockdown?.significant ? C.down : C.ns),
        label: (r) => { const t = T[rank[r]]; return `${t.name} · ${t.down} down, ${t.up} up · knockdown ${t.knockdown ? lfc(t.knockdown.log2fc) : '—'}`; },
        highlight: new Set(pos.has(current) ? [pos.get(current)!] : []),
        named: new Set(rank.slice(0, 8).map((_, r) => r)), name: (r) => T[rank[r]].name,
        xLabel: 'Target rank', yLabel: 'Affected genes (FDR < 0.1)',
      });
      plotA.onClick = (r) => r != null && selectTarget(rank[r]);
    }

    async function renderTarget() {
      const t = T[current];
      $('title-b').textContent = `${t.name} knockdown: effect on every readout gene`;
      const e = await ps.effectsOfTarget(current);
      if (T[current] !== t) return;
      const find = $<HTMLInputElement>('find').value.trim().toLowerCase();
      const self = ps.geneIndex(t.name);
      const hl = new Set<number>();
      for (let k = 0; k < e.index.length; k++) {
        const g = ps.genes[e.index[k]].toLowerCase();
        if (e.index[k] === self || (find && g === find)) hl.add(k);
      }
      const named = new Set<number>();
      for (let k = 0; k < e.index.length && named.size < 12; k++) if (e.nlog10padj[k] >= SIGNIFICANT) named.add(k);
      plotB.set({
        x: e.log2fc, y: e.nlog10p,
        color: (k) => (e.nlog10padj[k] >= SIGNIFICANT ? (e.log2fc[k] < 0 ? C.down : C.up) : C.ns),
        label: (k) => `${ps.genes[e.index[k]]} · log2FC ${lfc(e.log2fc[k])} · FDR ${p10(e.nlog10padj[k])}`,
        highlight: hl, named, name: (k) => ps.genes[e.index[k]],
        xLabel: 'log2 fold change', yLabel: '−log10 P', xLines: [0],
      });
      plotB.onClick = (k) => k != null && lookupGene(ps.genes[e.index[k]]);

      const sig: number[] = [];
      for (let k = 0; k < e.index.length; k++) if (e.nlog10padj[k] >= SIGNIFICANT) sig.push(k);
      const kd = t.knockdown;
      $('detail').innerHTML = `<h3>${esc(t.name)}</h3>
        <p class="muted">guides at <span class="mono">${esc(t.chrom)}:${(t.start + 1).toLocaleString()}-${t.end.toLocaleString()}</span> · ${t.guides} guides ·
          knockdown ${kd ? `log2FC ${lfc(kd.log2fc)}, FDR ${p10(kd.nlog10padj)} ${kd.significant ? '(confirmed)' : '(not significant)'}` : 'not tested'} ·
          ${t.tested.toLocaleString()} readout genes tested</p>
        <div class="btns">${linkButtons(t.name)}<button type="button" class="btn btn--light btn--sm" data-csv>Download all ${t.tested.toLocaleString()} results (CSV)</button></div>
        <h4>${sig.length} affected genes (FDR < 0.1)</h4>
        ${sig.length ? `<div class="cx-table cx-table--short"><table class="table"><thead><tr><th>Gene</th><th>log2FC</th><th>FDR</th></tr></thead><tbody>
          ${sig.slice(0, 200).map((k) => `<tr data-g="${esc(ps.genes[e.index[k]])}"><td><b>${esc(ps.genes[e.index[k]])}</b></td><td>${lfc(e.log2fc[k])}</td><td>${p10(e.nlog10padj[k])}</td></tr>`).join('')}
          </tbody></table></div>` : '<p class="muted">None at FDR < 0.1.</p>'}`;
      $('detail').querySelector<HTMLElement>('[data-csv]')!.onclick = () => {
        const rows = ['gene,log2fc,p_value,adj_p_value'];
        for (let k = 0; k < e.index.length; k++) rows.push(`${ps.genes[e.index[k]]},${e.log2fc[k]},${10 ** -e.nlog10p[k]},${10 ** -e.nlog10padj[k]}`);
        save(rows.join('\n') + '\n', `${screen.id}_${t.name}.csv`);
      };
      $('detail').querySelector('tbody')?.addEventListener('click', (ev) => {
        const g = (ev.target as HTMLElement).closest<HTMLElement>('tr[data-g]')?.dataset.g;
        if (g) lookupGene(g);
      });
    }

    async function lookupGene(gene: string) {
      const gi = ps.geneIndex(gene);
      const box = $('lookup');
      box.hidden = false;
      if (gi < 0) { box.innerHTML = `<h3>${esc(gene)}</h3><p class="muted">Not a readout gene in this screen.</p>`; return; }
      setUrl('g', ps.genes[gi]);
      $<HTMLInputElement>('find').value = ps.genes[gi];
      renderTarget();
      const e = await ps.effectsOnGene(gi);
      const n = e.index.length, top = Math.min(n, 30);
      const max = Math.max(...Array.from(e.log2fc.slice(0, top), Math.abs), 0.01);
      const nsig = Array.from(e.nlog10padj).filter((v) => v >= SIGNIFICANT).length;
      box.innerHTML = `<h3>Which perturbations change ${esc(ps.genes[gi])}?</h3>
        <p class="muted">${nsig} of ${n.toLocaleString()} targets change it at FDR < 0.1 · strongest ${top} shown · click a target</p>
        <div class="btns">${linkButtons(ps.genes[gi])}</div>
        <table class="table cx-bars"><tbody>${Array.from({ length: top }, (_, k) => {
          const t = T[e.index[k]], v = e.log2fc[k], sigk = e.nlog10padj[k] >= SIGNIFICANT, w = (Math.abs(v) / max) * 50;
          return `<tr data-t="${e.index[k]}"><td><b>${esc(t.name)}</b></td>
            <td class="bar"><span style="${v < 0 ? `right:50%;width:${w}%` : `left:50%;width:${w}%`};background:${sigk ? (v < 0 ? C.down : C.up) : C.ns}"></span></td>
            <td>${lfc(v)}</td><td>${p10(e.nlog10padj[k])}</td></tr>`;
        }).join('')}</tbody></table>`;
      box.querySelector('tbody')!.addEventListener('click', (ev) => {
        const ti = (ev.target as HTMLElement).closest<HTMLElement>('tr[data-t]')?.dataset.t;
        if (ti) selectTarget(+ti);
      });
    }

    function selectTarget(i: number) {
      current = i;
      setUrl('t', T[i].name);
      renderList();
      renderTarget();
    }

    $('table').onclick = (e) => { const tr = (e.target as HTMLElement).closest<HTMLElement>('tr[data-i]'); if (tr) selectTarget(+tr.dataset.i!); };
    $<HTMLInputElement>('q').oninput = renderList;
    $<HTMLInputElement>('sig').onchange = renderList;
    $<HTMLInputElement>('find').addEventListener('keydown', (e) => { if (e.key === 'Enter') lookupGene((e.target as HTMLInputElement).value); });
    renderList();
    await renderTarget();
    if (params.get('g')) lookupGene(params.get('g')!);
  }

  // ============================================================= start

  async function start() {
    const back = document.querySelector<HTMLAnchorElement>('.cx-head .back')!;
    back.href = '/crispr/';
    back.textContent = '← Your CRISPR screens';
    if (!screenId) {  // /crispr/: the data management page
      $('cx').hidden = true;
      const m = document.querySelector<HTMLElement>('[data-manager]')!;
      m.hidden = false;
      mountManager(m, 'crispr');
      return;
    }
    await loadWorkspace();
    listProjects('singlecell').then((p) => (singleCell = p)).catch(() => {});
    let screens: Screen[];
    try { screens = await listScreens(screenId); } catch (e) { fail(esc((e as Error).message)); return; }
    if (!screens.some((s) => s.id === screenId)) { fail(`There is no CRISPR screen “${esc(screenId)}”.<br><a class="btn btn--primary btn--sm" href="/crispr/">Your CRISPR screens</a>`); return; }
    const order = ['enhancer', 'perturbseq'];
    screens.sort((a, b) => order.indexOf(a.kind) - order.indexOf(b.kind) || a.title.localeCompare(b.title));
    screen = screens.find((s) => s.id === screenId)!;
    $('screen').innerHTML = [['enhancer', 'Enhancer screens'], ['perturbseq', 'Perturb-seq (TSS CRISPRi)']].map(([k, label]) =>
      `<optgroup label="${label}">${screens.filter((s) => s.kind === k).map((s) => `<option value="${esc(s.id)}" ${s === screen ? 'selected' : ''}>${esc(s.title)}${s.mine === false && s.ownerName ? ` · shared by ${esc(s.ownerName)}` : ''}</option>`).join('')}</optgroup>`).join('');
    $<HTMLSelectElement>('screen').onchange = (e) => { location.href = `/crispr/${encodeURIComponent((e.target as HTMLSelectElement).value)}/`; };
    $('facts').textContent = [screen.species, screen.cell, screen.assembly, screen.method].filter(Boolean).join(' · ');
    $('source').innerHTML = `${esc(screen.description ?? '')}${screen.citation ? ` <em>${esc(screen.citation)}</em>.` : ''}
      Data: ${(screen.sources ?? []).map((s) => s.url ? `<a href="${esc(s.url)}" target="_blank" rel="noopener">IGVF ${esc(s.accession)}</a>` : `<span class="mono">${esc(s.path ?? s.accession)}</span>`).join(', ')}`;
    document.title = `${screen.title} · CRISPR explorer · Omics Canvas`;
    $('msg').hidden = true;
    try { await (screen.kind === 'enhancer' ? enhancer() : perturbseq()); } catch (e) { fail(esc((e as Error).message)); }
  }
  start();
