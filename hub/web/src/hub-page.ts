// The hub landing page: tool cards with Genome Canvas's and GREAT's status and each viewer's dataset counts (yours, shared in the lab).
import { api, initChrome, loadWorkspace, onWorkspace } from './common';

initChrome('hub');

api<{ id: string; ok: boolean }[]>('/hub/api/services').then((s) => {
  const gc = s.find((x) => x.id === 'genome-canvas');
  const el = document.querySelector('[data-tool="genome-canvas"] [data-state]')!;
  el.textContent = gc?.ok ? 'online' : 'offline';
  el.className = `chip ${gc?.ok ? 'chip--ok' : 'chip--bad'}`;
}).catch(() => {});

// GREAT (Genome Canvas's /great/): the assemblies it has data for
fetch('/genome-canvas/api/great/status').then((r) => (r.ok ? r.json() : Promise.reject(r))).then((s: { assemblies: { name: string }[] }) => {
  const el = document.querySelector('[data-tool="great"] [data-state]')!;
  el.textContent = s.assemblies.length ? s.assemblies.map((a) => a.name).join(' · ') : 'no data';
  el.className = `chip ${s.assemblies.length ? 'chip--ok' : 'chip--bad'}`;
}).catch(() => {
  const el = document.querySelector('[data-tool="great"] [data-state]');
  if (el) { el.textContent = 'offline'; el.className = 'chip chip--bad'; }
});

async function counts() {
  const s = await api<{ kinds: Record<string, { mine: number; all: number; shared?: number }>; running: number; workspace: { name: string } | null }>('/hub/api/summary');
  for (const [k, n] of Object.entries(s.kinds)) {
    const el = document.querySelector(`[data-tool="${k}"] [data-count]`);
    if (!el) continue;
    const own = s.workspace ? (n.shared ? `${n.mine} yours` : `${n.mine} in your workspace`) : `${n.all} dataset${n.all === 1 ? '' : 's'}`;
    el.textContent = n.shared ? `${own} · ${n.shared} shared` : own;
    el.className = `chip ${(s.workspace ? n.mine : n.all) ? 'chip--accent' : ''}`;
  }
}
onWorkspace(() => counts().catch(() => {}));
loadWorkspace();
