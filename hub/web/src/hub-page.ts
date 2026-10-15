// The hub landing page: four tool cards with Genome Canvas's status and each viewer's dataset counts (yours, shared in the lab).
import { api, initChrome, loadWorkspace, onWorkspace } from './common';

initChrome('hub');

api<{ id: string; ok: boolean }[]>('/hub/api/services').then((s) => {
  const gc = s.find((x) => x.id === 'genome-canvas');
  const el = document.querySelector('[data-tool="genome-canvas"] [data-state]')!;
  el.textContent = gc?.ok ? 'online' : 'offline';
  el.className = `chip ${gc?.ok ? 'chip--ok' : 'chip--bad'}`;
}).catch(() => {});

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
