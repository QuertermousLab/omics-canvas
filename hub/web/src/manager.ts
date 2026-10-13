// Data management for one viewer (spatial, single-cell or CRISPR), shown at /spatial/, /single-cell/, /crispr/
// when no dataset is open: the workspace's datasets, its conversions with live progress, and the /nfs file
// browser with format detection and conversion options. Datasets belong to the Genome Canvas workspace
// that added them; other workspaces neither see nor change them (their links still open).
import { api, chooseWorkspace, esc, fmtAgo, fmtBytes, fmtDuration, loadWorkspace, onWorkspace, toast, workspace } from './common';

export type Kind = 'spatial' | 'singlecell' | 'crispr';
interface Dataset {
  id: string; kind: Kind; title: string; root: string; source: string; layout: string; url: string; publicUrl: string;
  slides: { id: string; title: string; fromDataset?: string; fromSlide?: string; fromTitle?: string; nCells?: number }[];
  added: number; nCells?: number; nGenes?: number; technology?: string; species?: string;
  bytes?: number; screenKind?: string; stats?: Record<string, number>;
  /** made of samples of other datasets (nothing copied) */
  combined?: boolean; description?: string;
}
/** one sample of a combined dataset: a slide of one of the workspace's datasets */
interface Pick { dataset: string; slide: string; title: string }
interface Job {
  id: string; datasetId: string; title: string; source: string; out: string; type: string; kind?: Kind; status: string;
  progress: number; message: string; created: number; started?: number; finished?: number; url?: string; log?: string[];
  /** long steps, resuming (converter "@@notice" lines) */
  notices?: string[];
  /** times the job continued from its checkpoints after a restart */
  resumes?: number;
}
interface Entry { name: string; path: string; dir: boolean; size: number | null; mtime: number; hint: string | null; converted?: boolean }
interface Listing { path: string; parent: string | null; entries: Entry[] }
interface Info {
  path: string; type: string; ready: boolean; kind: Kind | null; title: string; size?: number;
  details: Record<string, any>; output?: { path: string; exists: boolean; converted: boolean; writable: boolean; manifest: Record<string, any> | null };
  registered: Dataset[];
}

const PAGE: Record<Kind, { path: string; name: string; noun: string; lede: string }> = {
  spatial: { path: '/spatial/', name: 'Spatial viewer', noun: 'spatial datasets',
    lede: 'Tissue sections — Xenium, Visium / Visium HD, Slide-seq and other data with cell or spot coordinates.' },
  singlecell: { path: '/single-cell/', name: 'Single-cell viewer', noun: 'single-cell datasets',
    lede: 'scRNA-seq, snRNA-seq and multiome data shown on UMAP or another embedding.' },
  crispr: { path: '/crispr/', name: 'CRISPR explorer', noun: 'CRISPR screens',
    lede: 'Enhancer screens and Perturb-seq results (SCEPTRE, IGVF or any target / gene / log2FC / p-value table).' },
};
const FORMATS: Record<Kind, string[]> = {
  spatial: ['<b>.h5ad</b> with <span class="mono">obsm["spatial"]</span> (Visium, Slide-seq…) — the H&amp;E from <span class="mono">uns["spatial"]</span>', '<b>.rds</b> Seurat v3–v5 with images (Visium, Visium HD, Xenium / FOV) or SpatialExperiment, with the tissue image — spatial ATAC (Signac): peaks, gene activity, chromVAR and accessibility tracks too',
    '<b>Space Ranger output folder</b> (Visium / Visium HD bin folder: <span class="mono">spatial/</span> + matrix) — with the H&amp;E image',
    '<b>Xenium output folder</b> (experiment.xenium) — morphology image, cell outlines, post-Xenium H&amp;E', '<b>Viewer folders</b> (slide.json, project.json, ATHENA <span class="mono">spatial/data/view</span>)'],
  singlecell: ['<b>.h5ad</b> AnnData', '<b>.rds</b> Seurat v3–v5 or SingleCellExperiment — multiome / Signac scATAC: every assay (RNA, peaks, gene activity, chromVAR + motifs), accessibility tracks from the fragment files',
    '<b>.h5mu</b> MuData multiome (rna, atac, … modalities)', '<b>.h5</b> 10x matrix, <b>.loom</b> — UMAP and clusters are computed',
    '<b>Viewer folders</b> (slide.json, project.json, ATHENA <span class="mono">singlecell/data/view</span>)'],
  crispr: ['<b>.tsv / .csv / .parquet</b> results: SCEPTRE, IGVF, or target / gene / log2FC / p value columns', '<b>.rds</b> sceptre_object (discovery results) or a results data.frame',
    '<b>Screen folders</b> (project.json + tests.json / targets.json, ATHENA <span class="mono">crispr/data/view</span>)'],
};
const HINTS: Record<Kind, Set<string>> = {
  spatial: new Set(['h5ad', 'rds', 'xenium', 'visium', 'viewer', 'project', 'output']),
  singlecell: new Set(['h5ad', 'h5mu', 'rds', 'h5', 'loom', 'viewer', 'project', 'output']),
  crispr: new Set(['table', 'rds', 'project', 'output']),
};
const HINT_LABEL: Record<string, string> = { h5ad: 'h5ad', h5mu: 'h5mu', rds: 'rds', h5: '10x h5', loom: 'loom', table: 'table', xenium: 'Xenium', visium: 'Visium', viewer: 'viewer', project: 'project', output: 'converted' };
const KIND_OF_PATH: Record<Kind, string> = { spatial: 'spatial', singlecell: 'single-cell', crispr: 'crispr' };
const TYPE_LABEL: Record<string, string> = {
  'viewer-slide': 'Viewer dataset · ready', 'viewer-project': 'Viewer project · ready', 'crispr-screen': 'CRISPR screen · ready',
  'viewer-collection': 'Folder of datasets · ready', h5ad: 'AnnData (.h5ad)', h5mu: 'MuData multiome (.h5mu)', 'h5-10x': '10x matrix (.h5)', loom: 'Loom file',
  rds: 'R object (.rds)', xenium: 'Xenium output bundle', visium: 'Space Ranger output (Visium)', table: 'Results table', unsupported: 'Not supported',
};

const other = (k: Kind, path?: string) => `<a href="${PAGE[k].path}${path ? `?path=${encodeURIComponent(path)}#add` : ''}">${PAGE[k].name}</a>`;

export function mountManager(root: HTMLElement, kind: Kind) {
  const P = PAGE[kind];
  let datasets: Dataset[] = [];
  let jobs: Job[] = [];
  let listing: Listing | null = null;
  let selected: string | null = null;
  let polling = 0;
  const openLogs = new Set<string>();
  const params = new URLSearchParams(location.search);

  root.innerHTML = `<div class="mgr">
    <header class="mgr__head">
      <div><span class="eyebrow">${P.name}</span><h1>Your ${P.noun}</h1><p class="muted">${P.lede}</p></div>
      <p class="mgr__ws" data-m-ws></p>
    </header>
    <div data-m-gate hidden class="card mgr__gate">
      <h3>Choose your workspace</h3>
      <p class="muted">Datasets are kept per workspace — the same workspaces as Genome Canvas (one per user). Only you see, rename and remove what you add; dataset links still open for anyone you send them to.</p>
      <button type="button" class="btn btn--primary" data-m-choose>Choose workspace</button>
    </div>
    <div data-m-body hidden>
      <section class="card mgr__list">
        <div class="mgr__bar"><h2>Datasets</h2>
          ${kind !== 'crispr' ? '<button type="button" class="btn btn--light btn--sm" data-m-combine title="One dataset made of samples of your datasets — time points, conditions, sections"><svg class="icon"><use href="#i-layers"/></svg>Combine datasets</button>' : ''}
          <input class="input" type="search" placeholder="Filter" data-m-q aria-label="Filter datasets" /></div>
        <div class="table-wrap"><table class="table ds" data-m-table></table></div>
      </section>
      <dialog class="ws-dialog" data-m-cdialog>
        <form method="dialog" class="ws-card combine" data-m-cform>
          <div class="ws-head"><div><b data-m-ctitle>Combine datasets</b><small>One link with a sample picker, like a time course or several sections. The samples stay where they are; nothing is copied.</small></div></div>
          <label class="lbl">Name <input class="input" name="title" required maxlength="300" placeholder="e.g. Mouse SMC atherosclerosis time course" /></label>
          <label class="lbl">Description <textarea class="input" name="description" rows="2" maxlength="2000" placeholder="optional: what the samples are"></textarea></label>
          <div><span class="eyebrow">Samples, in this order</span><ol class="combine__picked" data-m-picked></ol></div>
          <div><span class="eyebrow">Add samples from your ${P.noun}</span><input class="input" type="search" placeholder="Filter" data-m-cq aria-label="Filter samples" />
            <ul class="combine__pool" data-m-pool></ul></div>
          <p class="err ws-err" data-m-cerr hidden></p>
          <div class="ws-foot"><button type="button" class="btn btn--quiet" data-m-ccancel>Cancel</button><button type="submit" class="btn btn--primary" data-m-csave>Create</button></div>
        </form>
      </dialog>
      <section class="mgr__jobs" data-m-jobs-block hidden>
        <div class="mgr__bar"><h2>Conversions</h2><span class="muted small">Run on THOR — you can close this page. At most 8 run at once (the rest queue); an interrupted one continues from its last finished step.</span>
          <button type="button" class="btn btn--quiet btn--sm mgr__right" data-m-clear>Clear finished</button></div>
        <ul class="jobs" data-m-jobs></ul>
      </section>
      <section class="mgr__add" id="add">
        <div class="mgr__bar"><h2>Add data</h2><span class="muted small">Pick a file or folder on <span class="mono">/nfs</span>. Converted files are written to <span class="mono">&lt;file&gt;.vizhub/</span> next to it.</span></div>
        <div class="add">
          <div class="card browser">
            <form class="pathbar" data-m-pathform>
              <button type="button" class="icon-btn" data-m-up title="Parent folder" aria-label="Parent folder"><svg class="icon"><use href="#i-up"/></svg></button>
              <input class="input mono" type="text" data-m-path spellcheck="false" autocomplete="off" aria-label="Path" placeholder="/nfs/baldar/…" />
              <button type="submit" class="btn btn--light">Go</button>
            </form>
            <div class="crumbs" data-m-crumbs></div>
            <div class="browser__tools">
              <label class="check"><input type="checkbox" data-m-only checked /> Only files for the ${P.name.toLowerCase()}</label>
              <label class="check"><input type="checkbox" data-m-hidden /> Hidden files</label>
              <span class="muted" data-m-count></span>
            </div>
            <ul class="files" data-m-files><li class="muted pad">Loading…</li></ul>
          </div>
          <div class="card inspector" data-m-inspector>
            <h3>Select a file or folder</h3>
            <p class="muted">The ${P.name.toLowerCase()} reads:</p>
            <ul class="formats">${FORMATS[kind].map((f) => `<li>${f}</li>`).join('')}</ul>
          </div>
        </div>
      </section>
    </div>
  </div>`;
  const $ = <T extends HTMLElement = HTMLElement>(s: string) => root.querySelector<T>(`[data-m-${s}]`)!;

  // ---------------------------------------------------------------- datasets
  const facts = (d: Dataset) => {
    if (d.kind === 'crispr') {
      const s = d.stats ?? {};
      return [d.screenKind === 'enhancer' ? 'Enhancer screen' : 'Perturb-seq', s.targets != null ? `${s.targets.toLocaleString()} targets` : '',
        s.tests != null ? `${s.tests.toLocaleString()} tests` : '', s.genes != null ? `${s.genes.toLocaleString()} readout genes` : '',
        s.significant != null ? `${s.significant.toLocaleString()} significant` : '', d.species ?? ''].filter(Boolean).join(' · ');
    }
    return [d.combined ? `combined: ${d.slides.length} sample${d.slides.length === 1 ? '' : 's'}` : '',
      d.nCells ? `${d.nCells.toLocaleString()} cells` : '', d.nGenes ? `${d.nGenes.toLocaleString()} genes` : '',
      d.slides.length > 1 && !d.combined ? `${d.slides.length} slides` : '', d.technology ?? '', d.species ?? '', d.bytes && d.layout !== 'project' ? fmtBytes(d.bytes) : '']
      .filter(Boolean).join(' · ');
  };

  function renderDatasets() {
    const q = $<HTMLInputElement>('q').value.trim().toLowerCase();
    const shown = datasets.filter((d) => !q || `${d.title} ${d.id} ${d.source} ${d.technology ?? ''} ${d.species ?? ''}`.toLowerCase().includes(q));
    if (!datasets.length) {
      $('table').innerHTML = `<tbody><tr><td class="empty">No ${P.noun} in your workspace yet — <a href="#add">add one below</a>.</td></tr></tbody>`;
      return;
    }
    $('table').innerHTML = `<thead><tr><th>Dataset</th><th>Link</th><th>Source</th><th></th></tr></thead><tbody>${shown.map((d) => `
      <tr data-id="${esc(d.id)}">
        <td class="ds__title"><a href="${esc(d.url)}">${esc(d.title)}</a><div class="small muted">${esc(facts(d))}</div></td>
        <td class="nowrap"><a class="mono small" href="${esc(d.url)}">${esc(d.url)}</a>
          <button type="button" class="icon-btn sm ghost" data-act="copy" title="Copy the full link" aria-label="Copy link"><svg class="icon"><use href="#i-copy"/></svg></button></td>
        <td class="ds__src"><span class="mono small" title="${esc(d.source)}">${esc(d.source)}</span><div class="small muted">added ${fmtAgo(d.added)}</div></td>
        <td class="nowrap ds__acts"><a class="btn btn--primary btn--sm" href="${esc(d.url)}">Open</a>
          ${d.combined ? '<button type="button" class="btn btn--quiet btn--sm" data-act="samples">Edit samples</button>' : ''}
          <button type="button" class="btn btn--quiet btn--sm" data-act="rename">Rename</button>
          <button type="button" class="btn btn--quiet btn--sm" data-act="remove">Remove</button></td>
      </tr>`).join('') || '<tr><td colspan="4" class="empty">Nothing matches the filter.</td></tr>'}</tbody>`;
  }

  async function loadDatasets() {
    datasets = await api<Dataset[]>(`/hub/api/datasets?kind=${kind}`);
    renderDatasets();
  }

  $('table').addEventListener('click', async (e) => {
    const btn = (e.target as HTMLElement).closest<HTMLElement>('[data-act]');
    if (!btn) return;
    const d = datasets.find((x) => x.id === btn.closest<HTMLElement>('tr')!.dataset.id)!;
    if (btn.dataset.act === 'copy') {
      navigator.clipboard?.writeText(d.publicUrl).then(() => toast(`Copied <span class="mono">${esc(d.publicUrl)}</span>`, 2500)).catch(() => prompt('Copy:', d.publicUrl));
    }
    if (btn.dataset.act === 'rename') {
      const title = prompt('Dataset name', d.title)?.trim();
      if (title && title !== d.title) { await api(`/hub/api/datasets/${d.id}`, { method: 'PATCH', body: { title } }).catch((err) => toast(esc(err.message))); loadDatasets(); }
    }
    if (btn.dataset.act === 'samples') openCombine(d);
    if (btn.dataset.act === 'remove') {
      const usedBy = datasets.filter((x) => x.combined && x.id !== d.id && x.slides.some((sl) => sl.fromDataset === d.id));
      if (!confirm(`Remove “${d.title}” from your workspace?\n\nIts link ${d.url} stops working. Source data is never deleted.${d.combined ? '\nThe datasets it combines are kept.' : ''}${usedBy.length ? `\nIts files stay for ${usedBy.map((x) => `“${x.title}”`).join(', ')}, which combines it.` : ''}`)) return;
      const generated = !d.combined && (/\.vizhub\/?$/.test(d.root) || d.root.includes('/vizhub/state/converted/'));
      const files = generated && confirm(`Also delete the converted viewer files?\n\n${d.root}\n\nOK = delete them · Cancel = keep them (adding the source again then needs no conversion).`);
      try {
        const r = await api<{ deletedFolder: string | null; keptFor: string[] }>(`/hub/api/datasets/${d.id}${files ? '?files=1' : ''}`, { method: 'DELETE' });
        toast(`Removed ${esc(d.title)}${r.deletedFolder ? ' and its converted files' : ''}${files && r.keptFor.length ? ` · files kept: also used by ${esc(r.keptFor.join(', '))}` : ''}`);
      } catch (err) { toast(esc((err as Error).message)); }
      loadDatasets();
      if (selected) inspect(selected);
    }
  });
  $<HTMLInputElement>('q').oninput = renderDatasets;

  // ---------------------------------------------------------------- combined datasets
  let editing: Dataset | null = null;
  let picks: Pick[] = [];
  const cdlg = () => $<HTMLDialogElement>('cdialog');
  const cform = () => $<HTMLFormElement>('cform');
  const sampleName = (d: Dataset, sl: Dataset['slides'][number]) => (d.slides.length > 1 ? `${d.title} · ${sl.title}` : d.title);

  function openCombine(d: Dataset | null) {
    editing = d;
    picks = d ? d.slides.map((sl) => ({ dataset: sl.fromDataset!, slide: sl.fromSlide!, title: sl.title })) : [];
    const f = cform();
    f.reset();
    (f.elements.namedItem('title') as HTMLInputElement).value = d?.title ?? '';
    (f.elements.namedItem('description') as HTMLTextAreaElement).value = d?.description ?? '';
    $('ctitle').textContent = d ? `Samples of “${d.title}”` : 'Combine datasets';
    $('csave').textContent = d ? 'Save' : 'Create';
    $('cerr').hidden = true;
    renderCombine();
    cdlg().showModal();
  }

  function renderCombine() {
    const byId = new Map(datasets.map((d) => [d.id, d]));
    $('picked').innerHTML = picks.length ? picks.map((p, i) => {
      const d = byId.get(p.dataset), sl = d?.slides.find((x) => x.id === p.slide);
      return `<li data-i="${i}"><input class="input" value="${esc(p.title)}" data-k="title" aria-label="Sample name" />
        <span class="small muted combine__from" title="${esc(d ? sampleName(d, sl!) : p.dataset)}">${esc(d ? sampleName(d, sl!) : `${p.dataset} (removed)`)}</span>
        <button type="button" class="icon-btn sm ghost" data-k="up" title="Move up" aria-label="Move up" ${i ? '' : 'disabled'}><svg class="icon"><use href="#i-up"/></svg></button>
        <button type="button" class="icon-btn sm ghost combine__down" data-k="down" title="Move down" aria-label="Move down" ${i < picks.length - 1 ? '' : 'disabled'}><svg class="icon"><use href="#i-up"/></svg></button>
        <button type="button" class="btn btn--quiet btn--xs" data-k="drop">Remove</button></li>`;
    }).join('') : '<li class="muted small">Add two or more samples below.</li>';
    const q = $<HTMLInputElement>('cq').value.trim().toLowerCase();
    const chosen = new Set(picks.map((p) => `${p.dataset}/${p.slide}`));
    const pool = datasets.filter((d) => !d.combined && d.id !== editing?.id)
      .flatMap((d) => d.slides.map((sl) => ({ d, sl })))
      .filter(({ d, sl }) => !q || `${d.title} ${sl.title} ${d.technology ?? ''}`.toLowerCase().includes(q));
    $('pool').innerHTML = pool.map(({ d, sl }) => {
      const on = chosen.has(`${d.id}/${sl.id}`);
      return `<li><button type="button" data-ds="${esc(d.id)}" data-sl="${esc(sl.id)}" ${on ? 'disabled' : ''}>
        <svg class="icon"><use href="#i-plus"/></svg><span>${esc(sampleName(d, sl))}</span>
        <small>${esc([sl.nCells ?? (d.slides.length === 1 ? d.nCells : null) ? `${(sl.nCells ?? d.nCells)!.toLocaleString()} cells` : '', d.technology ?? ''].filter(Boolean).join(' · '))}${on ? ' · added' : ''}</small></button></li>`;
    }).join('') || `<li class="muted small">${datasets.some((d) => !d.combined) ? 'Nothing matches the filter.' : `Add ${P.noun} first.`}</li>`;
    $<HTMLButtonElement>('csave').disabled = picks.length < (editing ? 1 : 2);
  }

  $('cq').addEventListener('input', renderCombine);
  $('pool').addEventListener('click', (e) => {
    const b = (e.target as HTMLElement).closest<HTMLButtonElement>('button[data-ds]');
    if (!b) return;
    const d = datasets.find((x) => x.id === b.dataset.ds)!, sl = d.slides.find((x) => x.id === b.dataset.sl)!;
    picks.push({ dataset: d.id, slide: sl.id, title: d.slides.length > 1 ? sl.title : d.title });
    renderCombine();
  });
  $('picked').addEventListener('input', (e) => {
    const el = e.target as HTMLInputElement, li = el.closest<HTMLElement>('[data-i]');
    if (li && el.dataset.k === 'title') picks[+li.dataset.i!].title = el.value;
  });
  $('picked').addEventListener('click', (e) => {
    const b = (e.target as HTMLElement).closest<HTMLElement>('button[data-k]'), li = b?.closest<HTMLElement>('[data-i]');
    if (!b || !li) return;
    const i = +li.dataset.i!;
    if (b.dataset.k === 'drop') picks.splice(i, 1);
    if (b.dataset.k === 'up' && i > 0) [picks[i - 1], picks[i]] = [picks[i], picks[i - 1]];
    if (b.dataset.k === 'down' && i < picks.length - 1) [picks[i + 1], picks[i]] = [picks[i], picks[i + 1]];
    renderCombine();
  });
  $('ccancel').onclick = () => cdlg().close();
  $('combine')?.addEventListener('click', () => openCombine(null));
  $('cform').addEventListener('submit', async (e) => {
    e.preventDefault();
    const f = new FormData(cform());
    const body = { kind, title: String(f.get('title') ?? '').trim(), description: String(f.get('description') ?? '').trim(), slides: picks };
    try {
      const d = await api<Dataset>(editing ? `/hub/api/datasets/${editing.id}` : '/hub/api/combined', { method: editing ? 'PATCH' : 'POST', body });
      cdlg().close();
      toast(`${editing ? 'Saved' : 'Created'} <a href="${esc(d.url)}">${esc(d.title)}</a> · ${d.slides.length} samples`, 6000);
      loadDatasets();
    } catch (err) {
      $('cerr').textContent = (err as Error).message;
      $('cerr').hidden = false;
    }
  });

  // ---------------------------------------------------------------- jobs
  function renderJobs() {
    $('jobs-block').hidden = !jobs.length;
    const now = Date.now() / 1000;
    $('jobs').innerHTML = jobs.map((j) => {
      const active = j.status === 'running' || j.status === 'queued';
      const took = j.started ? fmtDuration((j.finished ?? now) - j.started) : '';
      return `<li class="card job job--${j.status}" data-job="${j.id}">
        <div class="job__head"><b>${esc(j.title)}</b><span class="chip job__state">${esc(j.status)}</span>
          ${j.resumes ? `<span class="chip" title="Continued from its checkpoints after a restart">resumed${j.resumes > 1 ? ` ×${j.resumes}` : ''}</span>` : ''}
          <span class="mono small muted job__src" title="${esc(j.source)}">${esc(j.source)}</span></div>
        ${active ? (j.notices ?? []).map((n) => `<div class="notice small job__notice">${esc(n)}</div>`).join('') : ''}
        <div class="bar"><span style="width:${j.status === 'done' ? 100 : j.progress}%"></span></div>
        <div class="job__msg"><span>${esc(j.message)}</span><span class="muted">${active ? `${j.progress}% · ` : ''}${took}</span></div>
        <div class="job__acts">
          ${j.status === 'done' && j.url ? `<a class="btn btn--primary btn--sm" href="${esc(j.url)}">Open</a>` : ''}
          ${active ? '<button type="button" class="btn btn--light btn--sm" data-cancel>Cancel</button>' : ''}
          <button type="button" class="btn btn--quiet btn--sm" data-log>${openLogs.has(j.id) ? 'Hide log' : 'Show log'}</button>
          <span class="small muted">output <span class="mono">${esc(j.out)}</span></span>
        </div>
        ${openLogs.has(j.id) ? `<pre class="log" data-logtext>${esc((j.log ?? []).join('\n')) || '…'}</pre>` : ''}
      </li>`;
    }).join('');
    root.querySelectorAll<HTMLPreElement>('[data-logtext]').forEach((p) => (p.scrollTop = p.scrollHeight));
  }

  async function loadJobs() {
    if (!workspace()) return;
    const before = new Map(jobs.map((j) => [j.id, j.status]));
    const list = await api<Job[]>(`/hub/api/jobs?kind=${kind}`);
    await Promise.all(list.filter((j) => openLogs.has(j.id)).map(async (j) => { j.log = (await api<Job>(`/hub/api/jobs/${j.id}?log=80`)).log; }));
    jobs = list;
    renderJobs();
    const finished = jobs.filter((j) => before.get(j.id) && before.get(j.id) !== j.status && !['queued', 'running'].includes(j.status));
    for (const j of finished) {
      if (j.status === 'done') toast(`<b>${esc(j.title)}</b> is ready · <a href="${esc(j.url!)}">open it</a>`, 10000);
      else if (j.status === 'failed') toast(`<b>${esc(j.title)}</b> failed: ${esc(j.message)}`, 10000);
    }
    if (finished.length) { loadDatasets(); if (selected) inspect(selected); }
    const active = jobs.some((j) => j.status === 'running' || j.status === 'queued');
    clearTimeout(polling);
    polling = window.setTimeout(() => loadJobs().catch(() => {}), active ? 1500 : 15000);
  }

  $('jobs').addEventListener('click', async (e) => {
    const t = e.target as HTMLElement, li = t.closest<HTMLElement>('[data-job]');
    if (!li) return;
    const id = li.dataset.job!;
    if (t.closest('[data-cancel]') && confirm('Stop this conversion?')) { await api(`/hub/api/jobs/${id}/cancel`, { method: 'POST' }).catch((err) => toast(esc(err.message))); loadJobs(); }
    if (t.closest('[data-log]')) { openLogs.has(id) ? openLogs.delete(id) : openLogs.add(id); loadJobs(); }
  });
  $('clear').onclick = async () => {
    await Promise.all(jobs.filter((j) => !['queued', 'running'].includes(j.status)).map((j) => api(`/hub/api/jobs/${j.id}`, { method: 'DELETE' })));
    loadJobs();
  };

  // ---------------------------------------------------------------- file browser
  function renderBrowser() {
    if (!listing) return;
    const l = listing;
    $<HTMLInputElement>('path').value = l.path;
    const parts = l.path.split('/').filter(Boolean);
    $('crumbs').innerHTML = l.path ? parts.map((p, i) => `<button type="button" class="crumb" data-go="/${esc(parts.slice(0, i + 1).join('/'))}">${esc(p)}</button>`).join('<span>/</span>')
      + ` <button type="button" class="btn btn--light btn--xs" data-pick="${esc(l.path)}" title="Inspect this folder (Xenium bundle or viewer folder)">Use this folder</button>` : '';
    const only = $<HTMLInputElement>('only').checked;
    const shown = l.entries.filter((e) => e.dir || !only || (e.hint && HINTS[kind].has(e.hint)));
    $('count').textContent = `${shown.length} of ${l.entries.length}`;
    $('files').innerHTML = shown.map((e) => `<li class="${e.path === selected ? 'on' : ''}">
        <button type="button" class="file ${e.dir ? 'file--dir' : ''}" data-${e.dir ? 'go' : 'pick'}="${esc(e.path)}">
          <svg class="icon file__icon"><use href="#i-${e.dir ? 'folder' : 'file'}"/></svg><span class="file__name">${esc(e.name)}</span>
          ${e.hint ? `<span class="badge badge--${esc(e.hint)}">${esc(HINT_LABEL[e.hint] ?? e.hint)}</span>` : ''}
          ${e.converted ? '<span class="badge badge--output" title="A converted copy exists next to it">converted</span>' : ''}
          <span class="file__meta">${e.dir ? '' : fmtBytes(e.size)}</span>
        </button>
        ${e.dir && e.hint && e.hint !== 'output' ? `<button type="button" class="btn btn--light btn--xs" data-pick="${esc(e.path)}">Select</button>` : ''}
      </li>`).join('') || '<li class="muted pad">Empty folder</li>';
  }

  async function browse(path: string) {
    try {
      listing = await api<Listing>(`/hub/api/browse?path=${encodeURIComponent(path)}&hidden=${$<HTMLInputElement>('hidden').checked}`);
      try { localStorage.setItem('vizhub-path', listing.path); } catch { /* private window */ }
      renderBrowser();
    } catch (e) { toast(esc((e as Error).message)); }
  }

  const onPick = (e: Event) => {
    const t = e.target as HTMLElement;
    const go = t.closest<HTMLElement>('[data-go]'), pick = t.closest<HTMLElement>('[data-pick]');
    if (pick) inspect(pick.dataset.pick!);
    else if (go) browse(go.dataset.go!);
  };
  $('files').addEventListener('click', onPick);
  $('crumbs').addEventListener('click', onPick);
  $('up').onclick = () => listing?.parent && browse(listing.parent);
  $('pathform').addEventListener('submit', async (e) => {
    e.preventDefault();
    const p = $<HTMLInputElement>('path').value.trim().replace(/\/+$/, '') || '/nfs';
    const l = await api<Listing>(`/hub/api/browse?path=${encodeURIComponent(p)}`).catch((err) => { toast(esc(err.message)); return null; });
    if (!l) return;
    listing = l;
    renderBrowser();
    if (l.path !== p) inspect(p);
  });
  $<HTMLInputElement>('only').onchange = renderBrowser;
  $<HTMLInputElement>('hidden').onchange = () => listing && browse(listing.path);

  // ---------------------------------------------------------------- inspector
  /** Why this item belongs to another viewer, or null when this viewer can take it. */
  function elsewhere(info: Info): string | null {
    const t = info.type, d = info.details ?? {};
    if (t === 'unsupported' || t === 'viewer-collection' || t === 'rds') return null;
    if (info.ready && info.kind && info.kind !== kind) return `This is a ${PAGE[info.kind].noun.replace(/s$/, '')} — load it in the ${other(info.kind, info.path)}.`;
    if (t === 'table' && kind !== 'crispr') return `CRISPR results load in the ${other('crispr', info.path)}.`;
    if (kind === 'crispr' && ['h5ad', 'h5mu', 'h5-10x', 'loom', 'xenium', 'visium'].includes(t)) return `This holds cells, not screen results: use the ${other(info.kind === 'spatial' ? 'spatial' : 'singlecell', info.path)}.`;
    if (kind === 'singlecell' && t === 'xenium') return `Xenium bundles load in the ${other('spatial', info.path)}.`;
    if (kind === 'singlecell' && t === 'visium') return `Space Ranger outputs load in the ${other('spatial', info.path)}.`;
    if (kind === 'spatial' && (t === 'h5-10x' || t === 'loom' || t === 'h5mu' || (t === 'h5ad' && !d.spatialKey && !d.error))) return `No tissue coordinates (<span class="mono">obsm["spatial"]</span>) in this file: use the ${other('singlecell', info.path)}.`;
    return null;
  }

  function detailRows(info: Info): [string, string][] {
    const d = info.details ?? {};
    const rows: [string, string][] = [];
    if (info.size != null) rows.push(['Size', fmtBytes(info.size)]);
    if (d.error) rows.push(['Note', esc(d.error)]);
    if (info.type === 'h5ad') {
      if (d.nCells != null) rows.push(['Cells × genes', `${d.nCells.toLocaleString()} × ${d.nGenes?.toLocaleString() ?? '?'}`]);
      rows.push(['Expression from', esc(d.countsSource ?? '')]);
      rows.push(['Coordinates', d.spatialKey ? `<span class="mono">obsm["${esc(d.spatialKey)}"]</span>${d.visium ? ' · Visium scale factors' : ''}` : 'none']);
      rows.push(['Embeddings', d.embeddings?.length ? d.embeddings.map((k: string) => `<span class="mono">${esc(k)}</span>`).join(', ') : kind === 'spatial' ? 'none (optional UMAP under Advanced)' : 'none — a UMAP is computed']);
      rows.push(['Annotations', `${d.categorical?.length ?? 0} categorical, ${d.numeric?.length ?? 0} numeric`]);
    } else if (info.type === 'xenium') {
      rows.push(['Panel', esc(d.panel ?? '—')], ['Cells', d.cells?.toLocaleString() ?? '—'], ['Run', esc(d.run ?? '—')]);
      rows.push(['Tissue image', 'morphology channels; a post-Xenium H&E with its Xenium Explorer alignment (*he_imagealignment.csv) in the folder is added']);
    } else if (info.type === 'visium') {
      rows.push(['Matrix', `<span class="mono">${esc(String(d.matrix).split('/').pop() ?? '')}</span>`]);
      rows.push(['Positions', `<span class="mono">spatial/${esc(String(d.positions).split('/').pop() ?? '')}</span>`]);
      rows.push(['Tissue image', d.images?.length ? `<span class="mono">${d.images.map(esc).join(', ')}</span> (the high-resolution one is shown)` : 'none']);
    } else if (info.type === 'table') {
      const c = d.crisprColumns ?? {};
      rows.push(['Columns', Object.entries(c).filter(([, v]) => v).map(([k, v]) => `${k}: <span class="mono">${esc(v)}</span>`).join(' · ')]);
      rows.push(['Screen type', d.screenKind === 'enhancer' ? 'enhancer (element → gene tests)' : 'Perturb-seq (targets × transcriptome)']);
    } else if (info.type === 'rds') {
      rows.push(['', kind === 'crispr' ? 'A sceptre_object or a results data.frame; read with R when converting.' : 'A Seurat (v3–v5) or SingleCellExperiment object; read with R when converting.']);
    } else if (info.type === 'h5-10x' || info.type === 'h5mu') {
      rows.push(['', esc(d.note ?? '')]);
    } else if (info.type === 'viewer-slide') {
      rows.push(['Cells', d.nCells?.toLocaleString() ?? '—'], ['Genes', d.nGenes?.toLocaleString() ?? '—'], ['Technology', esc(d.technology ?? '—')]);
    } else if (info.type === 'viewer-project') {
      rows.push(['Slides', d.slides.map((s: any) => esc(s.title)).join(', ')]);
    } else if (info.type === 'crispr-screen') {
      rows.push(['Screen', d.screenKind === 'enhancer' ? 'enhancer' : 'Perturb-seq'], ['Species', esc(d.species ?? '—')]);
    } else if (info.type === 'unsupported') {
      rows.push(['', esc(d.reason ?? 'Unsupported')]);
    }
    return rows;
  }

  function optionsForm(info: Info) {
    const d = info.details ?? {};
    if (kind === 'crispr') return `
      <label class="lbl">Name <input class="input" name="title" value="${esc(info.title)}" /></label>
      <label class="lbl">Screen type <select class="input" name="screenKind"><option value="auto">detect${d.screenKind ? ` (${d.screenKind})` : ''}</option>
        <option value="enhancer">Enhancer screen (element → gene)</option><option value="perturbseq">Perturb-seq (gene targets × transcriptome)</option></select></label>
      <div class="row2">
        <label class="lbl">Significant at FDR below <input class="input" name="fdr" type="number" step="any" min="0" max="1" value="0.1" /></label>
        <label class="lbl">Species <select class="input" name="species"><option value="">detect</option><option>Human</option><option>Mouse</option></select></label>
      </div>`;
    const cats: { name: string; n: number | null }[] = d.categorical ?? [];
    const layers: string[] = d.layers ?? [];
    return `
      <label class="lbl">Name <input class="input" name="title" value="${esc(info.title)}" /></label>
      ${info.type === 'h5ad' && cats.length ? `<label class="lbl">Annotation shown first <select class="input" name="default"><option value="">automatic</option>${cats.map((c) => `<option value="${esc(c.name)}">${esc(c.name)}${c.n ? ` (${c.n})` : ''}</option>`).join('')}</select></label>`
        : info.type === 'xenium' ? '' : '<label class="lbl">Annotation shown first <input class="input" name="default" placeholder="automatic, e.g. seurat_clusters" /></label>'}
      ${kind === 'singlecell' && info.details?.spatialKey ? `<p class="small muted">Tissue coordinates are ignored here; the ${other('spatial')} shows the tissue.</p>` : ''}
      <details class="adv"><summary>Advanced</summary>
        ${info.type === 'h5ad' ? `<label class="lbl">Expression matrix <select class="input" name="matrix"><option value="">automatic (raw integer counts first)</option><option value="X">X</option>${d.raw ? '<option value="raw">raw.X</option>' : ''}${layers.map((l) => `<option value="${esc(l)}">layers["${esc(l)}"]</option>`).join('')}</select></label>` : ''}
        ${info.type === 'rds' ? '<label class="lbl">Seurat assay <input class="input" name="assay" placeholder="default assay" /></label>' : ''}
        ${kind === 'spatial' && info.type !== 'xenium' ? `<div class="row2">
          <label class="lbl">µm per coordinate unit <input class="input" name="pixelSize" type="number" step="any" min="0" placeholder="auto" /></label>
          <label class="lbl">Spot / cell diameter (µm) <input class="input" name="spotDiameter" type="number" step="any" min="0" placeholder="auto" /></label></div>
          <label class="check"><input type="checkbox" name="computeUmap" /> Also compute a UMAP</label>` : ''}
        ${kind === 'singlecell' ? '<label class="check"><input type="checkbox" name="noUmap" /> Never compute a UMAP (use the embeddings in the file)</label>' : ''}
        ${(info.type === 'rds' || info.type === 'h5mu') ? `<label class="check"><input type="checkbox" name="fragments" checked /> Accessibility tracks from the fragment files (reads every fragment once: slow for large files)</label>
          <label class="lbl">GTF for the gene track <input class="input mono" name="gtf" placeholder="only if the object has no gene annotation" /></label>
          <label class="lbl">RNA assay of an ATAC object <select class="input" name="geneActivity"><option value="auto">detect gene activity (totals follow the peaks)</option>
            <option value="yes">is gene activity (show the tracks with it)</option><option value="no">is real RNA (multiome)</option></select></label>` : ''}
        <label class="lbl">Species <select class="input" name="species"><option value="">detect</option><option>Human</option><option>Mouse</option></select></label>
      </details>`;
  }

  async function inspect(path: string) {
    selected = path;
    renderBrowser();
    const box = $('inspector');
    box.innerHTML = `<p class="muted">Inspecting <span class="mono">${esc(path)}</span>…</p>`;
    let info: Info;
    try { info = await api<Info>(`/hub/api/inspect?path=${encodeURIComponent(path)}`); } catch (e) { box.innerHTML = `<p class="err">${esc((e as Error).message)}</p>`; return; }
    if (selected !== path) return;
    const out = info.output;
    const reg = (info.registered ?? []).filter((d) => d.kind === kind);
    const running = jobs.find((j) => j.source === info.path && (j.status === 'running' || j.status === 'queued'));
    const away = elsewhere(info);
    let action = '';
    if (away) {
      action = `<div class="notice">${away}</div>`;
    } else if (info.type === 'viewer-collection') {
      const all = info.details.items as { path: string; title: string; kind: Kind; registered: boolean }[];
      const items = all.filter((it) => it.kind === kind);
      const others = [...new Set(all.filter((it) => it.kind !== kind).map((it) => it.kind))];
      action = items.length ? `<form data-addform><p class="small muted">Load these datasets (no conversion needed):</p><ul class="picklist">${items.map((it) => `<li><label class="check">
          <input type="checkbox" name="item" value="${esc(it.path)}" ${it.registered ? 'checked disabled' : 'checked'} /><span>${esc(it.title)}</span>
          ${it.registered ? '<span class="small muted">in your workspace</span>' : ''}</label></li>`).join('')}</ul>
          <button class="btn btn--primary" type="submit">Load selected</button></form>` : '';
      if (others.length) action += `<p class="small muted">Also here: ${others.map((k) => `${PAGE[k].noun} (${other(k)})`).join(', ')}.</p>`;
    } else if (info.type === 'unsupported') {
      action = '';
    } else if (info.ready) {
      action = `<form data-addform><label class="lbl">Name <input class="input" name="title" value="${esc(info.title)}" /></label>
        <p class="small muted">Already in the viewer format — loaded as it is, nothing is written.</p>
        <button class="btn btn--primary" type="submit">${reg.length ? 'Update' : 'Load'}</button></form>`;
    } else {
      const where = out?.writable ? `Writes <span class="mono">${esc(out.path)}</span>` : 'The folder is not writable: the converted files go to the hub\'s <span class="mono">state/converted/</span>';
      action = `<form data-addform>${optionsForm(info)}
        ${out?.converted ? `<div class="notice small">Converted ${out.manifest?.finished ? fmtAgo(out.manifest.finished) : 'before'} into <span class="mono">${esc(out.path)}</span>; the source has not changed since.</div>` : ''}
        ${kind !== 'crispr' && (info.type === 'rds' || info.type === 'h5mu') ? `<div class="notice small">Multiome / scATAC / spatial ATAC objects with fragment files take long: building the accessibility tracks reads every fragment once
        (roughly 1 h per 50–100 GB on THOR). The conversion runs on THOR, so you can close this page; if THOR or the hub restarts, it continues from the last finished step.</div>` : ''}
      <p class="small muted">${where}${out?.exists && !out.converted ? ' (an unfinished conversion there continues from its checkpoints; “Convert again” starts over)' : ''}.</p>
        <div class="btns">${out?.converted ? '<button class="btn btn--primary" type="submit" name="reuse">Load the converted files</button><button class="btn btn--light" type="submit" name="reconvert">Convert again</button>'
          : `<button class="btn btn--primary" type="submit" ${running ? 'disabled' : ''}>${running ? 'Converting…' : out?.exists ? 'Continue & load' : 'Convert & load'}</button>
             ${out?.exists && !running ? '<button class="btn btn--light" type="submit" name="reconvert">Start over</button>' : ''}`}</div>
      </form>`;
    }
    box.innerHTML = `<div class="insp">
      <span class="chip ${info.ready && !away ? 'chip--ok' : info.type === 'unsupported' || away ? 'chip--bad' : 'chip--accent'}">${esc(TYPE_LABEL[info.type] ?? info.type)}</span>
      <h3>${esc(info.path.split('/').pop())}</h3><p class="mono small muted path">${esc(info.path)}</p>
      ${(() => { const r = detailRows(info); return r.length ? `<dl class="facts">${r.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join('')}</dl>` : ''; })()}
      ${reg.length ? `<div class="registered"><b>In your workspace:</b> ${reg.map((d) => `<a href="${esc(d.url)}">${esc(d.title)}</a>`).join(' · ')}</div>` : ''}
      ${running ? '<div class="notice small">Converting now — see Conversions above.</div>' : ''}
      ${action}</div>`;
    const form = box.querySelector<HTMLFormElement>('[data-addform]');
    form?.addEventListener('submit', (e) => { e.preventDefault(); submit(info, form, (e as SubmitEvent).submitter as HTMLButtonElement | null); });
  }

  async function submit(info: Info, form: HTMLFormElement, submitter: HTMLButtonElement | null) {
    const f = new FormData(form);
    const val = (k: string) => String(f.get(k) ?? '').trim();
    const body: Record<string, unknown> = { path: info.path, title: val('title') };
    if (info.type === 'viewer-collection') body.items = f.getAll('item');
    else if (!info.ready) {
      body.kind = kind;
      body.reconvert = submitter?.name === 'reconvert';
      body.options = kind === 'crispr' ? { screenKind: val('screenKind'), fdr: val('fdr'), species: val('species') }
        : { default: val('default'), matrix: val('matrix'), assay: val('assay'), pixelSize: val('pixelSize'), spotDiameter: val('spotDiameter'),
            computeUmap: f.has('computeUmap'), noUmap: f.has('noUmap'), species: val('species'),
            noFragments: form.querySelector('[name="fragments"]') ? !f.has('fragments') : false, gtf: val('gtf'), geneActivity: val('geneActivity') };
    }
    form.querySelectorAll('button').forEach((b) => (b.disabled = true));
    try {
      const res = await api<{ datasets?: Dataset[]; job?: Job }>('/hub/api/datasets', { method: 'POST', body });
      if (res.datasets) {
        toast(res.datasets.length ? `Loaded ${res.datasets.map((d) => `<a href="${esc(d.url)}">${esc(d.title)}</a>`).join(', ')}` : 'Nothing new to load');
        await loadDatasets();
      }
      if (res.job) {
        openLogs.add(res.job.id);
        await loadJobs();
        $('jobs-block').scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
    } catch (e) { toast(esc((e as Error).message), 8000); }
    inspect(info.path);
  }

  // ---------------------------------------------------------------- start
  $('choose').onclick = () => chooseWorkspace();
  let browsed = false;
  async function show() {
    const ws = workspace();
    $('gate').hidden = !!ws;
    $('body').hidden = !ws;
    $('ws').innerHTML = ws ? `Workspace <b>${esc(ws.name)}</b> · <button type="button" class="btn btn--quiet btn--xs" data-m-switch>switch</button>` : '';
    root.querySelector<HTMLElement>('[data-m-switch]')?.addEventListener('click', () => chooseWorkspace());
    if (!ws) return;
    jobs = [];
    await Promise.all([loadDatasets().catch((e) => { $('table').innerHTML = `<tbody><tr><td class="empty err">${esc(e.message)}</td></tr></tbody>`; }), loadJobs().catch(() => {})]);
    if (!browsed) {
      browsed = true;
      const want = params.get('path');
      let start = '/nfs/baldar';
      try { start = localStorage.getItem('vizhub-path') || start; } catch { /* private window */ }
      await browse(want ? want.replace(/\/[^/]*$/, '') || '/nfs' : start);
      if (!listing) await browse('/nfs');
      if (want) inspect(want);
    } else if (selected) inspect(selected);
  }
  onWorkspace(() => show());
  loadWorkspace().then(() => show());
}
