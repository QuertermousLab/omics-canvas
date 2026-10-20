#!/usr/bin/env python3
"""Omics Canvas: the landing page at :8892/hub/, the ATHENA-style spatial and single-cell
viewers (/spatial/, /single-cell/) and CRISPR screen explorer (/crispr/), and the API behind them.

Datasets are found on /nfs with the file browser. Files already in the ATHENA viewer format are
registered as they are; anything else (h5ad, rds, 10x h5, loom, Xenium bundles, CRISPR result tables)
is converted by converters/convert.py in a background job (progress shown in the hub) into
<source>.vizhub/ next to the source, then registered. The registry is state/registry.json.

Run: venv/bin/python server.py   (the Apache gateway on :8892 proxies /hub/, /spatial/, /single-cell/, /crispr/ here)
"""
import hashlib
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
from urllib.parse import quote

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


def viewer_kind(info, wanted):
    """The viewer a ready folder is listed in: `wanted` (the page it was added from) when the data suits it — a slide
    with tissue coordinates and an embedding opens in either viewer — else what the folder is."""
    if wanted in ('spatial', 'singlecell') and info['type'] == 'viewer-slide' and info['kind'] in ('spatial', 'singlecell'):
        meta = info.get('details') or {}
        if wanted == 'spatial' and meta.get('spatial', True) is not False:
            return 'spatial'
        if wanted == 'singlecell' and meta.get('embeddings'):
            return 'singlecell'
    return info['kind']


def register_folder(root: Path, owner, *, title=None, source=None, job=None, id_=None, kind=None):
    """Register one ready viewer folder (slide, project or CRISPR screen) for a workspace, in the viewer `kind` asks
    for when the data suits it (viewer_kind). Returns the dataset."""
    info = detect.inspect_folder(root)
    if info['type'] == 'viewer-collection' or not info.get('ready'):
        raise HTTPException(400, f'{root} is not a viewer dataset ({info["type"]})')
    info['kind'] = viewer_kind(info, kind)
    with lock:
        existing = next((d for d in registry['datasets'] if d['root'] == str(root) and d.get('owner') == owner['id']
                         and d['kind'] == info['kind']), None)
        if existing:
            facts = folder_facts(root, info)
            if existing.get('descriptionEdited'):
                facts.pop('description', None)
            if existing.get('edited'):  # its samples were edited here: keep their totals
                facts.pop('nCells', None)
            existing.update(facts, updated=int(time.time()))
            if title:
                existing['title'] = title
            if job:
                existing['job'] = job
            save_registry()
            record_library(existing)
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
        record_library(d)
        return d


# ----------------------------------------------------------------- shared library
# Every dataset any workspace has loaded, converted or combined, listed for everyone (state/library.json) so that
# others add it to their own list in one click instead of looking for the files. An entry outlives the datasets
# made from it and follows its files: it is dropped when its folder is gone (deleted, or a combined dataset's samples
# all gone) and updated when the files were written again (converted again) or its author renamed or edited it.
# Checked whenever the library is read (at most every LIBRARY_TTL seconds) and hourly.

library = _load('library.json', {'entries': []})
LIBRARY_TTL = 30
_library_checked = {'at': 0.0}
SNAPSHOT_KEYS = ('kind', 'title', 'root', 'source', 'layout', 'combined', 'description', 'nCells', 'nGenes', 'technology', 'species',
                 'screenKind', 'stats', 'bytes')
SLIDE_KEYS = ('id', 'title', 'dir', 'root', 'fromTitle', 'nCells', 'technology', 'species', 'screenKind')


def save_library():
    with lock:
        _save('library.json', library)


def library_key(d):
    return f'combined:{d["id"]}' if d.get('combined') else d['root']


def library_snapshot(d):
    """What the library keeps of a dataset: its folder's samples (a project's edits are its workspace's own view)."""
    snap = {k: d[k] for k in SNAPSHOT_KEYS if d.get(k) not in (None, '')}
    slides = d['slides'] if d.get('combined') else d.get('folderSlides') or d['slides']
    snap['slides'] = [{k: s[k] for k in SLIDE_KEYS if s.get(k) is not None} for s in slides]
    if d.get('edited') and not d.get('combined'):
        snap.update(folder_facts(Path(d['root']), detect.inspect_folder(Path(d['root']))))
    return snap


def folder_stamp(root: Path, layout):
    """Changes when a folder's viewer files are written again: newest mtime of its manifest and metadata files."""
    names = ['vizhub.json', 'slide.json', 'project.json']
    if layout == 'project':
        try:
            names += [f'{c.name}/slide.json' for c in os.scandir(root) if c.is_dir() and not c.name.startswith('.')][:200]
        except OSError:
            pass
    stamp = 0
    for n in names:
        try:
            stamp = max(stamp, int((root / n).stat().st_mtime))
        except OSError:
            pass
    return stamp


def sample_marker(kind):
    return 'project.json' if kind == 'crispr' else 'slide.json'


def record_library(d, now=None):
    """Remember a dataset in the shared library, or update its entry (its author's name and samples win)."""
    if d.get('fromLibrary'):  # a copy of a shared combined dataset: one more user of the original
        with lock:
            e = next((x for x in library['entries'] if x['key'] == d['fromLibrary']), None)
            if e and d.get('ownerName') and d['ownerName'] not in e.setdefault('users', []):
                e['users'].append(d['ownerName'])
                save_library()
        return
    key, now = library_key(d), now or int(time.time())
    with lock:
        e = next((x for x in library['entries'] if x['key'] == key), None)
        snap = library_snapshot(d)
        if e is None:
            e = dict(key=key, datasetId=d['id'], owner=d.get('owner'), ownerName=d.get('ownerName'), added=d.get('added') or now, users=[])
            library['entries'].append(e)
            e.update(snap)
        elif e.get('owner') == d.get('owner'):
            e.update(snap, datasetId=d['id'])
            if d.get('added', 0) >= now - 5:
                e.pop('hidden', None)  # its author added it again
        else:  # another workspace's dataset of the same folder: only the files' facts
            e.update({k: v for k, v in snap.items() if k not in ('title', 'description')})
        if d.get('ownerName') and d['ownerName'] not in e.setdefault('users', []):
            e['users'].append(d['ownerName'])
        if not d.get('combined'):
            stamp = folder_stamp(Path(d['root']), d['layout'])
            if stamp != e.get('stamp'):
                e.update(stamp=stamp, updated=now)
        if e.get('owner') == d.get('owner'):
            e['updated'] = now
        save_library()


def _sync_entry(e, reg, busy, now):
    """Bring one entry up to date with its files and its author's dataset; False when it should be dropped."""
    if e.get('combined'):
        author = next((d for d in reg if d['id'] == e.get('datasetId') and d.get('owner') == e.get('owner')), None)
        snap = library_snapshot(author) if author else {'slides': e['slides']}
        marker = sample_marker(e['kind'])
        slides = [s for s in snap['slides'] if s.get('root') and (Path(s['root']) / marker).is_file()]
        if not slides:
            return False
        if len(slides) != len(snap['slides']):  # samples whose files are gone
            snap.update(slides=slides, **{k: v for k, v in combined_facts(slides).items() if k != 'source'})
        if any(e.get(k) != v for k, v in snap.items()):
            e.update(snap, updated=now)
        return True
    root = Path(e['root'])
    if e['root'] in busy:  # being converted again right now
        e['updating'] = True
        return True
    e.pop('updating', None)
    if not (root / ('slide.json' if e['layout'] == 'slide' else 'project.json')).is_file():
        return False
    stamp = folder_stamp(root, e['layout'])
    if stamp != e.get('stamp'):
        info = detect.inspect_folder(root)
        if not info.get('ready') or info['type'] == 'viewer-collection':
            return False
        if info['type'] == 'viewer-slide':
            slides = [dict(id=info['details'].get('id') or 'main', title=info['title'], dir='')]
        elif info['type'] == 'viewer-project':
            slides = [dict(id=s['id'], title=s['title'], dir=s['id'], **({'nCells': s['nCells']} if s.get('nCells') else {})) for s in info['details']['slides']]
        else:
            slides = []
        first = e.get('stamp', -1) == -1  # recorded without reading the folder: not an update
        e.update(folder_facts(root, info), slides=slides, kind=info['kind'], stamp=stamp, updated=e.get('updated', now) if first else now)
    author = next((d for d in reg if d['root'] == e['root'] and d.get('owner') == e.get('owner')), None)
    if author:
        for k in ('title', 'description'):
            if author.get(k) and author[k] != e.get(k):
                e[k] = author[k]
                e['updated'] = now
        e['datasetId'] = author['id']
    # a converted folder whose source file changed or went away since: still viewable, flagged
    m = detect.read_manifest(root)
    e.pop('sourceChanged', None)
    e.pop('sourceMissing', None)
    if m and m.get('sourceSize') is not None and e.get('source'):
        try:
            st = os.stat(e['source'])
            if st.st_size != m.get('sourceSize') or int(st.st_mtime) != m.get('sourceMtime'):
                e['sourceChanged'] = True
        except OSError:
            e['sourceMissing'] = True
    return True


def library_sync(force=False):
    if not force and time.time() - _library_checked['at'] < LIBRARY_TTL:
        return
    with lock:
        _library_checked['at'] = time.time()
        now = int(time.time())
        busy = {j['out'] for j in jobs.values() if j['status'] in ('queued', 'running')}
        before = json.dumps(library, sort_keys=True)
        kept = [e for e in library['entries'] if _sync_entry(e, registry['datasets'], busy, now)]
        # a source converted again into another folder (state/converted/ when its folder is read-only): the newest
        newest = {}
        for e in kept:
            if not e.get('combined') and e.get('source') and e['source'] != e['root']:
                k = (e['kind'], e['source'])
                if k not in newest or e.get('stamp', 0) > newest[k].get('stamp', 0):
                    newest[k] = e
        library['entries'] = [e for e in kept if e.get('combined') or not e.get('source') or e['source'] == e['root']
                              or newest.get((e['kind'], e['source'])) is e]
        if json.dumps(library, sort_keys=True) != before:
            save_library()


def library_seed():
    """Datasets and finished conversions from before the library existed (or missed): add them."""
    keys = {e['key'] for e in library['entries']}
    for d in sorted(registry['datasets'], key=lambda d: d.get('added', 0)):
        if d.get('fromLibrary') or library_key(d) not in keys:
            record_library(d, now=d.get('added'))
            keys.add(library_key(d))
        else:  # its entry exists: only make sure this workspace is among its users
            e = next(x for x in library['entries'] if x['key'] == library_key(d))
            if d.get('ownerName') and d['ownerName'] not in e.setdefault('users', []):
                e['users'].append(d['ownerName'])
                save_library()
    for j in sorted(jobs.values(), key=lambda j: j['created']):
        out = Path(j.get('out') or '')
        if j['status'] != 'done' or str(out) in keys or not (out / 'vizhub.json').is_file():
            continue
        info = detect.inspect_folder(out)
        if not info.get('ready') or info['type'] == 'viewer-collection':
            continue
        layout = {'viewer-slide': 'slide', 'viewer-project': 'project'}.get(info['type'], 'screen')
        with lock:
            library['entries'].append(dict(key=str(out), datasetId=j.get('datasetId'), owner=j.get('owner'), ownerName=j.get('ownerName'),
                                           added=j.get('finished') or j['created'], updated=j.get('finished') or j['created'],
                                           users=[j['ownerName']] if j.get('ownerName') else [], kind=info['kind'], title=j.get('title') or info['title'],
                                           root=str(out), source=j['source'], layout=layout, slides=[]))
        keys.add(str(out))
    with lock:
        for e in library['entries']:
            e.setdefault('stamp', -1)  # read from the folder on the first sync
    library_sync(force=True)


def _library_hourly(check=True):
    if check:
        try:
            library_sync(force=True)
        except Exception as exc:
            print(f'library sync: {exc}', file=sys.stderr)
    t = threading.Timer(3600, _library_hourly)
    t.daemon = True
    t.start()


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
                                source=j['source'], job=jid, id_=j['datasetId'], kind=j.get('kind'))
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
    o = body.get('options') or {}
    kind = body.get('kind') or 'auto'
    # converting again keeps the workspace's dataset (its link) for this output folder
    prev = next((d for d in registry['datasets'] if d.get('owner') == owner['id'] and d['root'] == str(out)
                 and (kind == 'auto' or d['kind'] == kind)), None)
    did = prev['id'] if prev else unique_id(title, f'{title}-{KIND_PATH.get(body.get("kind") or info.get("kind") or "", "data")}')
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

def combined_into():
    """dataset id -> [combined dataset ids] for datasets all of whose samples are in a combined dataset of the same
    workspace. Those are shown inside it, not on their own (their links still open)."""
    used = {}
    for c in registry['datasets']:
        if c.get('combined') or c.get('edited'):
            for sl in c['slides']:
                if sl.get('fromDataset') == c['id']:
                    continue  # an edited project's own sample
                used.setdefault((c.get('owner'), sl.get('fromDataset')), {}).setdefault(sl.get('fromSlide'), set()).add(c['id'])
    out = {}
    for d in registry['datasets']:
        got = used.get((d.get('owner'), d['id']))
        if not d.get('combined') and got and all(s['id'] in got for s in samples_of(d)):
            out[d['id']] = sorted(set().union(*got.values()))
    return out


@app.get('/hub/api/datasets')
def list_datasets(request: Request, kind: str = ''):
    """The datasets of the caller's workspace (all viewers, or one); `inCombined` marks those shown inside a
    combined dataset."""
    ws = workspace_of(request)
    inside = combined_into()
    return [{**public(d), **({'inCombined': inside[d['id']]} if d['id'] in inside else {})}
            for d in sorted(registry['datasets'], key=lambda d: -d.get('added', 0))
            if own(d, ws) and (not kind or d['kind'] == kind)]


@app.get('/hub/api/summary')
def summary(request: Request):
    """Dataset counts per viewer: the caller's, everyone's and the shared library's (hub cards)."""
    ws = workspace_of(request, required=False)
    out = {k: {'mine': 0, 'all': 0} for k in KIND_PATH}
    inside = combined_into()
    for d in registry['datasets']:
        if d['id'] in inside:
            continue
        out[d['kind']]['all'] += 1
        out[d['kind']]['mine'] += own(d, ws)
    library_sync()
    for e in library['entries']:
        if not e.get('hidden') and e['kind'] in out:
            out[e['kind']]['shared'] = out[e['kind']].get('shared', 0) + 1
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
        return {'datasets': [public(register_folder(src, ws, title=title, kind=body.get('kind')))]}
    out = info.get('output') or {}
    if out.get('converted') and not body.get('reconvert'):
        m = out.get('manifest') or {}
        d = register_folder(Path(out['path']), ws, title=title or m.get('title'), source=str(src), id_=None, kind=body.get('kind'))
        return {'datasets': [public(d)], 'reused': True}
    running = next((j for j in jobs.values() if j['source'] == str(src) and j['status'] in ('queued', 'running')), None)
    if running:
        if running.get('owner') == ws['id']:
            return {'job': job_public(running)}
        raise HTTPException(409, f'{running.get("ownerName")} is converting this file right now; add it again when that finishes')
    return {'job': job_public(start_job(src, info, body, ws))}


# ---------- combined datasets: several samples (slides of the workspace's datasets) shown as one project

def samples_of(d):
    """A dataset's samples: its slides, or the screen itself for a CRISPR screen (id "screen")."""
    return d['slides'] or ([dict(id='screen', title=d['title'], dir='')] if d['layout'] == 'screen' else [])


def slide_dir(d, s):
    """The folder of one slide: its own root in a combined dataset, else under the dataset's root."""
    return Path(s['root']) if s.get('root') else Path(d['root']) / s['dir']


def combined_slides(items, ws, kind):
    """[{dataset, slide, title, id?}] (the caller's datasets of this viewer) -> slides of a combined dataset, or the
    edited samples of a project (its own samples are {dataset: itself, slide}; `folderSlides` keeps the folder's).
    `id` keeps an existing sample's id (its ?s= link) through renames."""
    if not isinstance(items, list) or len(items) < 1:
        raise HTTPException(400, 'Pick at least one sample')
    out, taken = [], set()
    for it in items[:200]:
        src = dataset(str(it.get('dataset', '')))
        require_owner(src, ws)
        if src['kind'] != kind:
            raise HTTPException(400, f'“{src["title"]}” is not a {KIND_PATH[kind]} dataset')
        pool = samples_of(src) + [x for x in src.get('folderSlides') or [] if x['id'] not in {y['id'] for y in src['slides']}]
        s = next((x for x in pool if x['id'] == it.get('slide')), None) or (pool[0] if len(pool) == 1 and not it.get('slide') else None)
        if not s:
            raise HTTPException(400, f'“{src["title"]}” has no sample {it.get("slide")!r}')
        if s.get('fromDataset') and s.get('fromDataset') != src['id']:  # a sample of a combined / edited dataset: its origin
            src = registry_get(s['fromDataset']) or src
        folder = slide_dir(src, s) if not s.get('root') else Path(s['root'])
        marker = 'project.json' if kind == 'crispr' else 'slide.json'
        if not (folder / marker).is_file():
            raise HTTPException(400, f'{folder} has no {marker}')
        title = (str(it.get('title') or '').strip() or (s['title'] if len(samples_of(src)) > 1 else src['title']))[:120]
        want = slugify(it.get('id') or title)
        sid, k = want, 2
        while sid in taken:
            sid, k = f'{want}-{k}', k + 1
        taken.add(sid)
        try:
            m = json.loads((folder / marker).read_text())
        except Exception:
            m = {}
        extra = dict(screenKind=m.get('kind'), species=m.get('species')) if kind == 'crispr' else \
            dict(nCells=m.get('nCells'), technology=m.get('technology'), species=m.get('species'))
        out.append(dict(id=sid, title=title, dir=sid, root=str(folder), fromDataset=src['id'], fromTitle=src['title'], fromSlide=s.get('fromSlide') or s['id'],
                        **extra))
    return out


def registry_get(id_):
    return next((d for d in registry['datasets'] if d['id'] == id_), None)


def combined_facts(slides):
    uniq = lambda k: ', '.join(dict.fromkeys(str(s[k]) for s in slides if s.get(k)))
    facts = dict(nCells=sum(s.get('nCells') or 0 for s in slides) or None, technology=uniq('technology') or None, species=uniq('species') or None,
                 source='Combined: ' + ', '.join(dict.fromkeys(s['fromTitle'] for s in slides)))
    return facts


@app.post('/hub/api/combined')
async def add_combined(request: Request):
    """A new dataset made of samples of the caller's datasets (e.g. time points, sections); nothing is copied."""
    ws = workspace_of(request)
    body = await request.json()
    kind = body.get('kind')
    if kind not in KIND_PATH:
        raise HTTPException(400, 'kind: spatial, singlecell or crispr')
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
        record_library(d)
    return public(d)


@app.patch('/hub/api/datasets/{id_}')
async def edit_dataset(id_: str, request: Request):
    body = await request.json()
    d = dataset(id_)
    require_owner(d, workspace_of(request))
    editable = d.get('combined') or d['layout'] == 'project'
    if 'slides' in body and not editable:
        raise HTTPException(400, 'Only datasets with several samples can be edited this way; use Combine datasets')
    slides = combined_slides(body['slides'], workspace_of(request), d['kind']) if 'slides' in body else None
    old_title = d['title']
    with lock:
        for k, n in (('title', 300), ('description', 2000)):
            if k in body:
                if k == 'description' and d['layout'] == 'project' and not d.get('combined'):
                    try:  # edited unless it is project.json's own text
                        folder = json.loads((Path(d['root']) / 'project.json').read_text()).get('description') or ''
                    except Exception:
                        folder = ''
                    d['descriptionEdited'] = str(body[k])[:n] != folder
                    if not d['descriptionEdited']:
                        d.pop('descriptionEdited')
                d[k] = str(body[k])[:n]
        if slides is not None:
            if not d.get('combined'):
                # a project folder: its project.json is not changed; the folder's own samples stay restorable
                d.setdefault('folderSlides', [dict(x) for x in d['slides']])
                d['edited'] = True
            d['slides'] = slides
            facts = combined_facts(slides)
            if not d.get('combined'):
                facts.pop('source')
            d.update(facts)
        if d['title'] != old_title:
            rename_in_combined(d, old_title)
        save_registry()
        record_library(d)
    return public(d)


def rename_in_combined(d, old_title):
    """A renamed dataset is renamed where it is a sample: combined (and edited) datasets of its workspace show it by
    its name. A sample still called by the old name takes the new one; one renamed inside the combined dataset keeps
    its own name. Call with the lock held."""
    single = len(samples_of(d)) <= 1
    for c in registry['datasets']:
        if c is d or c.get('owner') != d.get('owner') or not (c.get('combined') or c.get('edited')):
            continue
        changed = False
        for sl in c['slides']:
            if sl.get('fromDataset') != d['id']:
                continue
            if single and sl.get('title') == (sl.get('fromTitle') or old_title) == old_title:
                sl['title'] = d['title']
            sl['fromTitle'] = d['title']
            changed = True
        if changed:
            if c.get('combined'):
                c['source'] = combined_facts(c['slides'])['source']
            record_library(c)


def sync_sample_names():
    """At start: samples still named after a dataset renamed before renames reached combined datasets (2026-10-16)."""
    by = {d['id']: d for d in registry['datasets']}
    with lock:
        any_change = False
        for c in registry['datasets']:
            if not (c.get('combined') or c.get('edited')):
                continue
            changed = False
            for sl in c['slides']:
                m = by.get(sl.get('fromDataset'))
                if not m or m is c or sl.get('fromTitle') in (None, m['title']):
                    continue
                if len(samples_of(m)) <= 1 and sl.get('title') == sl.get('fromTitle'):
                    sl['title'] = m['title']
                sl['fromTitle'] = m['title']
                changed = True
            if changed:
                any_change = True
                if c.get('combined'):
                    c['source'] = combined_facts(c['slides'])['source']
                record_library(c)
        if any_change:
            save_registry()


@app.post('/hub/api/datasets/{id_}/restore')
def restore_samples(id_: str, request: Request):
    """An edited project dataset: back to the samples of its folder's project.json."""
    d = dataset(id_)
    require_owner(d, workspace_of(request))
    if not d.get('folderSlides'):
        raise HTTPException(400, 'This dataset has its folder\'s samples')
    with lock:
        d['slides'] = d.pop('folderSlides')
        d.pop('edited', None)
        d.update(folder_facts(Path(d['root']), detect.inspect_folder(Path(d['root']))))
        save_registry()
        record_library(d)
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
    if removed:
        library_sync(force=True)  # its shared entry goes with the files
    return {'removed': id_, 'deletedFolder': removed, 'keptFor': sorted({x.get('ownerName') or '?' for x in others})}


# ---------- shared library: every workspace's datasets, for everyone

def library_public(e, ws, reg):
    """An entry as the pages show it: whether the caller has it, and a link that opens it now (any workspace's)."""
    if e.get('combined'):
        cands = [d for d in reg if d.get('fromLibrary') == e['key'] or d['id'] == e.get('datasetId')]
    else:
        cands = [d for d in reg if d['root'] == e['root'] and not d.get('combined')]
    mine = next((d for d in cands if own(d, ws)), None)
    live = mine or next((d for d in cands if d.get('owner') == e.get('owner')), None) or (cands[0] if cands else None)
    out = {k: v for k, v in e.items() if k not in ('stamp', 'owner', 'hidden')}
    out.update(nSamples=len(e.get('slides') or []), mine=bool(mine), myUrl=view_url(mine) if mine else None,
               openUrl=view_url(live) if live else None, author=bool(ws) and e.get('owner') == ws['id'])
    return out


def library_entry(key):
    e = next((x for x in library['entries'] if x['key'] == key and not x.get('hidden')), None)
    if not e:
        raise HTTPException(404, 'No longer in the shared library: its files were removed')
    return e


@app.get('/hub/api/library')
def list_library(request: Request, kind: str = ''):
    """Everything any workspace has loaded, converted or combined (newest first), checked against the files."""
    ws = workspace_of(request, required=False)
    library_sync()
    with lock:
        reg = list(registry['datasets'])
        entries = [e for e in library['entries'] if not e.get('hidden') and (not kind or e['kind'] == kind)]
        return [library_public(e, ws, reg) for e in sorted(entries, key=lambda e: -max(e.get('updated', 0), e.get('added', 0)))]


@app.post('/hub/api/library/add')
async def add_from_library(request: Request):
    """Add a shared dataset to the caller's workspace (nothing is copied or converted)."""
    ws = workspace_of(request)
    body = await request.json()
    library_sync()
    e = library_entry(str(body.get('key', '')))
    title = str(body.get('title') or '').strip()[:300] or e['title']
    reg = list(registry['datasets'])
    if e.get('combined'):
        mine = next((d for d in reg if own(d, ws) and (d.get('fromLibrary') == e['key'] or d['id'] == e.get('datasetId'))), None)
        if mine:
            return {'dataset': public(mine), 'existing': True}
        marker = sample_marker(e['kind'])
        slides = [{**s, 'fromTitle': s.get('fromTitle') or s['title']} for s in e['slides'] if (Path(s['root']) / marker).is_file()]
        if not slides:
            raise HTTPException(404, 'The samples of this combined dataset were removed')
        with lock:
            did = unique_id(title, f'{title}-{KIND_PATH[e["kind"]]}')
            d = dict(id=did, kind=e['kind'], title=title, description=e.get('description') or '', root=f'combined:{did}', layout='project',
                     combined=True, slides=slides, added=int(time.time()), job=None, owner=ws['id'], ownerName=ws['name'],
                     fromLibrary=e['key'], **combined_facts(slides))
            registry['datasets'].append(d)
            save_registry()
            record_library(d)
        return {'dataset': public(d)}
    mine = next((d for d in reg if own(d, ws) and d['root'] == e['root'] and not d.get('combined')), None)
    if mine:
        return {'dataset': public(mine), 'existing': True}
    return {'dataset': public(register_folder(Path(e['root']), ws, title=title, source=e.get('source')))}


@app.post('/hub/api/library/hide')
async def hide_from_library(request: Request):
    """Its author takes an entry off the shared list (it comes back if they add the dataset again)."""
    ws = workspace_of(request)
    body = await request.json()
    e = library_entry(str(body.get('key', '')))
    if e.get('owner') != ws['id']:
        raise HTTPException(403, f'Only {e.get("ownerName") or "the workspace that added it"} can take it off the shared list')
    with lock:
        e['hidden'] = True
        save_library()
    return {'hidden': e['key']}


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
        try:  # its log goes with it
            (STATE / 'jobs' / f'{jid}.log').unlink()
        except OSError:
            pass
    return {'ok': True}


# ---------- viewer API (the ATHENA viewers' /api/<kind>/projects and /api/data)

@app.get('/hub/api/projects')
def projects(kind: str, request: Request, include: str = ''):
    """Viewer picker: the caller's datasets of this viewer, plus `include` (a dataset opened by its link)."""
    ws = workspace_of(request, required=False)
    out = []
    inside = combined_into()
    for d in sorted(registry['datasets'], key=lambda d: d['title'].lower()):
        if d['kind'] != kind or not (own(d, ws) or d['id'] == include):
            continue
        if d['id'] in inside and d['id'] != include:  # picked from its combined dataset instead
            continue
        base = f'/hub/api/data/{d["id"]}'
        extra = {}
        if d['kind'] == 'crispr' and d.get('combined'):
            for sl in d['slides']:
                try:
                    pj = json.loads((Path(sl['root']) / 'project.json').read_text())
                except Exception:
                    continue
                src = registry_get(sl.get('fromDataset')) or {}
                out.append({**pj, 'id': d['id'], 'sample': sl['id'], 'group': d['title'], 'groupDescription': d.get('description') or '',
                            'title': sl['title'], 'url': f'{view_url(d)}?s={sl["id"]}', 'base': f'{base}/{sl["id"]}',
                            'arcs': f'/hub/api/arcs/{d["id"]}/{sl["id"]}', 'mine': own(d, ws), 'ownerName': d.get('ownerName'),
                            'reports': report_links(sl['root'], src.get('source')),
                            # reports belong to the screen the sample comes from; its owner manages them
                            'reportsOf': src.get('id') if src.get('layout') == 'screen' else None,
                            'reportsEditable': bool(src) and own(src, ws) and src.get('layout') == 'screen',
                            'slides': [], 'downloads': []})
            continue
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
            if d.get('description'):  # edited in the hub (starts as project.json's)
                extra['description'] = d['description']
        if d['layout'] == 'screen':
            extra.update(reports=report_links(d['root'], d.get('source')), reportsOf=d['id'], reportsEditable=own(d, ws))
        out.append({**extra, 'id': d['id'], 'title': d['title'], 'url': view_url(d), 'base': base, 'arcs': f'/hub/api/arcs/{d["id"]}', 'mine': own(d, ws),
                    'ownerName': d.get('ownerName'),
                    'technology': extra.get('technology') or d.get('technology'), 'species': extra.get('species') or d.get('species'),
                    'slides': [dict(id=s['id'], title=s['title'], base=f'{base}/{s["dir"]}'.rstrip('/'),
                                    **({'description': f'from {s["fromTitle"]}'} if s.get('fromTitle') else {})) for s in d['slides']],
                    'downloads': []})
    return out


RANGE = re.compile(r'bytes=(\d*)-(\d*)')


# --------------------------------------------------------------- reports (e.g. PerturbNMF gene program explorer)
# Interactive HTML pages that belong to a CRISPR screen, opened from the explorer. Kept per data folder in
# state/reports.json ({root: {"add": [{path, title}], "hide": [path]}}), so every workspace with the screen (and every
# combined dataset holding it) sees them; PerturbNMF reports next to the SCEPTRE results are found on their own.

reports = _load('reports.json', {})
_report_cache = {}  # source -> (time, [paths]) for found reports
_report_paths = {}  # report id -> path (filled when reports are listed)
REPORT_GLOBS = ['perturbnmf/gene_program_explorer/report/*.html', 'perturbnmf/**/report/*.html', '*/report/*GeneProgramExplorer*.html']


def found_reports(source):
    """PerturbNMF report pages near a screen's source file: in the folders up to three levels above it."""
    if not source or not source.startswith('/'):
        return []
    hit = _report_cache.get(source)
    if hit and time.time() - hit[0] < 300:
        return hit[1]
    paths = []
    folder = Path(source).parent
    for _ in range(4):
        for pattern in REPORT_GLOBS:  # the most specific first; the recursive one only when it finds nothing
            try:
                paths = [str(p) for p in sorted(folder.glob(pattern)) if p.is_file()]
            except OSError:
                paths = []
            if paths:
                break
        if paths or folder.parent == folder:
            break
        folder = folder.parent
    paths = list(dict.fromkeys(paths))[:20]
    _report_cache[source] = (time.time(), paths)
    return paths


def report_title(path):
    """The page's own <title> (a PerturbNMF report: "Gene Program Explorer"), else its file name."""
    try:
        with open(path, 'rb') as f:
            m = re.search(rb'<title[^>]*>(.*?)</title>', f.read(65536), re.I | re.S)
    except OSError:
        m = None
    title = re.sub(r'\s+', ' ', m.group(1).decode('utf-8', 'replace')).strip() if m else ''
    return (title or Path(path).stem.replace('_', ' '))[:120]


def report_list(root, source):
    """[{path, title, found}] for a screen folder: those added in the hub, then those found, minus hidden ones."""
    entry = reports.get(str(root)) or {}
    hidden = set(entry.get('hide') or [])
    out = [dict(path=r['path'], title=r.get('title') or report_title(r['path']), found=False) for r in entry.get('add') or []
           if os.path.isfile(r['path'])]
    seen = {r['path'] for r in out}
    out += [dict(path=p, title=report_title(p), found=True) for p in found_reports(source) if p not in seen and p not in hidden]
    return out


def report_id(path):
    rid = hashlib.sha1(path.encode()).hexdigest()[:12]
    _report_paths[rid] = path
    return rid


def report_links(root, source):
    return [dict(title=r['title'], url=f'/hub/api/reports/{report_id(r["path"])}/{quote(Path(r["path"]).name)}', found=r['found'])
            for r in report_list(root, source)]


@app.get('/hub/api/reports/{rid}/{rest:path}')
def report_file(rid: str, rest: str, request: Request):
    """A report page and the files next to it (its folder only)."""
    path = _report_paths.get(rid)
    if not path:  # not listed since the hub started: look through the screens
        for d in registry['datasets']:
            if d['kind'] == 'crispr':
                for sl in (d['slides'] or [dict(root=d['root'])]):
                    report_links(sl.get('root') or d['root'], (registry_get(sl.get('fromDataset')) or d).get('source'))
        path = _report_paths.get(rid)
    if not path:
        raise HTTPException(404, 'no such report')
    folder = Path(path).parent
    rel = posixpath.normpath(rest or Path(path).name)
    if rel.startswith(('..', '/')):
        raise HTTPException(400, 'bad path')
    target = folder / rel
    if not target.is_file():
        raise HTTPException(404, f'{rel} not found')
    return FileResponse(target, headers={'cache-control': 'private, max-age=300'})


@app.get('/hub/api/datasets/{id_}/reports')
def dataset_reports(id_: str, request: Request):
    d = dataset(id_)
    require_owner(d, workspace_of(request))
    return [dict(r, url=f'/hub/api/reports/{report_id(r["path"])}/{quote(Path(r["path"]).name)}') for r in report_list(d['root'], d.get('source'))]


@app.post('/hub/api/datasets/{id_}/reports')
async def add_report(id_: str, request: Request):
    """Link an HTML report to a screen (for everyone who has the screen)."""
    d = dataset(id_)
    require_owner(d, workspace_of(request))
    if d['kind'] != 'crispr' or d.get('combined'):
        raise HTTPException(400, 'Reports belong to a CRISPR screen (add them to the screens a combined dataset holds)')
    body = await request.json()
    p = checked_path(str(body.get('path') or '').strip())
    if not p.is_file() or p.suffix.lower() not in ('.html', '.htm'):
        raise HTTPException(400, 'Give an HTML file (.html)')
    title = str(body.get('title') or '').strip()[:120]
    with lock:
        entry = reports.setdefault(d['root'], {})
        entry['add'] = [r for r in entry.get('add') or [] if r['path'] != str(p)] + [dict(path=str(p), **({'title': title} if title else {}))]
        entry['hide'] = [h for h in entry.get('hide') or [] if h != str(p)]
        _save('reports.json', reports)
    return dataset_reports(id_, request)


@app.delete('/hub/api/datasets/{id_}/reports')
def remove_report(id_: str, path: str, request: Request):
    """Unlink a report (a found one is hidden). The HTML file is never deleted."""
    d = dataset(id_)
    require_owner(d, workspace_of(request))
    with lock:
        entry = reports.setdefault(d['root'], {})
        entry['add'] = [r for r in entry.get('add') or [] if r['path'] != path]
        if path in found_reports(d.get('source')):
            entry['hide'] = list(dict.fromkeys((entry.get('hide') or []) + [path]))
        if not entry['add'] and not entry.get('hide'):
            reports.pop(d['root'], None)
        _save('reports.json', reports)
    return dataset_reports(id_, request)


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


@app.get('/hub/api/arcs/{id_}/{sample}/{which}.bedpe')
def enhancer_arcs_sample(id_: str, sample: str, which: str, genome: str = 'hg38'):
    """The same for one screen of a combined CRISPR dataset."""
    return enhancer_arcs(id_, which, genome, sample)


@app.get('/hub/api/arcs/{id_}/{which}.bedpe')
def enhancer_arcs(id_: str, which: str, genome: str = 'hg38', sample: str = ''):
    """Enhancer -> target-gene TSS links of an enhancer screen as BEDPE, for a Genome Canvas loop track
    (as the ATHENA locus view): `significant` or `all` tested pairs; score = |log2FC| (arc height)."""
    if which not in ('significant', 'all'):
        raise HTTPException(404, 'significant or all')
    d = dataset(id_)
    tss = tss_table(genome)
    if d['kind'] != 'crispr' or tss is None:
        raise HTTPException(404, f'no enhancer links for {genome}')
    folder = Path(d['root'])
    if sample or d.get('combined'):
        sl = next((x for x in d['slides'] if x['id'] == sample), None)
        if not sl or not sl.get('root'):
            raise HTTPException(404, f'no screen {sample}')
        folder = Path(sl['root'])
    p = folder / 'tests.json'
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
    # /<sample id>/<file> of a sample with its own folder (combined or edited datasets) -> that folder
    sid, _, rest_ = rel.partition('/')
    sl = next((x for x in d['slides'] if x.get('root') and x['id'] == sid), None)
    if sl:
        p = Path(sl['root']) / rest_
    elif d.get('combined'):
        raise HTTPException(404, f'no sample {sid}')
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

library_seed()
_library_hourly(check=False)


sync_sample_names()


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host=CONFIG.get('host', '127.0.0.1'), port=int(CONFIG.get('port', 8893)), proxy_headers=True,
                forwarded_allow_ips='127.0.0.1', log_level='info', access_log=False)
