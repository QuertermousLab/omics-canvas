// "Shared datasets": the hub's shared library (/hub/api/library) as a dialog in the style of Genome Canvas's Shared
// tracks — everything any workspace has loaded, converted or combined for one viewer; tick some and add them
// (nothing is copied or converted). "Not in my workspace" (on by default, like Genome Canvas's "Only hg38") leaves
// out what the caller already has. Opened from the viewers' side panels, the CRISPR explorer's header and the dataset
// pages. "Shared by you" lists the caller's own entries to unshare.
import { api, esc, fmtAgo, fmtBytes, requireWorkspace, toast } from './common';

export type Kind = 'spatial' | 'singlecell' | 'crispr';
interface Sample { id: string; title: string; root?: string; nCells?: number }
/** server: library_public */
export interface SharedEntry {
  key: string; kind: Kind; title: string; root: string; source?: string; layout: string; combined?: boolean; description?: string;
  slides: Sample[]; nCells?: number; nGenes?: number; technology?: string; species?: string; bytes?: number;
  screenKind?: string; stats?: Record<string, number>; ownerName?: string; users: string[]; added: number; updated: number;
  /** the caller has it (myUrl), a link that opens it now (any workspace's), the caller added it first */
  mine: boolean; myUrl: string | null; openUrl: string | null; author: boolean;
  /** being converted again; its source file changed or was removed since the conversion */
  updating?: boolean; sourceChanged?: boolean; sourceMissing?: boolean;
}
export interface Added { id: string; title: string; url: string }

const NOUN: Record<Kind, [string, string]> = { spatial: ['dataset', 'datasets'], singlecell: ['dataset', 'datasets'], crispr: ['screen', 'screens'] };

/** One-line facts, as the dataset list shows them. */
function facts(e: SharedEntry) {
  const n = e.slides.length;
  if (e.kind === 'crispr') {
    if (e.combined) return [`combined: ${n} screen${n === 1 ? '' : 's'}`, e.species ?? ''].filter(Boolean).join(' · ');
    const s = e.stats ?? {};
    return [e.screenKind === 'enhancer' ? 'Enhancer screen' : 'Perturb-seq', s.targets != null ? `${s.targets.toLocaleString()} targets` : '',
      s.significant != null ? `${s.significant.toLocaleString()} significant` : '', e.species ?? ''].filter(Boolean).join(' · ');
  }
  return [e.combined ? `combined: ${n} samples` : n > 1 ? `${n} samples` : '', e.nCells ? `${e.nCells.toLocaleString()} cells` : '',
    e.technology ?? '', e.species ?? '', e.bytes && e.layout !== 'project' ? fmtBytes(e.bytes) : ''].filter(Boolean).join(' · ');
}

let entries: SharedEntry[] = [];
let loadedKind: Kind | null = null;

/** The library for a viewer (also refreshes the dialog's list). */
export async function loadShared(kind: Kind) {
  entries = await api<SharedEntry[]>(`/hub/api/library?kind=${kind}`);
  loadedKind = kind;
  return entries;
}

/** How many shared datasets the caller does not have yet. */
export const newCount = (list: SharedEntry[]) => list.filter((e) => !e.mine).length;

/** Fill a count badge (e.g. on a button) with the number not in the caller's workspace. */
export async function showCount(el: HTMLElement | null, kind: Kind) {
  if (!el) return;
  try {
    const n = newCount(await loadShared(kind));
    el.textContent = String(n);
    el.hidden = !n;
  } catch { el.hidden = true; }
}

/** A Genome Canvas-style source card for a side panel (the viewers). */
export const sharedCard = (kind: Kind) => `<button type="button" class="sh-source" data-shared-open>
  <span class="sh-source__glyph" aria-hidden="true"><svg class="icon"><use href="#i-users"/></svg></span>
  <span class="sh-source__text"><strong>Shared ${NOUN[kind][1]}<span class="chip sh-count" data-shared-count hidden></span></strong><small>Add what others in the lab have loaded</small></span>
  <svg class="icon sh-source__arrow" aria-hidden="true"><use href="#i-chevron-down"/></svg></button>`;

/** A compact button (dataset pages, the CRISPR explorer's header). */
export const sharedButton = (kind: Kind) => `<button type="button" class="btn btn--light btn--sm" data-shared-open title="${NOUN[kind][1][0].toUpperCase()}${NOUN[kind][1].slice(1)} others in the lab have loaded, converted or combined: add them without looking for their files"><svg class="icon"><use href="#i-users"/></svg>Shared ${NOUN[kind][1]}<span class="chip sh-count" data-shared-count hidden></span></button>`;

interface Options {
  /** after adding: the new datasets (the dataset page reloads its list; a viewer opens the first) */
  onAdded?: (added: Added[]) => void;
  /** label of the add button */
  addLabel?: string;
}

let dlg: HTMLDialogElement | null = null;
let current: { kind: Kind; opts: Options } | null = null;
const selected = new Set<string>();
let yours = false;
const NEW_KEY = 'vizhub-shared-new-only';

function build() {
  dlg = document.createElement('dialog');
  dlg.className = 'sh-dialog';
  dlg.innerHTML = `<div class="sh-card">
    <header class="sh-head"><div><span class="eyebrow">Lab library</span><h2 data-sh-title></h2></div>
      <button type="button" class="icon-btn ghost" data-sh-close title="Close" aria-label="Close">×</button></header>
    <div class="sh-toolbar">
      <label class="sh-search"><svg class="icon" aria-hidden="true"><use href="#i-search"/></svg><input type="search" data-sh-q placeholder="Filter by name, person, technology or path" aria-label="Filter" /></label>
      <label class="check sh-toggle"><input type="checkbox" data-sh-new checked /><span>Not in my workspace</span></label>
      <button type="button" class="icon-btn ghost" data-sh-refresh title="Check the files again" aria-label="Check the files again">↻</button>
      <p class="sh-note" data-sh-note></p>
    </div>
    <div class="sh-list" data-sh-list aria-live="polite"></div>
    <footer class="sh-foot">
      <div><span data-sh-sum>Nothing selected</span>
        <button type="button" class="btn btn--light btn--sm" data-sh-all>Select shown</button>
        <button type="button" class="btn btn--light btn--sm" data-sh-clear disabled>Clear all</button>
        <button type="button" class="btn btn--quiet btn--sm" data-sh-yours></button></div>
      <div><button type="button" class="btn btn--light" data-sh-cancel>Cancel</button><button type="button" class="btn btn--primary" data-sh-add disabled></button></div>
    </footer></div>`;
  document.body.append(dlg);
  const q = (s: string) => dlg!.querySelector<HTMLElement>(`[data-sh-${s}]`)!;
  q('close').onclick = q('cancel').onclick = () => dlg!.close();
  dlg.addEventListener('click', (e) => { if (e.target === dlg) dlg!.close(); });
  q('q').oninput = render;
  q('new').onchange = () => {
    try { localStorage.setItem(NEW_KEY, (q('new') as HTMLInputElement).checked ? '1' : '0'); } catch { /* private window */ }
    render();
  };
  q('refresh').onclick = () => refresh();
  q('all').onclick = () => { visible().filter((e) => !e.mine).forEach((e) => selected.add(e.key)); render(); };
  q('clear').onclick = () => { selected.clear(); render(); };
  q('yours').onclick = () => { yours = !yours; render(); };
  q('add').onclick = () => addSelected();
  q('list').addEventListener('change', (e) => {
    const box = e.target as HTMLInputElement, key = box.closest<HTMLElement>('[data-key]')?.dataset.key;
    if (!key) return;
    box.checked ? selected.add(key) : selected.delete(key);
    render();
  });
  q('list').addEventListener('click', async (e) => {
    const t = e.target as HTMLElement, row = t.closest<HTMLElement>('[data-key]');
    if (!row) return;
    const entry = entries.find((x) => x.key === row.dataset.key)!;
    if (t.closest('[data-sh-unshare]')) {
      if (!confirm(`Take “${entry.title}” off the shared list?\n\nOthers no longer see it here (those who added it keep it). Your own dataset is kept; it is shared again when you add it again.`)) return;
      try { await api('/hub/api/library/hide', { method: 'POST', body: { key: entry.key } }); } catch (err) { toast(esc((err as Error).message), 8000); }
      refresh();
    } else if (t.closest('.sh-name') && !yours && !entry.mine) {
      selected.has(entry.key) ? selected.delete(entry.key) : selected.add(entry.key);
      render();
    }
  });
}

const text = (e: SharedEntry) => `${e.title} ${e.source ?? ''} ${e.root} ${e.ownerName ?? ''} ${e.users.join(' ')} ${e.technology ?? ''} ${e.species ?? ''} ${e.description ?? ''} ${e.slides.map((s) => s.title).join(' ')}`.toLowerCase();

function visible() {
  const query = dlg!.querySelector<HTMLInputElement>('[data-sh-q]')!.value.trim().toLowerCase();
  const onlyNew = dlg!.querySelector<HTMLInputElement>('[data-sh-new]')!.checked;
  return entries.filter((e) => (yours ? e.author : !onlyNew || !e.mine) && (!query || text(e).includes(query)));
}

function render() {
  if (!dlg || !current) return;
  const { kind } = current, [one, many] = NOUN[kind];
  const q = (s: string) => dlg!.querySelector<HTMLElement>(`[data-sh-${s}]`)!;
  const shown = visible(), have = entries.filter((e) => e.mine).length, own = entries.filter((e) => e.author).length;
  for (const k of [...selected]) if (!entries.some((e) => e.key === k && !e.mine)) selected.delete(k);
  q('title').textContent = yours ? 'Shared by you' : `Shared ${many}`;
  q('note').textContent = yours
    ? `The ${many} you added first: everyone else sees them here. Unshare takes one off the list; your own ${one} is kept.`
    : `Every ${one} anyone in the lab has loaded, converted or combined${have ? ` (${have} already in your workspace)` : ''}. Adding one copies and converts nothing. ${many[0].toUpperCase()}${many.slice(1)} whose files were deleted drop off the list; ones converted again or renamed by their author are updated.`;
  const head = `<div class="sh-row sh-row--head"><span></span><span>NAME</span><span>CONTENTS</span><span>SHARED BY</span><span>${yours ? '' : 'UPDATED'}</span></div>`;
  const rows = shown.map((e) => {
    const others = e.users.filter((u) => u !== e.ownerName);
    const flags = [e.updating ? '<span class="chip chip--warn" title="Its files are being written again right now">converting again</span>' : '',
      e.sourceChanged ? '<span class="chip chip--warn" title="The source file changed after this conversion; select it under Add data to convert it again">source changed</span>' : '',
      e.sourceMissing ? '<span class="chip" title="The source file was moved or deleted; the converted files still open">source removed</span>' : ''].join('');
    const where = e.combined ? `combines ${e.slides.length} folders: ${e.slides.map((s) => s.title).join(', ')}` : e.source ?? e.root;
    const on = selected.has(e.key), have = e.mine && !yours;
    return `<div class="sh-row${on ? ' on' : ''}${have ? ' sh-row--mine' : ''}" data-key="${esc(e.key)}">
      <span>${yours ? '' : have ? '<input type="checkbox" checked disabled title="Already in your workspace" />' : `<input type="checkbox" ${on ? 'checked' : ''} aria-label="Select ${esc(e.title)}" />`}</span>
      <button type="button" class="sh-name" title="${esc(where)}"><span class="sh-icon">${e.combined ? 'SET' : e.slides.length > 1 ? 'PROJ' : kind === 'crispr' ? 'CRISPR' : 'DATA'}</span>
        <span class="sh-name__text"><strong>${esc(e.title)} ${flags}</strong><small>&lrm;${esc(where)}&lrm;</small></span></button>
      <small title="${esc(facts(e))}">${esc(facts(e))}</small>
      <small title="${esc(e.users.join(', '))}">${esc(e.author ? 'you' : e.ownerName ?? '?')}${others.length ? ` + ${others.length}` : ''}</small>
      <span class="sh-end">${yours ? `${e.openUrl ? `<a class="btn btn--quiet btn--xs" href="${esc(e.myUrl ?? e.openUrl)}">Open</a>` : ''}<button type="button" class="btn btn--light btn--xs" data-sh-unshare>Unshare</button>`
        : have ? `<span class="chip chip--ok">in yours</span><a class="sh-peek" href="${esc(e.myUrl!)}">open</a>`
        : `<small>${esc(fmtAgo(e.updated > e.added + 60 ? e.updated : e.added))}</small>${e.openUrl ? `<a class="sh-peek" href="${esc(e.openUrl)}" title="Look at it first (${esc(e.ownerName ?? '')}'s link)">open</a>` : ''}`}</span>
    </div>`;
  }).join('');
  const empty = yours ? `You have not shared any ${many} yet.`
    : entries.some((e) => !e.mine) ? 'Nothing matches the filter.' : entries.length ? `You already have every shared ${one}.` : `Nothing shared yet: ${many} that anyone adds show up here.`;
  q('list').innerHTML = head + (rows || `<div class="sh-empty">${esc(empty)}</div>`);
  const n = selected.size;
  q('sum').textContent = yours ? '' : n ? `${n} ${n === 1 ? one : many} selected` : 'Nothing selected';
  for (const k of ['all', 'clear', 'add']) q(k).hidden = yours;
  q('new').closest('label')!.hidden = yours;
  const pickable = shown.filter((e) => !e.mine);
  (q('all') as HTMLButtonElement).disabled = !pickable.length || pickable.every((e) => selected.has(e.key));
  (q('clear') as HTMLButtonElement).disabled = !n;
  (q('add') as HTMLButtonElement).disabled = !n;
  q('add').textContent = current.opts.addLabel ?? 'Add selected';
  q('yours').textContent = yours ? '← All shared' : `Shared by you (${own})`;
  q('yours').hidden = !yours && !own;
}

async function refresh() {
  if (!dlg || !current) return;
  const list = dlg.querySelector<HTMLElement>('[data-sh-list]')!;
  if (loadedKind !== current.kind || !entries.length) list.innerHTML = '<div class="sh-empty">Reading the shared list…</div>';
  try {
    await loadShared(current.kind);
    render();
  } catch (e) { list.innerHTML = `<div class="sh-empty err">${esc((e as Error).message)}</div>`; }
  document.querySelectorAll<HTMLElement>('[data-shared-count]').forEach((el) => { const n = newCount(entries); el.textContent = String(n); el.hidden = !n; });
}

async function addSelected() {
  if (!dlg || !current) return;
  if (!(await requireWorkspace())) return;
  const keys = [...selected], button = dlg.querySelector<HTMLButtonElement>('[data-sh-add]')!;
  button.disabled = true;
  const added: Added[] = [], failed: string[] = [];
  for (const key of keys) {
    try {
      const r = await api<{ dataset: Added }>('/hub/api/library/add', { method: 'POST', body: { key } });
      added.push(r.dataset);
    } catch (e) { failed.push(`${entries.find((x) => x.key === key)?.title ?? key}: ${(e as Error).message}`); }
  }
  selected.clear();
  if (added.length) toast(`Added to your workspace: ${added.map((d) => `<a href="${esc(d.url)}">${esc(d.title)}</a>`).join(', ')}${failed.length ? ` · ${esc(failed[0])}` : ''}`, 8000);
  else if (failed.length) toast(esc(failed[0]), 8000);
  if (added.length) {
    dlg.close();
    current.opts.onAdded?.(added);
  }
  refresh();
}

/** Open the dialog for a viewer. */
export function openShared(kind: Kind, opts: Options = {}) {
  if (!dlg) build();
  if (current?.kind !== kind) selected.clear();
  current = { kind, opts };
  yours = false;
  dlg!.querySelector<HTMLInputElement>('[data-sh-q]')!.value = '';
  try { dlg!.querySelector<HTMLInputElement>('[data-sh-new]')!.checked = localStorage.getItem(NEW_KEY) !== '0'; } catch { /* private window */ }
  render();
  if (!dlg!.open) dlg!.showModal();
  refresh();
}

/** Wire every [data-shared-open] button under `root` and fill their counts. */
export function bindShared(root: ParentNode, kind: Kind, opts: Options = {}) {
  root.querySelectorAll<HTMLElement>('[data-shared-open]').forEach((b) => (b.onclick = () => openShared(kind, opts)));
  root.querySelectorAll<HTMLElement>('[data-shared-count]').forEach((el) => showCount(el, kind));
}
