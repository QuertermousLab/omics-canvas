#!/usr/bin/env python3
"""Omics Canvas: the landing page at :8892/hub/, the ATHENA-style spatial and single-cell
viewers (/spatial/, /single-cell/) and CRISPR screen explorer (/crispr/), and the API behind them.

Datasets are found on /nfs with the file browser. Files already in the ATHENA viewer format are
registered as they are; anything else (h5ad, rds, 10x h5, loom, Xenium bundles, CRISPR result tables)
is converted by converters/convert.py in a background job (progress shown in the hub) into
<source>.vizhub/ next to the source, then registered. The registry is state/registry.json.

Run: venv/bin/python server.py   (the Apache gateway on :8892 proxies /hub/, /spatial/, /single-cell/, /crispr/ here)
"""
import json
import os
import posixpath
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.request
import uuid
from collections import deque
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse

HERE = Path(__file__).resolve().parent
VIZHUB = HERE.parent
STATE = VIZHUB / 'state'
STATIC = HERE / 'static'
sys.path.insert(0, str(HERE / 'converters'))
import detect  # noqa: E402

CONFIG = json.loads((HERE / 'config.json').read_text())
ROOTS = [dict(label=r['label'], path=os.path.normpath(r['path'])) for r in CONFIG['roots']]
PYTHON = str(HERE / 'venv' / 'bin' / 'python')
KIND_PATH = {'spatial': 'spatial', 'singlecell': 'single-cell', 'crispr': 'crispr'}
KIND_NAME = {'spatial': 'Spatial viewer', 'singlecell': 'Single-cell viewer', 'crispr': 'CRISPR explorer'}

STATE.mkdir(parents=True, exist_ok=True)
(STATE / 'jobs').mkdir(exist_ok=True)
lock = threading.RLock()


# ----------------------------------------------------------------- persistence

def _load(name, default):
    try:
        return json.loads((STATE / name).read_text())
    except Exception:
        return default


def _save(name, obj):
    tmp = STATE / f'.{name}.tmp'
    tmp.write_text(json.dumps(obj, indent=1))
    tmp.replace(STATE / name)


registry = _load('registry.json', {'datasets': []})
jobs = {j['id']: j for j in _load('jobs.json', {'jobs': []})['jobs']}
interrupted = []
for j in sorted(jobs.values(), key=lambda j: j['created']):  # cut off by a restart: resumed below from their checkpoints
    if j['status'] in ('queued', 'running') and j.get('cmd'):
        counted = j['status'] == 'queued' and str(j.get('message', '')).startswith('Interrupted')
        j.update(status='queued', resumes=j.get('resumes', 0) + (0 if counted else 1), message='Resuming after a restart of the hub or THOR')
        interrupted.append(j['id'])


def save_registry():
    with lock:
        _save('registry.json', registry)


def save_jobs():
    with lock:
        _save('jobs.json', {'jobs': sorted(jobs.values(), key=lambda j: j['created'])[-200:]})


# ----------------------------------------------------------------- workspaces
# The hub uses Genome Canvas's password-free workspaces (one per /home user, plus named ones) as owners:
# every dataset and conversion belongs to the workspace that added it, and only that workspace lists,
# renames or removes it. The pages send the workspace selected in Genome Canvas as X-Vizhub-Workspace.
# Like Genome Canvas this separates people's lists; it is not access control (links open for everyone).

WORKSPACE_API = CONFIG.get('workspaceApi', 'http://127.0.0.1:8000/api/workspaces')
_workspaces = {'at': 0.0, 'list': []}


def workspace_list():
    if time.time() - _workspaces['at'] > 30:
        try:
            with urllib.request.urlopen(WORKSPACE_API, timeout=3) as r:
                _workspaces.update(at=time.time(), list=json.load(r).get('workspaces') or [])
        except Exception:
            pass  # Genome Canvas down: keep the last list
    return _workspaces['list']


def workspace_of(request: Request, required=True):
    wid = request.headers.get('x-vizhub-workspace', '').strip()
    ws = next((w for w in workspace_list() if w['id'] == wid), None) if wid else None
    if not ws and wid and not workspace_list() and re.fullmatch(r'(home|manual)-[0-9a-f]{18}', wid):
        ws = {'id': wid, 'name': wid}  # Genome Canvas unreachable: trust a well-formed id
    if not ws and required:
        raise HTTPException(409, 'Choose your workspace first (the same one you use in Genome Canvas)')
    return ws


def own(d, ws):
    return bool(ws) and d.get('owner') == ws['id']


def require_owner(d, ws):
    if not own(d, ws):
        raise HTTPException(403, f'“{d["title"]}” belongs to workspace {d.get("ownerName") or "?"}; only that workspace can change it')


# ----------------------------------------------------------------- helpers

def under_root(path: str):
    p = os.path.normpath(path)
    return any(p == r['path'] or p.startswith(r['path'].rstrip('/') + '/') for r in ROOTS)


def checked_path(path: str):
    if not path or not path.startswith('/'):
        raise HTTPException(400, 'Give an absolute path, e.g. /nfs/baldar/...')
    p = os.path.normpath(path)
    if not under_root(p):
        raise HTTPException(403, f'Only paths under {", ".join(r["path"] for r in ROOTS)} can be used')
    if not os.path.exists(p):
        raise HTTPException(404, f'{p} does not exist')
    return Path(p)


def slugify(text):
    s = re.sub(r'[^a-z0-9]+', '-', str(text).lower().replace('µ', 'u')).strip('-')[:60]
    return s or 'dataset'


def unique_id(*bases):
    """The first of the candidate names (slugified) not in use, else the first one numbered."""
    taken = {d['id'] for d in registry['datasets']} | {j.get('datasetId') for j in jobs.values() if j['status'] in ('queued', 'running')}
    cands = [slugify(b) for b in bases if b]
    for c in cands:
        if c not in taken:
            return c
    s, k = cands[0], 2
    while f'{s}-{k}' in taken:
        k += 1
    return f'{s}-{k}'


def dataset(id_):
    d = next((d for d in registry['datasets'] if d['id'] == id_), None)
    if not d:
        raise HTTPException(404, f'No dataset {id_}')
    return d


def view_url(d):
    return f'/{KIND_PATH[d["kind"]]}/{d["id"]}/'


def public(d):
    return {**d, 'url': view_url(d), 'publicUrl': CONFIG['publicUrl'].rstrip('/') + view_url(d)}


def folder_facts(root: Path, info):
    """Summary numbers for the hub cards."""
    facts = {}
    try:
        if info['type'] == 'viewer-slide':
            m = json.loads((root / 'slide.json').read_text())
            facts = dict(nCells=m.get('nCells'), nGenes=len(m.get('genes') or []), technology=m.get('technology'), species=m.get('species'))
        elif info['type'] == 'viewer-project':
            pj = json.loads((root / 'project.json').read_text()) if (root / 'project.json').exists() else {}
            facts = dict(nCells=sum(s.get('nCells') or 0 for s in info['details']['slides']), technology=pj.get('technology'),
                         species=pj.get('species'), description=pj.get('description'))
        elif info['type'] == 'crispr-screen':
            pj = json.loads((root / 'project.json').read_text())
            facts = dict(screenKind=pj.get('kind'), species=pj.get('species'), stats=pj.get('stats'), description=pj.get('description'))
    except Exception:
        pass
    try:
        facts['bytes'] = sum(f.stat().st_size for f in root.iterdir() if f.is_file())
    except Exception:
        pass
    return {k: v for k, v in facts.items() if v is not None}


def register_folder(root: Path, owner, *, title=None, source=None, job=None, id_=None):
    """Register one ready viewer folder (slide, project or CRISPR screen) for a workspace. Returns the dataset."""
    info = detect.inspect_folder(root)
    if info['type'] == 'viewer-collection' or not info.get('ready'):
        raise HTTPException(400, f'{root} is not a viewer dataset ({info["type"]})')
    with lock:
        existing = next((d for d in registry['datasets'] if d['root'] == str(root) and d.get('owner') == owner['id']), None)
        if existing:
            existing.update(folder_facts(root, info), updated=int(time.time()))
            if title:
                existing['title'] = title
            if job:
                existing['job'] = job
            save_registry()
            return existing
        if info['type'] == 'viewer-slide':
            layout, slides = 'slide', [dict(id=info['details'].get('id') or 'main', title=info['title'], dir='')]
        elif info['type'] == 'viewer-project':
            layout, slides = 'project', [dict(id=s['id'], title=s['title'], dir=s['id']) for s in info['details']['slides']]
        else:
            layout, slides = 'screen', []
        name = title or info.get('title') or root.name
        generated = root.name.endswith(detect.OUTPUT_SUFFIX) or STATE in root.parents
        d = dict(id=id_ or (unique_id(name, f'{name}-{KIND_PATH[info["kind"]]}') if generated else unique_id(root.name, name, f'{root.name}-{KIND_PATH[info["kind"]]}')), kind=info['kind'], title=name, root=str(root), layout=layout, slides=slides,
                 source=source or str(root), added=int(time.time()), job=job, owner=owner['id'], ownerName=owner['name'],
                 **folder_facts(root, info))
        registry['datasets'].append(d)
        save_registry()
        return d


# ----------------------------------------------------------------- conversion jobs

# At most MAX_JOBS conversions run at once (server load); the others wait in a first-in, first-out queue.
MAX_JOBS = int(CONFIG.get('maxJobs', 8))
waiting = deque()
running = set()
procs = {}


def pump():
    """Start queued conversions while fewer than MAX_JOBS run; tell the waiting ones their place in line."""
    with lock:
        while waiting and len(running) < MAX_JOBS:
            jid = waiting.popleft()
            if jobs.get(jid, {}).get('status') != 'queued':
                continue
            running.add(jid)
            threading.Thread(target=run_job, args=(jid,), daemon=True).start()
        for k, jid in enumerate(waiting):
            jobs[jid]['message'] = (f'Waiting: {k} conversion{"s" if k != 1 else ""} ahead in the queue · ' if k else 'Next in the queue · ') + \
                f'{len(running)} running, at most {MAX_JOBS} at once'
        save_jobs()


def enqueue(jid):
    with lock:
        waiting.append(jid)
    pump()


def job_public(j, log_lines=0):
    out = {k: v for k, v in j.items() if k != 'cmd'}
    if log_lines:
        try:
            with open(STATE / 'jobs' / f'{j["id"]}.log', 'rb') as f:
                f.seek(0, 2)
                size = f.tell()
                f.seek(max(0, size - 64_000))
                text = f.read().decode('utf-8', 'replace')
            out['log'] = [l for l in text.splitlines() if not l.startswith('@@progress ')][-log_lines:]
        except Exception:
            out['log'] = []
    return out


def run_job(jid):
    j = jobs[jid]
    try:
        if j['status'] == 'cancelled':
            return
        j.update(status='running', started=j.get('started') if j.get('resumes') else int(time.time()), message='Starting')
        j['started'] = j['started'] or int(time.time())
        save_jobs()
        logf = open(STATE / 'jobs' / f'{jid}.log', 'a', buffering=1)  # appended: a resumed job keeps its earlier log
        if j.get('resumes'):
            logf.write(f'\n===== resumed after a restart ({time.strftime("%Y-%m-%d %H:%M")}) =====\n')
        env = {**os.environ, 'PYTHONUNBUFFERED': '1', 'OMP_NUM_THREADS': '8', 'NUMBA_NUM_THREADS': '8'}
        try:
            proc = subprocess.Popen(j['cmd'], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
                                    cwd=str(HERE / 'converters'), env=env, start_new_session=True)
        except Exception as e:
            j.update(status='failed', message=str(e), finished=int(time.time()))
            save_jobs()
            return
        procs[jid] = proc
        last_save, last_line = 0.0, ''
        for line in proc.stdout:
            logf.write(line)
            line = line.rstrip('\n')
            if line.startswith('@@notice '):
                note = line[len('@@notice '):].strip()
                j['notices'] = [x for x in j.get('notices', []) if x != note][-2:] + [note]
                save_jobs()
                continue
            if line.startswith('@@progress '):
                parts = line.split(' ', 2)
                try:
                    j['progress'] = max(j.get('progress', 0), min(100, int(parts[1])))
                except ValueError:
                    pass
                j['message'] = parts[2] if len(parts) > 2 else ''
            elif line.strip():
                last_line = line.strip()
            if time.time() - last_save > 2:
                save_jobs()
                last_save = time.time()
        code = proc.wait()
        procs.pop(jid, None)
        logf.close()
        if j['status'] == 'cancelled':
            save_jobs()
            return
        if code < 0:
            # stopped by a signal (systemd stopping or restarting the hub, a reboot): not a failure. The job waits
            # in the queue and continues from its checkpoints when the hub starts again (or in a minute if the
            # hub keeps running, e.g. the converter alone was stopped).
            j.update(status='queued', resumes=j.get('resumes', 0) + 1,
                     message='Interrupted: continues from its last finished step when the hub runs again')
            save_jobs()
            threading.Timer(60, lambda: enqueue(jid) if jobs.get(jid, {}).get('status') == 'queued' else None).start()
            return
        if code != 0:
            m = detect.read_manifest(Path(j['out'])) or {}
            j.update(status='failed', finished=int(time.time()),
                     message=(m.get('error') or last_line or f'converter exited with code {code}').strip().splitlines()[-1][:500])
            save_jobs()
            return
        try:
            d = register_folder(Path(j['out']), {'id': j.get('owner'), 'name': j.get('ownerName')}, title=j.get('title'),
                                source=j['source'], job=jid, id_=j['datasetId'])
            j.update(status='done', progress=100, finished=int(time.time()), message='Converted and loaded', datasetId=d['id'],
                     url=view_url(d))
        except Exception as e:
            j.update(status='failed', finished=int(time.time()), message=f'converted, but could not load: {getattr(e, "detail", e)}')
        save_jobs()
    finally:
        with lock:
            running.discard(jid)
        pump()


def start_job(src: Path, info, body, owner):
    out = detect.output_dir(src)
    if not os.access(src.parent, os.W_OK):
        out = STATE / 'converted' / f'{slugify(src.name)}-{uuid.uuid4().hex[:6]}'
    title = (body.get('title') or '').strip() or info.get('title') or src.name
    did = unique_id(title, f'{title}-{KIND_PATH.get(body.get("kind") or info.get("kind") or "", "data")}')
    o = body.get('options') or {}
    kind = body.get('kind') or 'auto'
    cmd = [PYTHON, '-u', str(HERE / 'converters' / 'convert.py'), str(src), str(out), '--id', did, '--title', title,
           '--kind', kind, '--rscript', ','.join(CONFIG.get('rscript') or ['Rscript'])]
    flag = {'default': '--default', 'pixelSize': '--pixel-size', 'spotDiameter': '--spot-diameter', 'assay': '--assay',
            'matrix': '--matrix', 'screenKind': '--screen-kind', 'species': '--species', 'technology': '--technology',
            'fdr': '--fdr', 'feature': '--feature', 'geneActivity': '--gene-activity'}
    for k, f in flag.items():
        v = o.get(k)
        if v not in (None, '', 'auto'):
            cmd += [f, str(v)]
    if o.get('computeUmap'):
        cmd.append('--compute-umap')
    if o.get('noUmap'):
        cmd.append('--no-umap')
    if o.get('noFragments'):
        cmd.append('--no-fragments')
    cmd += ['--fragment-workers', str(int(CONFIG.get('fragmentWorkers', 8)))]
    if body.get('reconvert'):
        cmd.append('--fresh')  # "Convert again": ignore checkpoints of an earlier attempt
    if o.get('gtf'):
        cmd += ['--gtf', str(o['gtf'])]
    for spec in o.get('embedding') or []:
        cmd += ['--embedding', spec]
    jid = uuid.uuid4().hex[:10]
    j = dict(id=jid, datasetId=did, owner=owner['id'], ownerName=owner['name'], source=str(src), out=str(out), type=info['type'], kind=kind if kind != 'auto' else info.get('kind'),
             title=title, status='queued', progress=0, message='Waiting for a free conversion slot', created=int(time.time()), cmd=cmd)
    with lock:
        jobs[jid] = j
        save_jobs()
    enqueue(jid)
    return j


# ----------------------------------------------------------------- app

app = FastAPI(title='Omics Canvas', docs_url='/hub/api/docs', openapi_url='/hub/api/openapi.json')


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    return JSONResponse({'error': exc.detail}, status_code=exc.status_code)


@app.exception_handler(Exception)
async def any_error(request: Request, exc: Exception):
    return JSONResponse({'error': f'{type(exc).__name__}: {exc}'}, status_code=500)


def page(name, **subst):
    html = (STATIC / name).read_text()
    for k, v in subst.items():
        html = html.replace(f'__{k}__', v)
    return HTMLResponse(html, headers={'cache-control': 'no-cache'})


@app.get('/')
def root():
    return RedirectResponse('/hub/')


@app.get('/hub')
@app.get('/hub/')
def hub_page():
    return page('hub.html')


def viewer_routes(kind, path):
    html = 'crispr.html' if kind == 'crispr' else 'viewer.html'

    def view():
        return page(html, KIND=kind)

    def view_dataset(id_: str):
        return page(html, KIND=kind)

    def slashless():
        return RedirectResponse(f'/{path}/')

    def slashless_dataset(id_: str):
        return RedirectResponse(f'/{path}/{id_}/')

    app.add_api_route(f'/{path}', slashless, methods=['GET'])
    app.add_api_route(f'/{path}/', view, methods=['GET'])
    app.add_api_route(f'/{path}/{{id_}}', slashless_dataset, methods=['GET'])
    app.add_api_route(f'/{path}/{{id_}}/', view_dataset, methods=['GET'])


for _kind, _path in KIND_PATH.items():
    viewer_routes(_kind, _path)


@app.get('/hub/static/{name:path}')
def static(name: str):
    p = (STATIC / posixpath.normpath(name)).resolve()
    if STATIC not in p.parents or not p.is_file():
        raise HTTPException(404, 'not found')
    return FileResponse(p, headers={'cache-control': 'public, max-age=86400' if '.v' in name or 'chunk' in name else 'no-cache'})


@app.get('/hub/api/health')
def health():
    return {'ok': True, 'datasets': len(registry['datasets'])}


def _probe(url):
    try:
        with urllib.request.urlopen(url, timeout=2) as r:
            return r.status < 500
    except urllib.error.HTTPError as e:
        return e.code < 500
    except Exception:
        return False


@app.get('/hub/api/services')
def services():
    return [dict(id=s['id'], name=s['name'], url=s['url'], ok=_probe(s['health'])) for s in CONFIG.get('services', [])]


@app.get('/hub/api/config')
def config():
    return dict(roots=ROOTS, publicUrl=CONFIG['publicUrl'], kinds=KIND_PATH)


# ---------- file browser

@app.get('/hub/api/browse')
def browse(path: str = '', hidden: bool = False):
    if not path:
        return dict(path='', parent=None, roots=ROOTS, entries=[dict(name=r['label'], path=r['path'], dir=True, hint=None) for r in ROOTS])
    p = checked_path(path)
    if not p.is_dir():
        p = p.parent
    entries = []
    try:
        it = list(os.scandir(p))
    except PermissionError:
        raise HTTPException(403, f'No permission to read {p}')
    names = {e.name for e in it}
    dirs_checked = 0
    for e in sorted(it, key=lambda e: (not e.is_dir(follow_symlinks=True), e.name.lower())):
        if not hidden and e.name.startswith('.'):
            continue
        try:
            is_dir = e.is_dir(follow_symlinks=True)
            st = e.stat(follow_symlinks=True)
        except OSError:
            continue
        name_l = e.name.lower()
        hint = None
        if is_dir:
            if e.name.endswith(detect.OUTPUT_SUFFIX):
                hint = 'output'
            elif dirs_checked < 400:
                dirs_checked += 1
                # os.path.exists is False (not an error) for folders we may not read
                if os.path.exists(os.path.join(e.path, 'slide.json')):
                    hint = 'viewer'
                elif os.path.exists(os.path.join(e.path, 'experiment.xenium')):
                    hint = 'xenium'
                elif os.path.exists(os.path.join(e.path, 'project.json')):
                    hint = 'project'
                elif any(os.path.exists(os.path.join(e.path, 'spatial', n)) for n in detect.SPACERANGER_POSITIONS):
                    hint = 'visium'
        elif name_l.endswith('.h5ad'):
            hint = 'h5ad'
        elif name_l.endswith('.rds'):
            hint = 'rds'
        elif name_l.endswith('.h5mu'):
            hint = 'h5mu'
        elif name_l.endswith(('.h5', '.loom')):
            hint = name_l.rsplit('.', 1)[1]
        elif name_l.endswith(detect.TABLE_EXT):
            hint = 'table'
        entries.append(dict(name=e.name, path=e.path, dir=is_dir, size=None if is_dir else st.st_size, mtime=int(st.st_mtime),
                            hint=hint, converted=(e.name + detect.OUTPUT_SUFFIX) in names))
    parent = str(p.parent) if under_root(str(p.parent)) and str(p) not in [r['path'] for r in ROOTS] else ''
    return dict(path=str(p), parent=parent, entries=entries, writable=os.access(p, os.W_OK))


@app.get('/hub/api/inspect')
def inspect(path: str, request: Request):
    ws = workspace_of(request, required=False)
    p = checked_path(path)
    info = detect.inspect(p)
    root = str(p) if info.get('ready') else (info.get('output') or {}).get('path')
    mine = [d for d in registry['datasets'] if own(d, ws)]
    info['registered'] = [public(d) for d in mine if d['root'] == root or d.get('source') == str(p)]
    if info['type'] == 'viewer-collection':
        roots = {d['root']: d for d in mine}
        for it in info['details']['items']:
            it['registered'] = it['path'] in roots
    return info


# ---------- datasets

@app.get('/hub/api/datasets')
def list_datasets(request: Request, kind: str = ''):
    """The datasets of the caller's workspace (all viewers, or one)."""
    ws = workspace_of(request)
    return [public(d) for d in sorted(registry['datasets'], key=lambda d: -d.get('added', 0))
            if own(d, ws) and (not kind or d['kind'] == kind)]


@app.get('/hub/api/summary')
def summary(request: Request):
    """Dataset counts per viewer: the caller's and everyone's (hub cards)."""
    ws = workspace_of(request, required=False)
    out = {k: {'mine': 0, 'all': 0} for k in KIND_PATH}
    for d in registry['datasets']:
        out[d['kind']]['all'] += 1
        out[d['kind']]['mine'] += own(d, ws)
    running = sum(1 for j in jobs.values() if j['status'] in ('queued', 'running') and ws and j.get('owner') == ws['id'])
    return {'kinds': out, 'running': running, 'workspace': ws}


@app.post('/hub/api/datasets')
async def add_dataset(request: Request):
    ws = workspace_of(request)
    body = await request.json()
    src = checked_path(body.get('path', ''))
    info = detect.inspect(src)
    title = (body.get('title') or '').strip() or None
    if info['type'] == 'unsupported':
        raise HTTPException(400, info['details'].get('reason') or 'Unsupported file')
    if info['type'] == 'viewer-collection':
        picked = set(body.get('items') or [it['path'] for it in info['details']['items']])
        added = [public(register_folder(Path(it['path']), ws)) for it in info['details']['items'] if it['path'] in picked]
        return {'datasets': added}
    if info.get('ready'):
        return {'datasets': [public(register_folder(src, ws, title=title))]}
    out = info.get('output') or {}
    if out.get('converted') and not body.get('reconvert'):
        m = out.get('manifest') or {}
        d = register_folder(Path(out['path']), ws, title=title or m.get('title'), source=str(src), id_=None)
        return {'datasets': [public(d)], 'reused': True}
    running = next((j for j in jobs.values() if j['source'] == str(src) and j['status'] in ('queued', 'running')), None)
    if running:
        if running.get('owner') == ws['id']:
            return {'job': job_public(running)}
        raise HTTPException(409, f'{running.get("ownerName")} is converting this file right now; add it again when that finishes')
    return {'job': job_public(start_job(src, info, body, ws))}


# ---------- combined datasets: several samples (slides of the workspace's datasets) shown as one project

def slide_dir(d, s):
    """The folder of one slide: its own root in a combined dataset, else under the dataset's root."""
    return Path(s['root']) if s.get('root') else Path(d['root']) / s['dir']


def combined_slides(items, ws, kind):
    """[{dataset, slide, title}] (the caller's datasets of this viewer) -> slides of a combined dataset."""
    if not isinstance(items, list) or len(items) < 1:
        raise HTTPException(400, 'Pick at least one sample')
    out, taken = [], set()
    for it in items[:200]:
        src = dataset(str(it.get('dataset', '')))
        require_owner(src, ws)
        if src['kind'] != kind or src['layout'] == 'screen':
            raise HTTPException(400, f'“{src["title"]}” is not a {KIND_PATH[kind]} dataset')
        s = next((x for x in src['slides'] if x['id'] == it.get('slide')), None) or (src['slides'][0] if len(src['slides']) == 1 and not it.get('slide') else None)
        if not s:
            raise HTTPException(400, f'“{src["title"]}” has no sample {it.get("slide")!r}')
        folder = slide_dir(src, s)
        if not (folder / 'slide.json').is_file():
            raise HTTPException(400, f'{folder} has no slide.json')
        title = (str(it.get('title') or '').strip() or (s['title'] if len(src['slides']) > 1 else src['title']))[:120]
        sid, k = slugify(title), 2
        while sid in taken:
            sid, k = f'{slugify(title)}-{k}', k + 1
        taken.add(sid)
        try:
            m = json.loads((folder / 'slide.json').read_text())
        except Exception:
            m = {}
        out.append(dict(id=sid, title=title, dir=sid, root=str(folder), fromDataset=src['id'], fromTitle=src['title'], fromSlide=s['id'],
                        nCells=m.get('nCells'), technology=m.get('technology'), species=m.get('species')))
    return out


def combined_facts(slides):
    uniq = lambda k: ', '.join(dict.fromkeys(str(s[k]) for s in slides if s.get(k)))
    return dict(nCells=sum(s.get('nCells') or 0 for s in slides), technology=uniq('technology') or None, species=uniq('species') or None,
                source='Combined: ' + ', '.join(dict.fromkeys(s['fromTitle'] for s in slides)))


@app.post('/hub/api/combined')
async def add_combined(request: Request):
    """A new dataset made of samples of the caller's datasets (e.g. time points, sections); nothing is copied."""
    ws = workspace_of(request)
    body = await request.json()
    kind = body.get('kind')
    if kind not in ('spatial', 'singlecell'):
        raise HTTPException(400, 'Combined datasets are for the spatial and single-cell viewers')
    title = str(body.get('title') or '').strip()[:300]
    if not title:
        raise HTTPException(400, 'Give the combined dataset a name')
    slides = combined_slides(body.get('slides'), ws, kind)
    with lock:
        did = unique_id(title, f'{title}-{KIND_PATH[kind]}')
        d = dict(id=did, kind=kind, title=title, description=str(body.get('description') or '')[:2000], root=f'combined:{did}',
                 layout='project', combined=True, slides=slides, added=int(time.time()), job=None,
                 owner=ws['id'], ownerName=ws['name'], **combined_facts(slides))
        registry['datasets'].append(d)
        save_registry()
    return public(d)


@app.patch('/hub/api/datasets/{id_}')
async def edit_dataset(id_: str, request: Request):
    body = await request.json()
    d = dataset(id_)
    require_owner(d, workspace_of(request))
    slides = combined_slides(body['slides'], workspace_of(request), d['kind']) if d.get('combined') and 'slides' in body else None
    with lock:
        for k, n in (('title', 300), ('description', 2000)):
            if k in body:
                d[k] = str(body[k])[:n]
        if slides is not None:
            d['slides'] = slides
            d.update(combined_facts(slides))
        save_registry()
    return public(d)


@app.delete('/hub/api/datasets/{id_}')
def remove_dataset(id_: str, request: Request, files: bool = False):
    d = dataset(id_)
    require_owner(d, workspace_of(request))
    removed = None
    if d.get('combined'):
        files = False  # it only points at other datasets' files
    root = Path(d['root'])
    # other datasets showing these files: the same folder in another workspace, or a combined dataset's sample
    others = [x for x in registry['datasets'] if x['id'] != id_ and (x['root'] == d['root'] or any(
        sl.get('root') and (Path(sl['root']) == root or root in Path(sl['root']).parents) for sl in x['slides']))]
    if files and others:
        files = False  # another workspace still shows these files: only unregister
    if files:
        # only folders this hub generated (with a vizhub.json manifest), never source data
        if detect.read_manifest(root) and (root.name.endswith(detect.OUTPUT_SUFFIX) or STATE in root.parents):
            shutil.rmtree(root)
            removed = str(root)
        else:
            raise HTTPException(400, 'Only folders generated by the hub can be deleted; this one is source data')
    with lock:
        registry['datasets'] = [x for x in registry['datasets'] if x['id'] != id_]
        save_registry()
    return {'removed': id_, 'deletedFolder': removed, 'keptFor': sorted({x.get('ownerName') or '?' for x in others})}


# ---------- jobs

@app.get('/hub/api/jobs')
def list_jobs(request: Request, kind: str = ''):
    ws = workspace_of(request)
    mine = [j for j in jobs.values() if j.get('owner') == ws['id'] and (not kind or j.get('kind') == kind)]
    return [job_public(j) for j in sorted(mine, key=lambda j: -j['created'])[:50]]


@app.get('/hub/api/jobs/{jid}')
def get_job(jid: str, log: int = 40):
    j = jobs.get(jid)
    if not j:
        raise HTTPException(404, 'No such job')
    return job_public(j, log_lines=log)


@app.post('/hub/api/jobs/{jid}/cancel')
def cancel_job(jid: str, request: Request):
    j = jobs.get(jid)
    if not j:
        raise HTTPException(404, 'No such job')
    if j.get('owner') != workspace_of(request)['id']:
        raise HTTPException(403, 'Only the workspace that started this conversion can stop it')
    if j['status'] in ('queued', 'running'):
        j.update(status='cancelled', message='Cancelled · converting the same file again continues from its checkpoints', finished=int(time.time()))
        with lock:
            if jid in waiting:
                waiting.remove(jid)
        proc = procs.get(jid)
        if proc:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except Exception:
                proc.terminate()
        save_jobs()
    return job_public(j)


@app.delete('/hub/api/jobs/{jid}')
def forget_job(jid: str, request: Request):
    j = jobs.get(jid)
    if j and j.get('owner') == workspace_of(request)['id'] and j['status'] not in ('queued', 'running'):
        with lock:
            jobs.pop(jid, None)
            save_jobs()
    return {'ok': True}


# ---------- viewer API (the ATHENA viewers' /api/<kind>/projects and /api/data)

@app.get('/hub/api/projects')
def projects(kind: str, request: Request, include: str = ''):
    """Viewer picker: the caller's datasets of this viewer, plus `include` (a dataset opened by its link)."""
    ws = workspace_of(request, required=False)
    out = []
    for d in sorted(registry['datasets'], key=lambda d: d['title'].lower()):
        if d['kind'] != kind or not (own(d, ws) or d['id'] == include):
            continue
        base = f'/hub/api/data/{d["id"]}'
        extra = {}
        if d['layout'] == 'screen':
            try:
                extra = json.loads((Path(d['root']) / 'project.json').read_text())
            except Exception:
                extra = {}
        elif d.get('combined'):
            extra = {'description': d['description']} if d.get('description') else {}
        elif d['layout'] == 'project':
            try:
                pj = json.loads((Path(d['root']) / 'project.json').read_text())
                extra = {k: pj[k] for k in ('description', 'citation', 'sourceUrl', 'technology', 'species', 'tissue', 'panel', 'doi') if k in pj}
            except Exception:
                extra = {}
        out.append({**extra, 'id': d['id'], 'title': d['title'], 'url': view_url(d), 'base': base, 'mine': own(d, ws),
                    'ownerName': d.get('ownerName'),
                    'technology': extra.get('technology') or d.get('technology'), 'species': extra.get('species') or d.get('species'),
                    'slides': [dict(id=s['id'], title=s['title'], base=f'{base}/{s["dir"]}'.rstrip('/'),
                                    **({'description': f'from {s["fromTitle"]}'} if s.get('fromTitle') else {})) for s in d['slides']],
                    'downloads': []})
    return out


RANGE = re.compile(r'bytes=(\d*)-(\d*)')


# --------------------------------------------------------------- enhancer arcs for Genome Canvas

_TSS = {}


def tss_table(genome: str):
    """gene symbol -> (chrom, 0-based TSS) from resources/genes.<genome>.json.gz ({symbol: [chr, start, end, strand]})."""
    if genome not in _TSS:
        f = HERE / 'resources' / f'genes.{genome}.json.gz'
        if not f.is_file():
            return None
        import gzip
        genes = json.loads(gzip.decompress(f.read_bytes()))
        _TSS[genome] = {g: (c, int(s) - 1 if strand == '+' else int(e) - 1) for g, (c, s, e, strand) in genes.items()}
    return _TSS[genome]


@app.get('/hub/api/arcs/{id_}/{which}.bedpe')
def enhancer_arcs(id_: str, which: str, genome: str = 'hg38'):
    """Enhancer -> target-gene TSS links of an enhancer screen as BEDPE, for a Genome Canvas loop track
    (as the ATHENA locus view): `significant` or `all` tested pairs; score = |log2FC| (arc height)."""
    if which not in ('significant', 'all'):
        raise HTTPException(404, 'significant or all')
    d = dataset(id_)
    tss = tss_table(genome)
    if d['kind'] != 'crispr' or tss is None:
        raise HTTPException(404, f'no enhancer links for {genome}')
    p = Path(d['root']) / 'tests.json'
    if not p.is_file():
        raise HTTPException(404, 'not an enhancer screen')
    t = json.loads(p.read_text())
    col = {c: i for i, c in enumerate(t['columns'])}
    arcs = []
    for r in t['rows']:
        chrom, start, end, gene = r[col['chrom']], r[col['start']], r[col['end']], r[col['gene']]
        sig = bool(r[col['significant']])
        if not chrom or (which == 'significant' and not sig):
            continue
        chrom = chrom if str(chrom).startswith('chr') else f'chr{chrom}'
        hit = tss.get(gene)
        if not hit or hit[0] != chrom:
            continue
        lfc = float(r[col['log2fc']])
        name = f'{gene} log2FC {lfc:+.2f} · FDR {10 ** -float(r[col["nlog10padj"]]):.1e}' if sig else f'{gene} log2FC {lfc:+.2f} (n.s.)'
        arcs.append((chrom, int(start), int(end), chrom, hit[1], hit[1] + 1, name, f'{abs(lfc):.4f}', '.', '.'))
    arcs.sort(key=lambda a: (a[0], a[1], a[4]))
    body = ''.join('\t'.join(map(str, a)) + '\n' for a in arcs)
    # readable from Genome Canvas on another origin (e.g. the Mac app's local backend)
    return Response(body, media_type='text/plain', headers={'cache-control': 'private, max-age=300', 'access-control-allow-origin': '*'})


@app.get('/hub/api/data/{id_}/{rest:path}')
def data(id_: str, rest: str, request: Request):
    d = dataset(id_)
    rel = posixpath.normpath(rest)
    if rel.startswith(('..', '/')) or '/../' in rel:
        raise HTTPException(400, 'bad path')
    if d.get('combined'):  # /<sample id>/<file> -> that sample's own folder
        sid, _, rest_ = rel.partition('/')
        sl = next((x for x in d['slides'] if x['id'] == sid), None)
        if not sl:
            raise HTTPException(404, f'no sample {sid}')
        p = Path(sl['root']) / rest_
    else:
        p = Path(d['root']) / rel
    if not p.is_file():
        raise HTTPException(404, f'{rel} not found')
    size = p.stat().st_size
    headers = {'accept-ranges': 'bytes', 'cache-control': 'private, max-age=300'}
    m = RANGE.fullmatch(request.headers.get('range', '').strip())
    if not m:
        return FileResponse(p, headers=headers)
    a, b = m.groups()
    if a == '':
        start, end = max(0, size - int(b)), size - 1
    else:
        start, end = int(a), min(size - 1, int(b) if b else size - 1)
    if start > end or start >= size:
        return Response(status_code=416, headers={'content-range': f'bytes */{size}'})
    length = end - start + 1
    if length <= 32 << 20:
        with open(p, 'rb') as f:
            f.seek(start)
            body = f.read(length)
        return Response(body, status_code=206, media_type='application/octet-stream',
                        headers={**headers, 'content-range': f'bytes {start}-{end}/{size}'})

    def chunks():
        with open(p, 'rb') as f:
            f.seek(start)
            left = length
            while left:
                buf = f.read(min(left, 1 << 20))
                if not buf:
                    break
                left -= len(buf)
                yield buf
    return StreamingResponse(chunks(), status_code=206, media_type='application/octet-stream',
                             headers={**headers, 'content-range': f'bytes {start}-{end}/{size}', 'content-length': str(length)})


for _jid in interrupted:  # conversions cut off by a restart continue from their checkpoints
    enqueue(_jid)


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host=CONFIG.get('host', '127.0.0.1'), port=int(CONFIG.get('port', 8893)), proxy_headers=True,
                forwarded_allow_ips='127.0.0.1', log_level='info', access_log=False)
