// "Gene Program Explorer" and other interactive HTML reports of a CRISPR screen: list them, locate an HTML file on
// /nfs with a small folder browser, add or remove it. Used by the CRISPR explorer (next to the screen picker) and by
// the CRISPR datasets page (Reports…). Reports are kept per data folder by the hub (state/reports.json).
import { api, esc } from './common';

interface Report { path: string; title: string; found: boolean; url: string }
interface Entry { name: string; path: string; dir: boolean }
interface Listing { path: string; parent: string | null; entries: Entry[] }

let dlg: HTMLDialogElement | null = null;

function build() {
  dlg = document.createElement('dialog');
  dlg.className = 'ws-dialog';
  dlg.innerHTML = `<form method="dialog" class="ws-card combine reports">
    <div class="ws-head"><div><b data-r-title>Gene Program Explorer</b><small>Interactive HTML pages of this screen, e.g. the
      PerturbNMF gene program explorer. They open from the CRISPR explorer for everyone who has the screen; the files stay
      where they are. One next to the screen's results (…/perturbnmf/gene_program_explorer/report/) is found on its own.</small></div></div>
    <ul class="combine__pool reports__list" data-r-list></ul>
    <div><span class="eyebrow">Locate an HTML file</span>
      <div class="reports__bar"><button type="button" class="icon-btn sm" data-r-up title="Parent folder" aria-label="Parent folder"><svg class="icon"><use href="#i-up"/></svg></button>
        <input class="input mono" data-r-path spellcheck="false" autocomplete="off" aria-label="Folder or HTML file" placeholder="/nfs/baldar/…" />
        <button type="button" class="btn btn--light btn--sm" data-r-go>Go</button></div>
      <ul class="files reports__files" data-r-files></ul></div>
    <label class="lbl">Name <input class="input" data-r-name placeholder="Gene Program Explorer (the page's own title)" /></label>
    <p class="err ws-err" data-r-err hidden></p>
    <div class="ws-foot"><button type="button" class="btn btn--quiet" data-r-close>Close</button>
      <button type="button" class="btn btn--primary" data-r-add disabled>Add selected file</button></div>
  </form>`;
  document.body.append(dlg);
}

const q = <T extends HTMLElement = HTMLElement>(s: string) => dlg!.querySelector<T>(`[data-r-${s}]`)!;

/** Opens the dialog for dataset `id` (a CRISPR screen); `changed` runs after a report was added or removed. */
export async function openReportsDialog(id: string, title: string, changed: () => void, start = '') {
  if (!dlg) build();
  let picked = '';
  q('title').textContent = `Reports of “${title}”`;
  q('err').hidden = true;
  q<HTMLInputElement>('name').value = '';
  const fail = (e: unknown) => { q('err').textContent = (e as Error).message; q('err').hidden = false; };

  const render = (list: Report[]) => {
    q('list').innerHTML = list.length ? list.map((r) => `<li class="reports__row"><a href="${esc(r.url)}" target="_blank" rel="noopener"><b>${esc(r.title)}</b> ↗</a>
        <span class="mono small muted" title="${esc(r.path)}">${esc(r.path)}</span>${r.found ? '<span class="chip">found next to the results</span>' : ''}
        <button type="button" class="btn btn--quiet btn--xs" data-path="${esc(r.path)}">Remove</button></li>`).join('')
      : '<li class="muted small">No report linked yet — locate its HTML file below.</li>';
  };
  const browse = async (path: string): Promise<boolean> => {
    try {
      const l = await api<Listing>(`/hub/api/browse?path=${encodeURIComponent(path)}`);
      q<HTMLInputElement>('path').value = l.path;
      q('up').dataset.parent = l.parent ?? '';
      const shown = l.entries.filter((e) => e.dir || /\.html?$/i.test(e.name)).sort((a, b) => Number(b.dir) - Number(a.dir) || a.name.localeCompare(b.name));
      q('files').innerHTML = shown.map((e) => `<li${e.path === picked ? ' class="on"' : ''}><button type="button" class="file${e.dir ? ' file--dir' : ''}" data-entry="${esc(e.path)}" data-dir="${e.dir ? 1 : ''}">
          <svg class="icon file__icon"><use href="#i-${e.dir ? 'folder' : 'file'}"/></svg><span class="file__name">${esc(e.name)}</span></button></li>`).join('')
        || '<li class="muted small pad">No folders or HTML files here.</li>';
      q('err').hidden = true;
      return true;
    } catch (e) { fail(e); return false; }
  };
  const pick = (path: string) => {
    picked = path;
    q<HTMLButtonElement>('add').disabled = !picked;
    dlg!.querySelectorAll<HTMLElement>('[data-entry]').forEach((b) => b.parentElement!.classList.toggle('on', b.dataset.entry === picked));
  };

  q('files').onclick = (e) => {
    const b = (e.target as HTMLElement).closest<HTMLElement>('[data-entry]');
    if (!b) return;
    if (b.dataset.dir) { pick(''); browse(b.dataset.entry!); } else pick(b.dataset.entry!);
  };
  q('files').ondblclick = (e) => { const b = (e.target as HTMLElement).closest<HTMLElement>('[data-entry]'); if (b && !b.dataset.dir) q('add').click(); };
  q('up').onclick = () => { if (q('up').dataset.parent) browse(q('up').dataset.parent!); };
  q('go').onclick = () => {
    const v = q<HTMLInputElement>('path').value.trim();
    if (/\.html?$/i.test(v)) { pick(v); browse(v); } else browse(v);
  };
  q<HTMLInputElement>('path').onkeydown = (e) => { if (e.key === 'Enter') { e.preventDefault(); q('go').click(); } };
  q('add').onclick = async () => {
    try {
      render(await api<Report[]>(`/hub/api/datasets/${id}/reports`, { method: 'POST', body: { path: picked, title: q<HTMLInputElement>('name').value.trim() } }));
      pick('');
      q<HTMLInputElement>('name').value = '';
      changed();
    } catch (e) { fail(e); }
  };
  q('list').onclick = async (e) => {
    const b = (e.target as HTMLElement).closest<HTMLElement>('button[data-path]');
    if (!b) return;
    try {
      render(await api<Report[]>(`/hub/api/datasets/${id}/reports?path=${encodeURIComponent(b.dataset.path!)}`, { method: 'DELETE' }));
      changed();
    } catch (err) { fail(err); }
  };
  q('close').onclick = () => dlg!.close();

  pick('');
  const list = await api<Report[]>(`/hub/api/datasets/${id}/reports`).catch(() => [] as Report[]);
  render(list);
  // start in the first report's folder, else at the screen's data (a file opens its folder), else the data roots
  if (!(await browse(list[0]?.path || start || ''))) await browse('');
  dlg!.showModal();
}
