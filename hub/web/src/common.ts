// Shared by the hub, viewer and explorer pages: escaping, API calls, the Genome Canvas theme and workspaces.

const ESC: Record<string, string> = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
/** Escape text before putting it into innerHTML. */
export const esc = (s: unknown) => String(s ?? '').replace(/[&<>"']/g, (c) => ESC[c]);

// ------------------------------------------------------------------ workspaces (Genome Canvas's)
// Genome Canvas keeps the selected workspace in a cookie scoped to /genome-canvas/, so the pages ask its API
// (same origin) and pass the id to the hub API as X-Vizhub-Workspace. Choosing here also chooses it there.

export interface Workspace { id: string; name: string; kind: string }
let current: Workspace | null = null;
let catalog: Workspace[] = [];
let loaded: Promise<Workspace | null> | null = null;
const listeners: ((w: Workspace | null) => void)[] = [];

export const workspace = () => current;
export const onWorkspace = (fn: (w: Workspace | null) => void) => listeners.push(fn);

async function gc<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`/genome-canvas/api/${path}`, body === undefined ? {} : {
    method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error((data as { error?: string }).error ?? `Genome Canvas: ${res.status}`);
  return data as T;
}

/** Tell the Omics Canvas desktop app (WKWebView message handler "vizhub") what changed. */
export function postToDesktop(message: Record<string, unknown>) {
  try { (window as any).webkit?.messageHandlers?.vizhub?.postMessage(message); } catch { /* not in the app */ }
}

function setWorkspace(w: Workspace | null) {
  current = w;
  postToDesktop({ type: 'workspace', workspace: w });
  const name = document.querySelector<HTMLElement>('[data-ws-name]');
  if (name) name.textContent = w?.name ?? 'Choose workspace';
  document.querySelector('[data-ws-button]')?.classList.toggle('unset', !w);
  listeners.forEach((fn) => fn(w));
}

export function loadWorkspace() {
  return (loaded ??= gc<{ selected: Workspace | null; workspaces: Workspace[] }>('workspaces').then((d) => {
    catalog = d.workspaces ?? [];
    setWorkspace(d.selected);
    return d.selected;
  }).catch(() => { setWorkspace(null); return null; }));
}

let dialogWaiters: ((w: Workspace | null) => void)[] = [];
/** Open the workspace chooser; resolves with the chosen workspace (or the current one when closed). */
export function chooseWorkspace(): Promise<Workspace | null> {
  const dlg = document.querySelector<HTMLDialogElement>('[data-ws-dialog]')!;
  const list = dlg.querySelector<HTMLElement>('[data-ws-list]')!, q = dlg.querySelector<HTMLInputElement>('[data-ws-q]')!;
  const err = dlg.querySelector<HTMLElement>('[data-ws-err]')!;
  const render = () => {
    const f = q.value.trim().toLowerCase();
    const shown = catalog.filter((w) => !f || w.name.toLowerCase().includes(f));
    list.innerHTML = shown.map((w) => `<li><button type="button" data-ws="${esc(w.id)}" class="${w.id === current?.id ? 'on' : ''}">
      <span>${esc(w.name)}</span><small>${w.kind === 'home' ? 'home' : ''}</small></button></li>`).join('') || '<li class="muted">No workspace matches.</li>';
  };
  const pick = async (id: string) => {
    try {
      const { workspace: w } = await gc<{ workspace: Workspace }>('workspaces/select', { id });
      setWorkspace(w);
      dlg.close();
    } catch (e) { err.textContent = (e as Error).message; err.hidden = false; }
  };
  if (!dlg.dataset.bound) {
    dlg.dataset.bound = '1';
    q.oninput = render;
    list.onclick = (e) => { const b = (e.target as HTMLElement).closest<HTMLElement>('[data-ws]'); if (b) pick(b.dataset.ws!); };
    dlg.querySelector<HTMLButtonElement>('[data-ws-create]')!.onclick = async () => {
      const name = dlg.querySelector<HTMLInputElement>('[data-ws-new]')!.value.trim();
      if (!name) return;
      try {
        const { workspace: w } = await gc<{ workspace: Workspace }>('workspaces', { name });
        catalog.push(w);
        setWorkspace(w);
        dlg.close();
      } catch (e) { err.textContent = (e as Error).message; err.hidden = false; }
    };
    dlg.addEventListener('close', () => { const ws = dialogWaiters; dialogWaiters = []; ws.forEach((fn) => fn(current)); });
  }
  err.hidden = true;
  q.value = '';
  gc<{ workspaces: Workspace[] }>('workspaces').then((d) => { catalog = d.workspaces ?? catalog; render(); }).catch(() => render());
  render();
  if (!dlg.open) dlg.showModal();
  return new Promise((resolve) => dialogWaiters.push(resolve));
}

/** The current workspace, asking for one first if none is chosen. */
export async function requireWorkspace() {
  return (await loadWorkspace()) ?? current ?? chooseWorkspace();
}

// ------------------------------------------------------------------ API

export async function api<T>(path: string, init: { method?: string; body?: unknown } = {}): Promise<T> {
  const headers: Record<string, string> = {};
  if (init.body !== undefined) headers['content-type'] = 'application/json';
  if (current) headers['x-vizhub-workspace'] = current.id;
  const res = await fetch(path, { method: init.method ?? 'GET', headers, body: init.body !== undefined ? JSON.stringify(init.body) : undefined });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error((data as { error?: string }).error ?? `Request failed (${res.status})`);
  return data as T;
}

// ------------------------------------------------------------------ theme (Genome Canvas: key genome-canvas:theme)

const THEME_KEY = 'genome-canvas:theme';
/** A theme token's current value, e.g. css('--text-3'); canvases read colors through this. */
export const css = (name: string) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

function applyTheme(theme: string) {
  document.documentElement.dataset.theme = theme;
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', theme === 'dark' ? '#2e3440' : '#f8f9fb');
  const t = document.querySelector<HTMLElement>('[data-theme-toggle]');
  if (t) t.title = t.ariaLabel = `Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`;
  window.dispatchEvent(new Event('vizhub:theme'));
}

const PAGE_TITLE: Record<string, string> = { hub: '', spatial: 'Spatial viewer', 'single-cell': 'Single-cell viewer', crispr: 'CRISPR explorer' };

/** Top bar: page title, theme toggle, workspace chip; the desktop app bridge. */
export function initChrome(navId: string) {
  const title = document.querySelector<HTMLElement>('[data-page-title]');
  if (title) title.textContent = PAGE_TITLE[navId] ?? '';
  // Omics Canvas desktop app (desktop/): the native window calls these and listens to postToDesktop
  (window as any).VizHubDesktop = {
    setTheme(theme: string) { applyTheme(theme === 'dark' ? 'dark' : 'light'); return true; },
    exportPNG() {
      // only on a page that shows a plot (the dataset pages keep the viewer's markup hidden)
      const b = document.querySelector<HTMLElement>('[data-shot]');
      if (!b || b.offsetParent === null) return false;
      b.click();
      return true;
    },
    workspaces: () => gc<{ selected: Workspace | null; workspaces: Workspace[] }>('workspaces'),
    async selectWorkspace(id: string) {
      const { workspace: w } = await gc<{ workspace: Workspace }>('workspaces/select', { id });
      setWorkspace(w);
      return w;
    },
    async datasets(kind: string) { await loadWorkspace(); return current ? api(`/hub/api/datasets?kind=${encodeURIComponent(kind)}`) : []; },
    state: () => ({ page: navId, path: location.pathname + location.search, title: document.title, workspace: current }),
  };
  postToDesktop({ type: 'page', page: navId, path: location.pathname + location.search });
  const system = window.matchMedia('(prefers-color-scheme: dark)');
  const saved = () => { try { return localStorage.getItem(THEME_KEY); } catch { return null; } };
  applyTheme(saved() || (system.matches ? 'dark' : 'light'));
  system.addEventListener?.('change', (e) => { if (!saved()) applyTheme(e.matches ? 'dark' : 'light'); });
  // the other tabs (Genome Canvas included) follow a change made in one
  window.addEventListener('storage', (e) => { if (e.key === THEME_KEY) applyTheme(e.newValue || (system.matches ? 'dark' : 'light')); });
  document.querySelector<HTMLElement>('[data-theme-toggle]')!.onclick = () => {
    const theme = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
    try {
      if (theme === (system.matches ? 'dark' : 'light')) localStorage.removeItem(THEME_KEY);
      else localStorage.setItem(THEME_KEY, theme);
    } catch { /* storage disabled */ }
    applyTheme(theme);
  };
  document.querySelector<HTMLElement>('[data-ws-button]')!.onclick = () => chooseWorkspace();
  loadWorkspace();
}

// ------------------------------------------------------------------ formatting

export const fmtBytes = (n: number | null | undefined) => {
  if (n == null) return '';
  if (n < 1024) return `${n} B`;
  const u = ['KB', 'MB', 'GB', 'TB'];
  let i = -1;
  do { n /= 1024; i++; } while (n >= 1024 && i < u.length - 1);
  return `${n.toFixed(n < 10 ? 1 : 0)} ${u[i]}`;
};

export const fmtAgo = (sec: number) => {
  const d = Date.now() / 1000 - sec;
  if (d < 60) return 'just now';
  if (d < 3600) return `${Math.round(d / 60)} min ago`;
  if (d < 86400) return `${Math.round(d / 3600)} h ago`;
  return new Date(sec * 1000).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
};

export const fmtDuration = (sec: number) => (sec < 60 ? `${Math.round(sec)} s` : sec < 3600 ? `${Math.floor(sec / 60)} min ${Math.round(sec % 60)} s` : `${Math.floor(sec / 3600)} h ${Math.round((sec % 3600) / 60)} min`);

export function toast(html: string, ms = 5000) {
  let t = document.querySelector<HTMLElement>('[data-toast]');
  if (!t) { t = Object.assign(document.createElement('div'), { className: 'toast' }); t.dataset.toast = ''; document.body.append(t); }
  t.innerHTML = html;
  t.hidden = false;
  clearTimeout((t as any)._t);
  (t as any)._t = setTimeout(() => (t!.hidden = true), ms);
}
