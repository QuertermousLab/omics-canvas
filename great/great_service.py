"""The GREAT service of Omics Canvas: jobs, worker processes, request limits and GREAT's output formats.

- Jobs live in <state>/jobs/<id>/ (job.json, the regions, one table per ontology), kept jobRetentionDays and within
  maxStorageMB (a cleanup thread, start_maintenance).
- Analyses run in worker processes (great.py --worker), at most maxConcurrentJobs at once.
- Request limits (minimum interval per client, jobs per hour, queue wait, sizes) are settings in
  <state>/settings.json, changed live from the admin panel (POST /great/api/settings) without a restart.
- GREAT's interfaces, for rGREAT and scripts: greatWeb.php (form POST, rGREAT's submitGreatJob), readJsFromFile.php,
  downloadAllTSV.php and downloadAssociations.php (rGREAT's result calls), greatStart.php (the GREAT Programming
  Interface, outputType=web|batch). server.py maps /great/public/cgi-bin/<name>.php here.

Standard library only.
"""

import gzip
import html
import json
import math
import os
import re
import secrets
import select
import shutil
import subprocess
import sys
import threading
import time
from collections import OrderedDict, deque
from email.parser import BytesParser
from email.policy import HTTP
from pathlib import Path
from urllib.parse import parse_qs

import great

APP_DIR = Path(__file__).resolve().parent
JOB_ID = re.compile(r"[0-9]{8}-[0-9]{6}-[a-z0-9]{8}")
DEFAULT_SETTINGS = OrderedDict([
    ("minIntervalSeconds", 5),        # between two analyses from one client (GREAT asked for 60)
    ("maxJobsPerHour", 0),            # per client; 0 = no limit
    ("maxConcurrentJobs", 3),         # analyses running at once (worker processes)
    ("maxWaitSeconds", 300),          # a request over a limit waits up to this long, then gets HTTP 429
    ("maxRegions", 1000000),          # test regions per analysis (GREAT: 500,000)
    ("maxBackgroundRegions", 2000000),  # background regions (GREAT: 1,000,000)
    ("maxUploadMB", 200),             # per file, after decompression (GREAT: 50 MB)
    ("jobRetentionDays", 30),         # results are kept this long (GREAT: 24 hours)
    ("maxStorageMB", 20480),          # stored results above this: the oldest are deleted; 0 = no limit
    ("exportTermLimit", 0),           # terms per ontology in batch / TSV exports; 0 = all (GREAT: 500)
    ("allowRemoteURLs", True),        # greatStart.php may fetch requestURL / bgURL
    ("apiEnabled", True),             # GREAT's .php interfaces (rGREAT, scripts)
    ("exemptClients", ["127.0.0.1", "::1"]),  # never rate limited
    ("blockedClients", []),
])
SETTING_LIMITS = {
    "minIntervalSeconds": (0, 86400), "maxJobsPerHour": (0, 100000), "maxConcurrentJobs": (1, 64),
    "maxWaitSeconds": (0, 3600), "maxRegions": (1, 50000000), "maxBackgroundRegions": (1, 100000000),
    "maxUploadMB": (1, 4096), "jobRetentionDays": (0.01, 3650), "maxStorageMB": (0, 100000000),
    "exportTermLimit": (0, 1000000),
}
WORKER_TIMEOUT_SECONDS = 1800
CLEANUP_INTERVAL_SECONDS = 600  # the automatic cleanup: retention, then the storage limit


class LimitError(Exception):
    """A request refused by the limits: HTTP status, message, seconds to wait."""

    def __init__(self, status, message, retry_after=None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


# ---------------------------------------------------------------- worker processes

class Worker:
    def __init__(self, data_dir):
        self.process = subprocess.Popen(
            [sys.executable, "-u", str(APP_DIR / "great.py"), "--worker", str(data_dir)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, cwd=str(APP_DIR), universal_newlines=True, bufsize=1)
        self.assemblies = set()
        self.busy = False

    def alive(self):
        return self.process.poll() is None

    def call(self, request, timeout):
        self.process.stdin.write(json.dumps(request) + "\n")
        self.process.stdin.flush()
        ready, _, _ = select.select([self.process.stdout], [], [], timeout)
        if not ready:
            self.stop()
            raise RuntimeError("The GREAT worker did not answer within {} s".format(timeout))
        line = self.process.stdout.readline()
        if not line:
            raise RuntimeError("The GREAT worker stopped (exit {})".format(self.process.poll()))
        return json.loads(line)

    def stop(self):
        if self.alive():
            self.process.kill()
        try:
            self.process.wait(5)
        except subprocess.TimeoutExpired:
            pass
        for pipe in (self.process.stdin, self.process.stdout):
            try:
                pipe.close()
            except OSError:
                pass


class WorkerPool:
    """Worker processes, reused; a request goes to an idle one that already holds its assembly when possible."""

    def __init__(self, data_dir, size):
        self.data_dir = data_dir
        self.size = size  # callable: workers to keep
        self.workers = []
        self.lock = threading.Condition()

    def call(self, request, assembly=None, timeout=WORKER_TIMEOUT_SECONDS):
        worker = self._acquire(assembly)
        try:
            response = worker.call(request, timeout)
            if assembly:
                worker.assemblies.add(assembly)
        except Exception:
            worker.stop()
            raise
        finally:
            self._release(worker)
        if not response.get("ok"):
            if response.get("user"):
                raise great.InputError(response.get("error", "GREAT could not run this request"))
            raise RuntimeError(response.get("error", "GREAT worker error"))
        return response["result"]

    def _acquire(self, assembly):
        with self.lock:
            while True:
                self.workers = [w for w in self.workers if w.alive() or w.busy]
                idle = [w for w in self.workers if not w.busy]
                chosen = next((w for w in idle if assembly in w.assemblies), None) or \
                    next((w for w in idle if not w.assemblies), None) or (idle[0] if idle else None)
                if chosen is None and len(self.workers) < self.size() + 2:
                    chosen = Worker(self.data_dir)
                    self.workers.append(chosen)
                if chosen is not None:
                    chosen.busy = True
                    return chosen
                self.lock.wait(1.0)

    def _release(self, worker):
        with self.lock:
            worker.busy = False
            idle = [w for w in self.workers if not w.busy and w.alive()]
            while len(self.workers) > self.size() + 1 and idle:  # shrink after the limit was lowered
                extra = idle.pop()
                extra.stop()
                self.workers.remove(extra)
            self.lock.notify_all()

    def status(self):
        with self.lock:
            return [{"pid": w.process.pid, "busy": w.busy, "assemblies": sorted(w.assemblies)}
                    for w in self.workers if w.alive()]

    def stop(self):
        with self.lock:
            for w in self.workers:
                w.stop()
            self.workers = []


# ---------------------------------------------------------------- the service

class GreatService:
    def __init__(self, data_dir, state_dir, admins=()):
        """data_dir: the built genes and ontologies (data/<assembly>/); state_dir: jobs/ and settings.json."""
        self.data_dir = Path(data_dir)
        self.state_dir = Path(state_dir)
        self.jobs_dir = self.state_dir / "jobs"
        self.settings_file = self.state_dir / "settings.json"
        self.admins = [str(a) for a in admins or []]
        self.lock = threading.Condition()
        self.running = OrderedDict()   # job id -> {client, assembly, started}
        self.waiting = OrderedDict()   # ticket -> {client, since}
        self.history = {}              # client -> deque of start times (last hour)
        self.recent = deque(maxlen=200)  # finished jobs, for the admin panel
        self.last_sweep = 0
        self.last_cleanup = None  # {"time", "removed", "freedBytes", "reason"} of the last cleanup that removed jobs
        self.maintenance = None
        self._settings = None
        self._settings_mtime = None
        self.pool = WorkerPool(self.data_dir, lambda: int(self.settings()["maxConcurrentJobs"]))
        self.manifests = {}

    # ---------- settings (live: re-read when the file changes)

    def settings(self):
        try:
            mtime = self.settings_file.stat().st_mtime_ns
        except OSError:
            mtime = None
        if self._settings is None or mtime != self._settings_mtime:
            saved = {}
            if mtime is not None:
                try:
                    saved = json.loads(self.settings_file.read_text())
                except (OSError, ValueError):
                    saved = {}
            merged = OrderedDict(DEFAULT_SETTINGS)
            for key, value in saved.items():
                if key in merged:
                    merged[key] = value
            self._settings, self._settings_mtime = merged, mtime
        return self._settings

    def update_settings(self, changes):
        current = OrderedDict(self.settings())
        for key, value in changes.items():
            if key not in DEFAULT_SETTINGS:
                raise ValueError("Unknown setting {}".format(key))
            default = DEFAULT_SETTINGS[key]
            if isinstance(default, bool):
                value = value if isinstance(value, bool) else str(value).lower() in ("1", "true", "yes", "on")
            elif isinstance(default, list):
                if isinstance(value, str):
                    value = [v for v in re.split(r"[\s,]+", value) if v]
                if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                    raise ValueError("{} must be a list of addresses".format(key))
                value = [v.strip() for v in value if v.strip()][:500]
            else:
                try:
                    value = float(value)
                except (TypeError, ValueError):
                    raise ValueError("{} must be a number".format(key))
                low, high = SETTING_LIMITS[key]
                if not math.isfinite(value) or not low <= value <= high:
                    raise ValueError("{} must be between {:g} and {:g}".format(key, low, high))
                if isinstance(default, int) and key not in ("jobRetentionDays", "maxStorageMB"):
                    value = int(value)
            current[key] = value
        self.state_dir.mkdir(parents=True, exist_ok=True)
        part = self.settings_file.with_suffix(".json.part")
        part.write_text(json.dumps(current, indent=2))
        part.replace(self.settings_file)
        self._settings = None
        with self.lock:
            self.lock.notify_all()  # waiting requests see the new limits
        return self.settings()

    def is_admin(self, workspace):
        if not workspace:
            return False
        if self.admins:
            return workspace.get("id") in self.admins or workspace.get("name") in self.admins
        return workspace.get("kind") == "home" and workspace.get("name") == Path.home().name

    # ---------- data

    def assemblies(self):
        out = []
        for name in great.ASSEMBLIES:
            path = self.data_dir / name / "manifest.json"
            try:
                stat = path.stat()
            except OSError:
                continue
            cached = self.manifests.get(name)
            if not cached or cached[0] != stat.st_mtime_ns:
                cached = (stat.st_mtime_ns, json.loads(path.read_text()))
                self.manifests[name] = cached
            manifest = cached[1]
            out.append({"name": name, "species": great.SPECIES[name], "genes": manifest.get("genes"),
                        "built": manifest.get("built"), "ontologies": manifest.get("ontologies", [])})
        return out

    def available(self, assembly):
        return any(a["name"] == assembly for a in self.assemblies())

    def warm(self, assemblies=("hg38",)):
        names = [a for a in assemblies if self.available(a)]
        if names:
            threading.Thread(target=self._warm, args=(names,), daemon=True).start()

    def _warm(self, names):
        try:
            self.pool.call({"op": "warm", "assemblies": names}, assembly=names[0])
        except Exception as exc:
            print("GREAT: warm-up failed: {}".format(exc), file=sys.stderr)

    # ---------- limits

    def admit(self, client):
        """Wait for a free slot within the limits (live settings); raise LimitError when the wait is too long."""
        ticket = secrets.token_hex(6)
        with self.lock:
            start = time.time()
            self.waiting[ticket] = {"client": client, "since": start}
            try:
                while True:
                    s = self.settings()
                    now = time.time()
                    if client in s["blockedClients"]:
                        raise LimitError(403, "This client is blocked from GREAT on this server")
                    exempt = client in s["exemptClients"]
                    starts = self.history.setdefault(client, deque())
                    while starts and starts[0] < now - 3600:
                        starts.popleft()
                    waits = []  # (seconds, reason)
                    if not exempt and starts and s["minIntervalSeconds"] > 0:
                        waits.append((starts[-1] + s["minIntervalSeconds"] - now,
                                      "one analysis per {:g} s from one client".format(s["minIntervalSeconds"])))
                    if not exempt and s["maxJobsPerHour"] > 0 and len(starts) >= s["maxJobsPerHour"]:
                        waits.append((starts[-int(s["maxJobsPerHour"])] + 3600 - now,
                                      "at most {} analyses per hour from one client".format(s["maxJobsPerHour"])))
                    wait, reason = max(waits + [(0, "")])
                    full = len(self.running) >= s["maxConcurrentJobs"]
                    if wait <= 0 and not full:
                        starts.append(now)
                        return
                    if now + max(wait, 0) > start + s["maxWaitSeconds"] or (full and now > start + s["maxWaitSeconds"]):
                        retry = int(math.ceil(max(wait, 10)))
                        if wait <= 0:
                            reason = "the server is busy ({} analyses running)".format(len(self.running))
                        raise LimitError(429, "Too many GREAT requests: {}. Try again in {} s.".format(reason, retry),
                                         retry)
                    self.lock.wait(min(max(wait, 0.2), 5.0) if wait > 0 else 1.0)
            finally:
                self.waiting.pop(ticket, None)

    # ---------- jobs

    def job_dir(self, job_id):
        if not JOB_ID.fullmatch(job_id or ""):
            raise FileNotFoundError("No GREAT job {}".format(job_id))
        folder = self.jobs_dir / job_id
        if not (folder / "job.json").is_file():
            raise FileNotFoundError("GREAT job {} does not exist or has expired".format(job_id))
        return folder

    def job(self, job_id):
        return json.loads((self.job_dir(job_id) / "job.json").read_text())

    def job_records(self):
        """Every stored job, newest first: id, name, owner, assembly, regions, size, age (for the admin panel)."""
        records = []
        if not self.jobs_dir.is_dir():
            return records
        now = time.time()
        for folder in self.jobs_dir.iterdir():
            if not JOB_ID.fullmatch(folder.name):
                continue
            size, newest = 0, 0
            try:
                for item in folder.iterdir():
                    stat = item.stat()
                    size += stat.st_size
                    newest = max(newest, stat.st_mtime)
                job = json.loads((folder / "job.json").read_text())
            except (OSError, ValueError):
                job = {}
            created = job.get("created") or newest or now
            records.append({
                "id": folder.name, "name": job.get("name", ""), "owner": job.get("owner"), "client": job.get("client", ""),
                "assembly": job.get("assembly", ""), "source": job.get("source", ""), "status": job.get("status", "?"),
                "regions": (job.get("summary") or {}).get("regions"), "background": job.get("background", ""),
                "created": created, "ageDays": round((now - created) / 86400, 2), "bytes": size,
                "running": folder.name in self.running,
            })
        records.sort(key=lambda r: -r["created"])
        return records

    def delete_jobs(self, job_ids, reason="admin"):
        """Delete stored jobs (never running ones); returns {"removed", "freedBytes"}."""
        wanted = set(job_ids)
        removed, freed = 0, 0
        for record in self.job_records():
            if record["id"] in wanted and not record["running"]:
                shutil.rmtree(str(self.jobs_dir / record["id"]), ignore_errors=True)
                removed += 1
                freed += record["bytes"]
        result = {"removed": removed, "freedBytes": freed}
        if removed:
            self.last_cleanup = dict(result, time=time.time(), reason=reason)
        return result

    def cleanup(self, action, days=None, job_ids=None, owner=None):
        """The admin panel's cleanup: "expired" (older than the retention, then the storage limit), "olderThan"
        days, "all", "owner" (one workspace's jobs) or "jobs" (the given ids)."""
        records = self.job_records()
        if action == "expired":
            return self.sweep(force=True)
        if action == "olderThan":
            if days is None or float(days) < 0:
                raise ValueError("Give the age in days")
            ids = [r["id"] for r in records if r["ageDays"] > float(days)]
        elif action == "all":
            ids = [r["id"] for r in records]
        elif action == "owner":
            ids = [r["id"] for r in records if r["owner"] == owner]
        elif action == "jobs":
            ids = [i for i in (job_ids or []) if JOB_ID.fullmatch(str(i))]
        else:
            raise ValueError("Unknown cleanup {!r}".format(action))
        return self.delete_jobs(ids, reason="admin: " + action)

    def sweep(self, force=False):
        """Delete jobs older than the retention, then the oldest until the storage limit holds. Runs every
        CLEANUP_INTERVAL_SECONDS (start_maintenance) and after analyses."""
        now = time.time()
        if not force and now - self.last_sweep < CLEANUP_INTERVAL_SECONDS:
            return {"removed": 0, "freedBytes": 0}
        self.last_sweep = now
        settings = self.settings()
        limit = float(settings["jobRetentionDays"]) * 86400
        records = [r for r in self.job_records() if not r["running"]]
        expired = [r["id"] for r in records if now - r["created"] > limit]
        quota = float(settings["maxStorageMB"]) * 1048576
        over = []
        if quota > 0:
            kept = [r for r in records if r["id"] not in set(expired)]
            total = sum(r["bytes"] for r in kept)
            for record in sorted(kept, key=lambda r: r["created"]):  # oldest first
                if total <= quota:
                    break
                over.append(record["id"])
                total -= record["bytes"]
        reason = "automatic: " + ", ".join(x for x, ids in (("retention", expired), ("storage limit", over)) if ids)
        return self.delete_jobs(expired + over, reason=reason)

    def start_maintenance(self):
        """The automatic cleanup, in a background thread (the server calls this once)."""
        if self.maintenance:
            return

        def loop():
            while True:
                try:
                    self.sweep(force=True)
                except Exception as exc:  # never stop the loop
                    print("GREAT: cleanup failed: {}".format(exc), file=sys.stderr)
                time.sleep(CLEANUP_INTERVAL_SECONDS)

        self.maintenance = threading.Thread(target=loop, name="great-cleanup", daemon=True)
        self.maintenance.start()

    def submit(self, assembly, fg_bytes, bg_bytes=None, raw_params=None, client="", owner=None, source="web",
               name="", bg_name=""):
        """Read the regions, wait for the limits, run the analysis; returns the finished job (job.json)."""
        s = self.settings()
        if not self.available(assembly):
            raise great.InputError("Assembly {} is not available on this server (available: {})".format(
                assembly, ", ".join(a["name"] for a in self.assemblies()) or "none; run tools/fetch_great_data.py"))
        params = great.normalized_params(raw_params or {})
        sizes = self._sizes(assembly)
        maximum = int(s["maxUploadMB"]) * 1048576
        fg, fg_report = great.read_regions(fg_bytes, sizes, params["coordinates"], maximum, int(s["maxRegions"]))
        bg, bg_report, warnings = None, None, []
        if bg_bytes is not None:
            bg, bg_report = great.read_regions(bg_bytes, sizes, params["coordinates"], maximum,
                                               int(s["maxBackgroundRegions"]))
            known = {r[:3] for r in bg}
            missing = [r for r in fg if r[:3] not in known]
            if missing:
                warnings.append("{:,} test regions were not in the background set and were added to it (GREAT "
                                "requires the test set to be a subset of the background).".format(len(missing)))
                bg = bg + [r for r in {r[:3]: r for r in missing}.values()]
        if len(fg) > 5000 and bg is None:
            warnings.append("Large input ({:,} regions): the hypergeometric test over genes may saturate; GREAT "
                            "recommends the binomial test (or the top few thousand regions).".format(len(fg)))
        self.admit(client)
        job_id = time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(4)
        with self.lock:
            self.running[job_id] = {"client": client, "assembly": assembly, "started": time.time(),
                                    "regions": len(fg), "source": source}
        folder = self.jobs_dir / job_id
        try:
            folder.mkdir(parents=True)
            job = {"id": job_id, "version": great.GREAT_VERSION, "created": time.time(), "assembly": assembly,
                   "params": params, "rule": great.rule_description(params), "name": name or "Test regions",
                   "backgroundName": bg_name or ("Background regions" if bg is not None else ""),
                   "background": "regions" if bg is not None else "wholeGenome", "client": client, "owner": owner,
                   "source": source, "report": fg_report, "backgroundReport": bg_report, "warnings": warnings,
                   "status": "running"}
            (folder / "job.json").write_text(json.dumps(job))
            great.write_job_regions(folder, "fg.tsv.gz", fg)
            if bg is not None:
                great.write_job_regions(folder, "bg.tsv.gz", bg)
            started = time.time()
            self.pool.call({"op": "analyze", "job": str(folder)}, assembly=assembly)
            job = json.loads((folder / "job.json").read_text())
            job["seconds"] = round(time.time() - started, 2)
            (folder / "job.json").write_text(json.dumps(job))
        except BaseException:
            shutil.rmtree(str(folder), ignore_errors=True)
            raise
        finally:
            with self.lock:
                info = self.running.pop(job_id, None)
                if info:
                    info.update(id=job_id, seconds=round(time.time() - info["started"], 2))
                    self.recent.appendleft(info)
                self.lock.notify_all()
        self.sweep()
        return job

    def _sizes(self, assembly):
        sizes = OrderedDict()
        with open(str(self.data_dir / assembly / "chrom.sizes")) as handle:
            for line in handle:
                f = line.split()
                if len(f) >= 2:
                    sizes[f[0]] = int(f[1])
        return sizes

    def rows(self, job_id, ontology):
        path = self.job_dir(job_id) / (ontology + ".json.gz")
        if not re.fullmatch(r"[A-Za-z0-9]+", ontology) or not path.is_file():
            raise FileNotFoundError("No ontology {} in GREAT job {}".format(ontology, job_id))
        return great.read_json_gz(path)

    def term(self, job_id, ontology, term_id):
        job = self.job(job_id)
        return self.pool.call({"op": "term", "job": str(self.job_dir(job_id)), "ontology": ontology,
                               "term": term_id}, assembly=job["assembly"], timeout=300)

    def names(self, job_id, ontology, term_ids):
        job = self.job(job_id)
        return self.pool.call({"op": "names", "job": str(self.job_dir(job_id)), "ontology": ontology,
                               "terms": list(term_ids)}, assembly=job["assembly"], timeout=600)

    def jobs_of(self, owner, limit=50):
        out = []
        if not owner or not self.jobs_dir.is_dir():
            return out
        for folder in sorted(self.jobs_dir.iterdir(), reverse=True):
            if not JOB_ID.fullmatch(folder.name):
                continue
            try:
                job = json.loads((folder / "job.json").read_text())
            except (OSError, ValueError):
                continue
            if job.get("owner") == owner:
                out.append(public_job(job))
                if len(out) >= limit:
                    break
        return out

    def delete(self, job_id, owner):
        job = self.job(job_id)
        if not owner or job.get("owner") != owner:
            raise PermissionError("Only the workspace that ran this analysis can delete it")
        shutil.rmtree(str(self.job_dir(job_id)), ignore_errors=True)

    def status(self):
        with self.lock:
            now = time.time()
            running = [dict(v, id=k, seconds=round(now - v["started"], 1)) for k, v in self.running.items()]
            waiting = [dict(v, seconds=round(now - v["since"], 1)) for v in self.waiting.values()]
            recent = list(self.recent)[:50]
            clients = sorted(((c, len(d)) for c, d in self.history.items() if d), key=lambda x: -x[1])[:50]
        records = self.job_records()
        return {"running": running, "waiting": waiting, "recent": recent,
                "clientsLastHour": [{"client": c, "jobs": n} for c, n in clients], "storedJobs": len(records),
                "storedBytes": sum(r["bytes"] for r in records), "workers": self.pool.status(),
                "dataDir": str(self.data_dir), "jobsDir": str(self.jobs_dir), "lastCleanup": self.last_cleanup,
                "nextCleanup": self.last_sweep + CLEANUP_INTERVAL_SECONDS if self.maintenance else None}


def public_job(job):
    """job.json without the client address, for pages and the API."""
    return {k: v for k, v in job.items() if k not in ("client",)}


# ---------------------------------------------------------------- forms (multipart without the cgi module)

def parse_form(content_type, body):
    """{name: value} from a POST body; file fields are bytes, others str."""
    content_type = content_type or ""
    fields = {}
    if content_type.lower().startswith("multipart/form-data"):
        message = BytesParser(policy=HTTP).parsebytes(
            b"Content-Type: " + content_type.encode("latin-1") + b"\r\nMIME-Version: 1.0\r\n\r\n" + body)
        for part in message.iter_parts():
            name = part.get_param("name", header="content-disposition")
            if not name:
                continue
            payload = part.get_payload(decode=True) or b""
            if part.get_filename() is not None:
                fields[name] = payload
                fields[name + "__filename"] = part.get_filename()
            else:
                fields[name] = payload.decode("utf-8", errors="replace")
    else:
        for name, values in parse_qs(body.decode("utf-8", errors="replace"), keep_blank_values=True).items():
            fields[name] = values[0]
    return fields


# ---------------------------------------------------------------- GREAT's output formats

def ontology_keys(job):
    return [(o["key"], o["name"], o["category"]) for o in job.get("summary", {}).get("ontologies", [])]


def session_page(job, ui_url):
    """greatWeb.php's answer: the session name and ontology list rGREAT reads, and a link for people."""
    categories = OrderedDict()
    for key, name, category in ontology_keys(job):
        categories.setdefault(category, []).append((key, name))
    columns = list(categories)
    depth = max((len(v) for v in categories.values()), default=0)
    cells = ['<div class="gc_header gc_column">{}</div>'.format(html.escape(c)) for c in columns]
    for row in range(depth):  # rGREAT assigns the n-th item to category (n mod number of categories)
        for c in columns:
            items = categories[c]
            if row < len(items):
                key, name = items[row]
                cells.append('<div class="gc_info_item gc_column"><input type="checkbox" value="{}" checked>{}'
                             '</div>'.format(key, html.escape(name)))
            else:
                cells.append('<div class="gc_info_item gc_column"></div>')
    warnings = "".join("<p><strong>Warning:</strong> {}</p>".format(html.escape(w)) for w in job.get("warnings", []))
    return ("<!doctype html><html><head><meta charset=\"utf-8\"><title>GREAT job {id}</title>"
            "<script>var _sessionName = \"{id}\";</script></head><body>"
            "<p>GREAT {version} (Omics Canvas): job <a href=\"{url}\">{id}</a> finished: {regions} regions, {assembly}.</p>"
            "{warnings}<div class=\"gc_ontologies\">{cells}</div>"
            "<script>if (!/rgreat|curl|wget|python/i.test(navigator.userAgent)) location.replace({url_js});</script>"
            "</body></html>").format(
        id=job["id"], version=great.GREAT_VERSION, url=html.escape(ui_url), regions=job["summary"]["regions"],
        assembly=html.escape(job["assembly"]), warnings=warnings, cells="".join(cells), url_js=json.dumps(ui_url))


def error_page(message):
    """GREAT's user-error page (rGREAT shows the Error details text)."""
    return ("<!doctype html><html><head><meta charset=\"utf-8\"><title>GREAT error</title></head><body>"
            "<p>GREAT encountered a user error:</p><blockquote><b>Error details:</b> {}</blockquote></body></html>"
            ).format(html.escape(message))


def _g(value):
    if isinstance(value, float):
        return "{:.6g}".format(value)
    return str(value)


def export_header(job, columns):
    params = job["params"]
    fg = "{} ({:,} regions)".format(job.get("name") or "Test regions", job["summary"]["regions"])
    bg = "whole genome" if job.get("background") != "regions" else "{} ({:,} regions)".format(
        job.get("backgroundName") or "Background regions", job["summary"].get("backgroundRegions", 0))
    return [
        "# GREAT version {} (Omics Canvas)\tSpecies assembly: {}\tAssociation rule: {}".format(
            great.GREAT_VERSION, job["assembly"], job.get("rule") or great.rule_description(params)),
        "# Test set: {}\tBackground: {}\tJob: {}".format(fg, bg, job["id"]),
        "# Output generated on {}".format(time.strftime("%Y-%m-%d %H:%M:%S %Z")),
        "# " + "\t".join(columns),
    ]


WHOLE_GENOME_COLUMNS = ["Ontology", "ID", "Desc", "BinomRank", "BinomP", "BinomBonfP", "BinomFdrQ", "RegionFoldEnrich",
                        "ExpRegions", "ObsRegions", "GenomeFrac", "SetCov", "HyperRank", "HyperP", "HyperBonfP",
                        "HyperFdrQ", "GeneFoldEnrich", "ExpGenes", "ObsGenes", "TotalGenes", "GeneSetCov", "TermCov",
                        "Regions", "Genes"]
BACKGROUND_COLUMNS = ["Ontology", "ID", "Desc", "Rank", "HyperP", "HyperBonfP", "HyperFdrQ", "RegionFoldEnrich",
                      "ExpRegions", "ObsRegions", "TotalRegions", "SetCov", "TermCov", "FgGeneHits", "BgGeneHits",
                      "TotalGenes", "Regions", "Genes"]


def export_rows(service, job, key, name, limit, min_count=1, max_count=None):
    """GREAT's "All data as .tsv" rows for one ontology: terms hit by one or more regions, best first. Terms
    annotated to fewer than min_count or more than max_count genes are not tested (GREAT's annotation count)."""
    rows = service.rows(job["id"], key)
    total_column = 10 if job.get("background") == "regions" else 8
    rows = [r for r in rows if r[total_column] >= min_count and (max_count is None or r[total_column] <= max_count)]
    background = job.get("background") == "regions"
    p_column = 11 if background else 7
    ranks, bonferroni, fdr = great.corrections([r[p_column] for r in rows])
    if not background:
        h_ranks, h_bonferroni, h_fdr = great.corrections([r[14] for r in rows])
    order = sorted((i for i, r in enumerate(rows) if r[4] > 0), key=lambda i: (rows[i][p_column], i))
    if limit:
        order = order[:limit]
    names = service.names(job["id"], key, [rows[i][0] for i in order]) if order else {}
    lines = []
    for i in order:
        r = rows[i]
        regions, genes = names.get(r[0], ([], []))
        if background:
            values = [name, r[0], r[1], ranks[i], r[11], bonferroni[i], fdr[i], r[5], r[3], r[4], r[2], r[6], r[7],
                      r[8], r[9], r[10], ",".join(regions), ",".join(genes)]
        else:
            values = [name, r[0], r[1], ranks[i], r[7], bonferroni[i], fdr[i], r[5], r[3], r[4], r[2], r[6],
                      h_ranks[i], r[14], h_bonferroni[i], h_fdr[i], r[11], r[9], r[10], r[8], r[12], r[13],
                      ",".join(regions), ",".join(genes)]
        lines.append("\t".join(_g(v).replace("\t", " ") for v in values))
    return lines, len(rows)


def export_tsv(service, job, keys=None, limit=None, min_count=1, max_count=None):
    """All data as .tsv (one or all ontologies), GREAT's format: header, rows, footer."""
    background = job.get("background") == "regions"
    columns = BACKGROUND_COLUMNS if background else WHOLE_GENOME_COLUMNS
    lines = export_header(job, columns)
    footer = ["# Ontology\tTerms tested\tMinimum annotation count\tMaximum annotation count"]
    if limit is None:
        limit = int(service.settings()["exportTermLimit"])
    for key, name, _ in ontology_keys(job):
        if keys and key not in keys:
            continue
        rows, tested = export_rows(service, job, key, name, limit, min_count, max_count)
        lines.extend(rows)
        footer.append("# {}\t{}\t{}\t{}".format(name, tested, min_count, "Inf" if max_count is None else max_count))
    return "\n".join(lines + footer) + "\n"


def association_table(service, job, kind="region", ontology=None, term=None):
    """downloadAssociations.php: region -> "GENE (+distance), ..." (or NONE), or gene -> regions."""
    params = job["params"]
    header = "# GREAT version {} (Omics Canvas)\tSpecies assembly: {}\tAssociation rule: {}".format(
        great.GREAT_VERSION, job["assembly"], job.get("rule") or great.rule_description(params))
    folder = service.job_dir(job["id"])
    if ontology and term:
        details = service.term(job["id"], ontology, term)
        if kind == "gene":
            lines = ["{}\t{}".format(g["gene"], ", ".join("{} ({:+d})".format(r, d) for r, d in g["regions"]))
                     for g in details["byGene"]]
        else:
            lines = ["{}\t{}".format(r["region"], ", ".join("{} ({:+d})".format(g, d) for g, d in r["genes"]))
                     for r in details["byRegion"]]
        return "\n".join([header, "# Term: {} {} ({})".format(term, details["name"], details["ontologyName"])] +
                         lines) + "\n"
    symbols = [g[0] for g in _symbols(service, job["assembly"])]
    associations = great.read_associations(folder)
    if kind == "gene":
        by_gene = OrderedDict()
        for label, pairs in associations:
            for g, d in pairs:
                by_gene.setdefault(g, []).append((label, d))
        lines = ["{}\t{}".format(symbols[g], ", ".join("{} ({:+d})".format(r, d) for r, d in items))
                 for g, items in sorted(by_gene.items(), key=lambda x: symbols[x[0]])]
    else:
        lines = ["{}\t{}".format(label, ", ".join("{} ({:+d})".format(symbols[g], d) for g, d in pairs) or "NONE")
                 for label, pairs in associations]
    return "\n".join([header] + lines) + "\n"


_SYMBOLS = {}


def _symbols(service, assembly):
    path = service.data_dir / assembly / "genes.tsv"
    stamp = path.stat().st_mtime_ns
    if _SYMBOLS.get(assembly, (None,))[0] != stamp:
        genes = []
        with open(str(path)) as handle:
            for line in handle:
                if not line.startswith("#") and line.strip():
                    f = line.rstrip("\n").split("\t")
                    genes.append((f[4], f[0]))
        _SYMBOLS[assembly] = (stamp, genes)
    return _SYMBOLS[assembly][1]


def symbols(service, assembly):
    """[(symbol, Ensembl id)] in the assembly's gene order."""
    return _symbols(service, assembly)


def term_bed(service, job, ontology, term):
    """The test regions behind one term as BED (name = region; genes in the name)."""
    details = service.term(job["id"], ontology, term)
    lines = ['track name="GREAT {}" description="{} - {}"'.format(term, details["name"][:60].replace('"', "'"),
                                                                  job.get("name", "")[:40].replace('"', "'"))]
    for r in details["byRegion"]:
        lines.append("{}\t{}\t{}\t{}".format(r["chrom"], r["start"], r["end"],
                                             r["region"] + ":" + "|".join(g for g, _ in r["genes"])))
    return "\n".join(lines) + "\n"


def regions_bed(service, job):
    folder = service.job_dir(job["id"])
    regions = great.read_job_regions(folder, "fg.tsv.gz")
    lines = ['track name="{}" description="GREAT test regions"'.format(job.get("name", "GREAT")[:60].replace('"', "'"))]
    lines.extend("{}\t{}\t{}\t{}".format(r[0], r[1], r[2], great.region_label(r)) for r in regions)
    return "\n".join(lines) + "\n"
