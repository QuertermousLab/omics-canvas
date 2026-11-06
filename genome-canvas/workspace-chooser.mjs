// Omics Canvas workspaces, for every module: the chooser (pick, find or create a workspace; the window that opens
// by itself when none is chosen) and the manager (Home workspaces to hide or restore, manual ones to delete).
// Genome Canvas owns the workspaces (its /api/workspaces and the genome_canvas_workspace cookie); the hub's pages,
// GREAT and Genome Canvas itself import this module from Genome Canvas (…/genome-canvas/workspace-chooser.mjs), so
// the API is found next to it. A change made in one page is announced to the others (BroadcastChannel), which
// follow it through watchWorkspace; a page that comes back to the front checks the cookie's choice again.

const BASE = new URL(".", import.meta.url);
const CHANNEL = "omics-canvas:workspace";
const api = (path) => new URL(`api/${path}`, BASE).toString();
const ICON_LAYERS = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m12 3 9 5-9 5-9-5 9-5Z"/><path d="m3 13 9 5 9-5"/></svg>';
const ICON_PLUS = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>';

const state = { selected: null, workspaces: [], hidden: [], localMode: false, mode: "choose", options: {}, waiters: [] };
let dialog = null;
let channel = null;

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

async function request(path, body, method) {
  const response = await fetch(api(path), body === undefined && !method ? { credentials: "same-origin" } : {
    method: method || "POST", credentials: "same-origin",
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || `Genome Canvas: HTTP ${response.status}`);
  return payload;
}

/** The workspace list and the selected one ({selected, workspaces, hiddenHomeWorkspaces, localMode}). */
export async function loadWorkspaces() {
  const payload = await request("workspaces");
  state.selected = payload.selected || null;
  state.workspaces = Array.isArray(payload.workspaces) ? payload.workspaces : [];
  state.hidden = Array.isArray(payload.hiddenHomeWorkspaces) ? payload.hiddenHomeWorkspaces : [];
  state.localMode = Boolean(payload.localMode);
  return payload;
}

function announce(message) {
  try {
    channel ??= new BroadcastChannel(CHANNEL);
    channel.postMessage(message);
  } catch { /* no BroadcastChannel: other pages check again when they come back to the front */ }
}

/** Tell the other open pages that the selection changed (pages that select through Genome Canvas's API directly). */
export function announceWorkspace(workspace) {
  announce({ type: "selected", id: workspace?.id || null });
}

/** Select a workspace (its cookie, for every module) and tell the other pages. */
export async function selectWorkspace(id) {
  const { workspace } = await request("workspaces/select", { id });
  state.selected = workspace;
  announceWorkspace(workspace);
  return workspace;
}

/**
 * Call onChange(workspace or null) when the selection changes in another page (or while this page was in the
 * background); currentId() is the page's own idea of the selected workspace.
 */
export function watchWorkspace(currentId, onChange) {
  let checking = null;
  const check = () => {
    checking ??= loadWorkspaces().then((payload) => {
      const selected = payload.selected || null;
      if ((selected?.id || null) !== (currentId() || null)) onChange(selected);
    }).catch(() => {}).finally(() => { checking = null; });
    return checking;
  };
  try {
    const listener = new BroadcastChannel(CHANNEL);
    listener.onmessage = (event) => { if (event.data?.type === "selected") check(); };
  } catch { /* checked on visibility only */ }
  let last = 0;
  const again = () => {
    if (document.visibilityState !== "visible" || Date.now() - last < 2000) return;
    last = Date.now();
    check();
  };
  document.addEventListener("visibilitychange", again);
  window.addEventListener("focus", again);
}

// ---------------------------------------------------------------- the dialog

const STYLE = `
.ocws { border: 0; padding: 0; background: transparent; color: var(--text); max-width: calc(100vw - 32px); max-height: calc(100vh - 32px); font: 400 13px/1.45 var(--font, ui-sans-serif, system-ui, sans-serif); }
.ocws::backdrop { background: rgba(36, 41, 51, .45); backdrop-filter: blur(4px); }
.ocws * { box-sizing: border-box; }
.ocws svg { width: 16px; height: 16px; flex: none; fill: none; stroke: currentColor; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; }
.ocws-card { width: min(560px, 100vw - 32px); max-height: calc(100vh - 32px); display: grid; gap: 12px; padding: 20px 22px 14px; overflow: auto;
  border: 1px solid var(--border); border-radius: var(--radius-xl, 18px); background: var(--surface); box-shadow: var(--shadow-lg); }
.ocws-head { display: flex; gap: 12px; align-items: center; }
.ocws-logo { width: 34px; height: 34px; display: grid; place-items: center; flex: none; border-radius: 99px; background: var(--accent-soft-2); color: var(--accent-text); }
.ocws-head b { display: block; font-family: var(--heading-font, Georgia, serif); font-size: 18px; font-weight: 650; }
.ocws-head small { color: var(--text-3); font-size: 12.5px; }
.ocws input { height: 34px; width: 100%; border: 1px solid var(--border-strong); border-radius: var(--radius, 9px); background: var(--surface); color: var(--text); padding: 0 10px; font: inherit; }
.ocws input:focus { outline: none; border-color: var(--accent); box-shadow: 0 0 0 3px var(--ring); }
.ocws button { font: inherit; color: inherit; cursor: pointer; }
.ocws-list { list-style: none; margin: 0; padding: 0; max-height: 320px; overflow: auto; display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 6px; }
.ocws-list button { width: 100%; height: 38px; display: flex; align-items: center; gap: 8px; padding: 0 10px; border: 1px solid var(--border); border-radius: var(--radius, 9px);
  background: var(--surface); text-align: left; }
.ocws-list button:hover { border-color: var(--accent); }
.ocws-list button.on { border-color: var(--accent); background: var(--accent-soft); color: var(--accent-text); }
.ocws-list button span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ocws-list small, .ocws-row small { margin-left: auto; color: var(--text-4); font-size: 10.5px; letter-spacing: .03em; text-transform: uppercase; }
.ocws-muted { color: var(--text-3); }
.ocws-create { display: flex; gap: 8px; }
.ocws-btn { height: 34px; display: inline-flex; align-items: center; justify-content: center; gap: 7px; padding: 0 13px; border: 1px solid var(--border-strong);
  border-radius: var(--radius, 9px); background: var(--surface); color: var(--text-2); font-weight: 550; white-space: nowrap; }
.ocws-btn:hover { background: var(--surface-hover); color: var(--text); }
.ocws-btn.quiet { border-color: transparent; background: transparent; color: var(--text-3); }
.ocws-btn.quiet:hover { color: var(--text); background: var(--surface-hover); }
.ocws-btn.small { height: 28px; padding: 0 10px; font-size: 12px; }
.ocws-btn.danger { color: var(--danger); }
.ocws-btn.danger:hover { border-color: var(--danger-border); background: var(--danger-soft); }
.ocws-foot { display: flex; justify-content: space-between; gap: 8px; }
.ocws-err { margin: 0; color: var(--danger); }
.ocws-group { display: grid; gap: 6px; }
.ocws-group h3 { display: flex; align-items: baseline; justify-content: space-between; margin: 4px 0 0; font: 650 12px/1.3 var(--font, sans-serif); color: var(--text-2); }
.ocws-group h3 small { color: var(--text-4); font-weight: 500; }
.ocws-rows { display: grid; gap: 4px; max-height: 240px; overflow: auto; }
.ocws-row { display: flex; align-items: center; gap: 10px; min-height: 40px; padding: 4px 6px 4px 10px; border: 1px solid var(--border); border-radius: var(--radius, 9px); }
.ocws-row.on { border-color: var(--accent); background: var(--accent-soft); }
.ocws-row b { min-width: 0; overflow: hidden; font-weight: 600; text-overflow: ellipsis; white-space: nowrap; }
.ocws-row .ocws-actions { display: flex; gap: 4px; margin-left: 8px; }
.ocws-note { margin: 0; color: var(--text-3); font-size: 12px; }
.ocws details summary { cursor: pointer; color: var(--text-2); font-weight: 600; font-size: 12px; }
.ocws details[open] summary { margin-bottom: 6px; }
`;

function ensureDialog() {
  if (dialog) return dialog;
  if (!document.getElementById("ocws-style")) {
    const style = document.createElement("style");
    style.id = "ocws-style";
    style.textContent = STYLE;
    document.head.append(style);
  }
  dialog = document.createElement("dialog");
  dialog.className = "ocws";
  dialog.setAttribute("aria-labelledby", "ocws-title");
  dialog.innerHTML = `<form method="dialog" class="ocws-card">
    <header class="ocws-head"><span class="ocws-logo">${ICON_LAYERS}</span><div><b id="ocws-title"></b><small data-ocws-subtitle></small></div></header>
    <div data-ocws-body></div>
    <div class="ocws-create"><input type="text" maxlength="48" placeholder="New workspace name" aria-label="New workspace name" data-ocws-new>
      <button type="button" class="ocws-btn" data-ocws-create>${ICON_PLUS}Create</button></div>
    <p class="ocws-err" data-ocws-err hidden></p>
    <footer class="ocws-foot"><button type="button" class="ocws-btn quiet" data-ocws-switch></button>
      <button type="submit" class="ocws-btn" data-ocws-close>Close</button></footer>
  </form>`;
  document.body.append(dialog);
  dialog.addEventListener("cancel", (event) => { if (state.options.required && !state.selected) event.preventDefault(); });
  dialog.addEventListener("close", () => {
    const waiters = state.waiters;
    state.waiters = [];
    waiters.forEach((resolve) => resolve(state.selected));
  });
  dialog.querySelector("[data-ocws-create]").addEventListener("click", create);
  dialog.querySelector("[data-ocws-new]").addEventListener("keydown", (event) => {
    if (event.key === "Enter") { event.preventDefault(); create(); }
  });
  dialog.querySelector("[data-ocws-switch]").addEventListener("click", () => {
    if (state.mode === "choose" && state.options.manage) {
      dialog.close();  // the page's own manager (Genome Canvas's workspace page)
      state.options.manage();
      return;
    }
    state.mode = state.mode === "choose" ? "manage" : "choose";
    render();
  });
  dialog.addEventListener("click", (event) => {
    const pick = event.target.closest("[data-ocws-pick]");
    const action = event.target.closest("[data-ocws-action]");
    if (action) act(action.dataset.ocwsAction, action.dataset.id);
    else if (pick) choose(pick.dataset.ocwsPick);
  });
  dialog.addEventListener("input", (event) => { if (event.target.matches("[data-ocws-q]")) renderList(); });
  return dialog;
}

function showError(error) {
  const node = dialog.querySelector("[data-ocws-err]");
  node.textContent = error ? error.message || String(error) : "";
  node.hidden = !error;
}

function kindLabel(workspace) {
  return workspace.kind === "home" ? "Home" : "Shared";
}

function render() {
  const manage = state.mode === "manage";
  dialog.querySelector("#ocws-title").textContent = manage ? "Manage workspaces" : "Choose your workspace";
  dialog.querySelector("[data-ocws-subtitle]").textContent = manage
    ? "Home workspaces come from /home on the server; manual workspaces start in the shared /nfs folders."
    : "Your datasets, tracks and analyses are kept per workspace, the same in every Omics Canvas module.";
  dialog.querySelector("[data-ocws-switch]").innerHTML = manage ? "&larr; Back to choosing" : "Manage workspaces&hellip;";
  dialog.querySelector("[data-ocws-close]").hidden = Boolean(state.options.required && !state.selected);
  const body = dialog.querySelector("[data-ocws-body]");
  if (!manage) {
    body.innerHTML = `<div style="display:grid;gap:10px"><input type="search" placeholder="Find a workspace" aria-label="Find a workspace" data-ocws-q>
      <ul class="ocws-list" data-ocws-list></ul></div>`;
    renderList();
    return;
  }
  const row = (w, actions) => `<div class="ocws-row${w.id === state.selected?.id ? " on" : ""}"><b>${esc(w.name)}</b>
    <small>${w.id === state.selected?.id ? "current · " : ""}${esc(kindLabel(w))}</small><span class="ocws-actions">${actions}</span></div>`;
  const open = (w) => w.id === state.selected?.id ? "" : `<button type="button" class="ocws-btn small" data-ocws-pick="${esc(w.id)}">Open</button>`;
  const homes = state.workspaces.filter((w) => w.kind === "home");
  const manual = state.workspaces.filter((w) => w.kind !== "home");
  body.innerHTML = `<div style="display:grid;gap:10px">
    <section class="ocws-group"><h3>Server Home workspaces <small>discovered from /home</small></h3><div class="ocws-rows">${homes.map((w) =>
      row(w, `${open(w)}<button type="button" class="ocws-btn small" data-ocws-action="hide" data-id="${esc(w.id)}">Hide</button>`)).join("")
      || '<p class="ocws-note">No Home workspaces.</p>'}</div></section>
    <section class="ocws-group"><h3>Manual workspaces <small>start in shared /nfs</small></h3><div class="ocws-rows">${manual.map((w) =>
      row(w, `${open(w)}<button type="button" class="ocws-btn small danger" data-ocws-action="delete" data-id="${esc(w.id)}">Delete</button>`)).join("")
      || '<p class="ocws-note">No manual workspaces yet.</p>'}</div></section>
    ${state.hidden.length ? `<details class="ocws-group"><summary>Hidden Home workspaces (${state.hidden.length})</summary><div class="ocws-rows">${state.hidden.map((w) =>
      row(w, `<button type="button" class="ocws-btn small" data-ocws-action="restore" data-id="${esc(w.id)}">Restore</button>`)).join("")}</div></details>` : ""}
    <p class="ocws-note">Hiding a Home workspace never deletes its files; deleting a manual workspace keeps its files on disk.</p></div>`;
}

function renderList() {
  const list = dialog.querySelector("[data-ocws-list]");
  if (!list) return;
  const query = (dialog.querySelector("[data-ocws-q]")?.value || "").trim().toLocaleLowerCase();
  const shown = state.workspaces.filter((w) => !query || w.name.toLocaleLowerCase().includes(query));
  list.innerHTML = shown.map((w) => `<li><button type="button" data-ocws-pick="${esc(w.id)}" class="${w.id === state.selected?.id ? "on" : ""}">
    <span>${esc(w.name)}</span><small>${w.kind === "home" ? "home" : ""}</small></button></li>`).join("")
    || `<li class="ocws-muted">${state.workspaces.length ? "No workspace matches." : "No workspaces yet: create one below."}</li>`;
}

async function choose(id) {
  showError(null);
  try {
    await selectWorkspace(id);
    dialog.close();
  } catch (error) { showError(error); }
}

async function create() {
  const input = dialog.querySelector("[data-ocws-new]");
  const name = input.value.trim();
  if (!name) { input.focus(); return; }
  showError(null);
  try {
    const { workspace } = await request("workspaces", { name });  // a new workspace is selected at once
    state.selected = workspace;
    input.value = "";
    announceWorkspace(workspace);
    dialog.close();
  } catch (error) { showError(error); }
}

async function act(action, id) {
  const workspace = [...state.workspaces, ...state.hidden].find((w) => w.id === id);
  if (!workspace) return;
  showError(null);
  try {
    if (action === "restore") {
      await request("workspaces/restore", { id });
    } else {
      const hide = action === "hide";
      const detail = hide ? "The Home directory and its files are not deleted; restore it here at any time."
        : "Its files stay on disk; the workspace and its saved Favorites are removed from the list.";
      if (!window.confirm(`${hide ? "Hide" : "Delete"} workspace “${workspace.name}”?\n\n${detail}`)) return;
      await request(`workspaces/${encodeURIComponent(id)}`, undefined, "DELETE");
      if (state.selected?.id === id) {
        state.selected = null;
        announceWorkspace(null);
      }
    }
    await loadWorkspaces();
    render();
  } catch (error) { showError(error); }
}

function open(mode, options) {
  ensureDialog();
  state.mode = mode;
  state.options = options || {};
  showError(null);
  render();
  if (!dialog.open) dialog.showModal();
  loadWorkspaces().then(render).catch(showError);
  window.setTimeout(() => dialog.querySelector(mode === "choose" ? "[data-ocws-q]" : "[data-ocws-new]")?.focus(), 0);
  return new Promise((resolve) => state.waiters.push(resolve));
}

/**
 * Open the chooser; resolves with the selected workspace when it closes (null if none). Options: required (no
 * Close until one is chosen: the page cannot work without), manage (a function that opens the page's own manager
 * instead of the built-in one, as Genome Canvas does).
 */
export function chooseWorkspace(options = {}) {
  return open("choose", options);
}

/** Open the manager (hide / restore Home workspaces, delete manual ones, create, open). */
export function manageWorkspaces(options = {}) {
  return open("manage", options);
}
