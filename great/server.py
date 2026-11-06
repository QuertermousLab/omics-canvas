#!/usr/bin/env python3
"""GREAT server of Omics Canvas: the GREAT page, its API and GREAT's own interfaces, all under /great/.

    python3 server.py --host 127.0.0.1 --port 8894 --canvas http://127.0.0.1:8000

The gateway routes http://<server>:8892/great/ here (paths unchanged). Routes:
  /great/                      the page (web/index.html; @CANVAS_BASE@ = Genome Canvas's address, whose workspace and
                               file APIs the page calls)
  /great/api/...               the page's API (jobs, results, term details, exports, settings, admin)
  /great/public*/cgi-bin/*.php GREAT's interfaces: greatWeb.php, readJsFromFile.php, downloadAllTSV.php,
                               downloadAssociations.php, showTermDetails.php (rGREAT), greatStart.php (batch API)
  /great/rgreat.R              points rGREAT at this server for an R session

Sign-in: with OMICS_CANVAS_AUTH set (the lab server's unit), the page and its API need an Omics Canvas session
(../auth/sessions.py); GREAT's .php interfaces, rgreat.R and /great/api/health stay open, and never read server files.
Administrators are the sign-in's. Workspaces are Genome Canvas's: the page sends the selected one in
X-Vizhub-Workspace (as the hub's pages do) and the server checks it against Genome Canvas's workspace list. Regions from Genome Canvas files are read through its
workspace-scoped /data/ URLs. great.py computes, great_service.py runs jobs and holds the live limits.
Standard library only (Python 3.8+).
"""

import argparse
import gzip
import ipaddress
import json
import os
import re
import socket
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, unquote, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

import great as great_tools
import great_service

APP_DIR = Path(__file__).resolve().parent
WEB_DIR = APP_DIR / "web"
CONFIG_FILE = APP_DIR / "great.config.json"
STATIC = {
    "great.mjs": "text/javascript; charset=utf-8",
    "great.css": "text/css; charset=utf-8",
    "base.css": "text/css; charset=utf-8",
    "favicon.svg": "image/svg+xml",
}
COMPAT = re.compile(r"/great/public(?:-[0-9.]+)?/cgi-bin/([A-Za-z]+)\.php")
WORKSPACE_ID = re.compile(r"(?:home|manual)-[a-f0-9]{18}|local")
GZIP_MIN_BYTES = 1400
RGREAT_HELPER = r"""# Use rGREAT's online functions with the GREAT server of Omics Canvas (great.stanford.edu is offline).
#   library(rGREAT)
#   source("@SERVER@/rgreat.R")
#   job <- submitGreatJob(gr, genome = "hg38", request_interval = 0)   # the server applies its own limits
#   tables <- getEnrichmentTables(job)                                    # also getRegionGeneAssociations(job)
# It points rGREAT's server addresses (BASE_URL_LIST, every GREAT version) at this server for this R session.
local({
  server <- "@SERVER@"
  ns <- asNamespace("rGREAT")
  urls <- get("BASE_URL_LIST", envir = ns)
  urls[] <- sub("^https?://great\\.stanford\\.edu", server, urls)
  if (bindingIsLocked("BASE_URL_LIST", ns)) {
    unlockBinding("BASE_URL_LIST", ns)
    on.exit(lockBinding("BASE_URL_LIST", ns))
  }
  assign("BASE_URL_LIST", urls, envir = ns)
  message("rGREAT now uses ", server, " (GREAT 4.0.4 compatible, Omics Canvas)")
})
"""


def load_sign_in():
    """(Gate, module) of Omics Canvas sign-in when OMICS_CANVAS_AUTH names its state folder, else (None, None)."""
    folder = os.environ.get("OMICS_CANVAS_AUTH", "").strip()
    module_path = APP_DIR.parent / "auth" / "sessions.py"
    if not folder or not module_path.is_file():
        return None, None
    import importlib.util
    spec = importlib.util.spec_from_file_location("omics_canvas_sessions", str(module_path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Gate(folder), module


def read_config():
    try:
        return json.loads(CONFIG_FILE.read_text())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        print("Warning: could not read {}: {}".format(CONFIG_FILE, exc), file=sys.stderr)
        return {}


# ---------------------------------------------------------------- public URLs (requestURL, the page's URL input)

def validate_public_url(url):
    """Only public http(s) addresses are fetched: never this server's network neighbours."""
    parsed = urlparse(str(url))
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("not a public http(s) URL")
    try:
        addresses = {item[4][0].split("%", 1)[0] for item in socket.getaddrinfo(
            parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)}
    except socket.gaierror:
        raise ValueError("the host could not be resolved")
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_unspecified:
            raise ValueError("a private or local address")
    return parsed.geturl()


class SafeRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


PUBLIC_OPENER = build_opener(SafeRedirects())


# ---------------------------------------------------------------- Genome Canvas (workspaces, files)

class Canvas:
    """Genome Canvas at `url` (internal), published at `path` (/genome-canvas/ behind the gateway)."""

    def __init__(self, url, path):
        self.url = url.rstrip("/")
        self.path = "/" + path.strip("/") + "/" if path.strip("/") else "/"
        self.lock = Lock()
        self.cache = (0, [])

    def workspaces(self):
        with self.lock:
            stamp, items = self.cache
            if time.time() - stamp < 30:
                return items
        try:
            with build_opener().open(self.url + "/api/workspaces", timeout=5) as response:
                items = json.loads(response.read().decode("utf-8")).get("workspaces", [])
        except (OSError, ValueError):
            items = self.cache[1]
        with self.lock:
            self.cache = (time.time(), items)
        return items

    def workspace(self, workspace_id):
        if not workspace_id or not WORKSPACE_ID.fullmatch(workspace_id):
            return None
        return next((w for w in self.workspaces() if w.get("id") == workspace_id), None)

    def fetch(self, data_path, maximum):
        """A file through Genome Canvas's /data/ URLs."""
        try:
            with build_opener().open(self.url + data_path, timeout=120) as response:
                body = response.read(maximum + 1)
        except HTTPError as exc:
            raise great_tools.InputError("Genome Canvas could not read the file (HTTP {})".format(exc.code))
        except (URLError, OSError) as exc:
            raise great_tools.InputError("Genome Canvas could not be reached: {}".format(getattr(exc, "reason", exc)))
        if len(body) > maximum:
            raise great_tools.InputError("The regions file is larger than this server's limit of {} MB".format(
                maximum // 1048576))
        return body


# ---------------------------------------------------------------- the handler

class GreatHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "GREAT/" + great_tools.GREAT_VERSION

    def log_message(self, fmt, *args):
        print("{} - {}".format(self.address_string(), fmt % args))

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        super().end_headers()

    @property
    def great(self):
        return self.server.great

    @property
    def canvas(self):
        return self.server.canvas

    # ---------- responses

    def accepts_gzip(self):
        return any(t.strip().split(";")[0].strip().lower() == "gzip" and "q=0" not in t.replace(" ", "")
                   for t in self.headers.get("Accept-Encoding", "").split(","))

    def send_body(self, body, content_type, status=200, headers=None, filename=None, cache="no-store"):
        body = body.encode("utf-8") if isinstance(body, str) else body
        compressed = len(body) >= GZIP_MIN_BYTES and self.accepts_gzip() and not content_type.startswith(("image/", "font/"))
        if compressed:
            body = gzip.compress(body, compresslevel=5, mtime=0)
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.send_header("Vary", "Accept-Encoding")
        if compressed:
            self.send_header("Content-Encoding", "gzip")
        if filename:
            self.send_header("Content-Disposition", 'attachment; filename="{}"'.format(re.sub(r"[^A-Za-z0-9._-]+", "_", filename)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def send_text(self, text, content_type="text/plain; charset=utf-8", status=200, headers=None, filename=None):
        self.send_body(text, content_type, status, headers, filename)

    def send_json(self, payload, status=200, headers=None):
        self.send_body(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), "application/json; charset=utf-8",
                       status, headers)

    def send_api_error(self, status, message):
        self.send_json({"error": message}, status)

    def send_redirect(self, location, status=302):
        self.send_response(status)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    # ---------- request facts

    def client(self):
        """The client's address: the last X-Forwarded-For entry (added by the gateway) for proxied requests."""
        address = self.client_address[0]
        forwarded = self.headers.get("X-Forwarded-For", "")
        try:
            proxied = ipaddress.ip_address(address).is_loopback
        except ValueError:
            proxied = False
        if forwarded and proxied:
            address = forwarded.split(",")[-1].strip() or address
        return address

    def origin(self):
        host = self.headers.get("X-Forwarded-Host", "").split(",")[0].strip() or self.headers.get("Host", "")
        if not re.fullmatch(r"[A-Za-z0-9.\-:\[\]]{1,255}", host or ""):
            host = "{}:{}".format(*self.server.server_address[:2])
        scheme = self.headers.get("X-Forwarded-Proto", "").split(",")[0].strip().lower()
        return "{}://{}".format(scheme if scheme in ("http", "https") else "http", host)

    def public_base(self):
        """Where GREAT is reached, without a trailing slash: <origin>/great."""
        return self.origin() + "/great"

    def ui_url(self, job_id, extra=""):
        return "{}/#job={}{}".format(self.public_base(), job_id, extra)

    def same_origin(self):
        origin = self.headers.get("Origin")
        return not origin or urlparse(origin).netloc == self.headers.get("Host", "")

    def workspace(self):
        return self.canvas.workspace(self.headers.get("X-Vizhub-Workspace", "").strip())

    def read_body(self):
        if self.headers.get("Transfer-Encoding"):
            self.close_connection = True
            raise great_tools.InputError("Send the request with a Content-Length header")
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise great_tools.InputError("Invalid Content-Length")
        limit = 2 * int(self.great.settings()["maxUploadMB"]) * 1048576 + 1048576  # a test and a background file
        if length > limit:
            self.close_connection = True
            raise great_service.LimitError(413, "The upload is larger than this server's limit of {} MB per file".format(
                self.great.settings()["maxUploadMB"]))
        return self.rfile.read(length) if length > 0 else b""

    def read_json_body(self, maximum):
        body = self.read_body()
        if not body or len(body) > maximum:
            raise ValueError("Send a JSON object of at most {} kB".format(maximum // 1024))
        payload = json.loads(body.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Send a JSON object")
        return payload

    # ---------- inputs

    def maximum_bytes(self):
        return int(self.great.settings()["maxUploadMB"]) * 1048576

    def own_url(self, url):
        netloc = urlparse(url.strip()).netloc
        return not netloc or netloc in {self.headers.get("Host", ""), self.headers.get("X-Forwarded-Host", "").split(",")[0].strip()}

    def url_bytes(self, url):
        """A regions file from a URL: this server's Genome Canvas files through Genome Canvas, others fetched
        (public addresses only)."""
        if self.own_url(url):
            if not self.server_files:
                raise great_tools.InputError("GREAT's programming interface reads uploaded regions and public URLs, "
                                             "not this server's files")
            return self.track_bytes(url)
        if not self.great.settings()["allowRemoteURLs"]:
            raise great_tools.InputError("This server does not fetch regions from URLs; upload the file instead")
        try:
            validate_public_url(url)
        except ValueError:
            raise great_tools.InputError("The regions URL must be a public http(s) address")
        maximum = self.maximum_bytes()
        try:
            with PUBLIC_OPENER.open(Request(url, headers={"User-Agent": "OmicsCanvas-GREAT/1.0"}), timeout=60) as response:
                body = response.read(maximum + 1)
        except HTTPError as exc:
            raise great_tools.InputError("The regions URL returned HTTP {}".format(exc.code))
        except (URLError, TimeoutError, socket.timeout, ValueError, OSError) as exc:
            raise great_tools.InputError("Could not read the regions URL: {}".format(getattr(exc, "reason", exc)))
        if len(body) > maximum:
            raise great_tools.InputError("The regions URL holds more than {} MB".format(self.great.settings()["maxUploadMB"]))
        return body

    def track_bytes(self, url):
        """A Genome Canvas track file named by its URL (…/genome-canvas/data/<workspace>/<root>/<path>); a track
        loaded from a public address is fetched from there."""
        if not self.own_url(url):
            return self.url_bytes(url)
        path = urlparse(url.strip()).path
        if path.startswith(self.canvas.path + "data/"):
            path = "/" + path[len(self.canvas.path):]
        elif not path.startswith("/data/"):
            raise great_tools.InputError("Not a Genome Canvas track URL: {}".format(url))
        parts = path.split("/")  # "", "data", <workspace or root>, ...
        workspace = self.workspace()
        if len(parts) > 3 and not WORKSPACE_ID.fullmatch(unquote(parts[2])) and workspace:
            parts.insert(2, quote(workspace["id"]))  # an older unscoped URL: read it in the page's workspace
        return self.canvas.fetch("/".join(parts), self.maximum_bytes())

    def server_file_bytes(self, root, path):
        workspace = self.workspace()
        if not workspace:
            raise PermissionError("Select a Genome Canvas workspace to analyze its files")
        data_path = "/data/{}/{}/{}".format(quote(workspace["id"]), quote(root, safe=""),
                                            "/".join(quote(p, safe="") for p in path.strip("/").split("/")))
        return self.canvas.fetch(data_path, self.maximum_bytes())

    server_files = True  # False for GREAT's open interfaces: they never read this server's files

    def input(self, fields, prefix):
        """(bytes, label) from the form fields <prefix>File, Text, Track, Root+Path or URL; (None, "") if none."""
        upload = fields.get(prefix + "File")
        if isinstance(upload, bytes) and upload:
            return upload, fields.get(prefix + "File__filename") or "uploaded regions"
        text = fields.get(prefix + "Text") or fields.get(prefix + "Data")
        if isinstance(text, bytes):
            text = text.decode("utf-8", errors="replace")
        if text and text.strip():
            return text.encode("utf-8"), "pasted regions"
        if not self.server_files and (fields.get(prefix + "Track") or fields.get(prefix + "Root") or fields.get(prefix + "Path")):
            raise great_tools.InputError("GREAT's programming interface reads uploaded regions and public URLs, not this "
                                         "server's files")
        if fields.get(prefix + "Track"):
            track = fields[prefix + "Track"]
            return self.track_bytes(track), unquote(Path(urlparse(track).path).name)
        if fields.get(prefix + "Root") and fields.get(prefix + "Path"):
            return self.server_file_bytes(fields[prefix + "Root"], fields[prefix + "Path"]), Path(fields[prefix + "Path"]).name
        if fields.get(prefix + "URL"):
            return self.url_bytes(fields[prefix + "URL"]), Path(urlparse(fields[prefix + "URL"]).path).name or "URL"
        return None, ""

    # ---------- routing

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        self.route("GET")

    def do_POST(self):
        self.route("POST")

    def do_DELETE(self):
        self.route("DELETE")

    def route(self, method):
        self.server_files, self.identity = True, None  # a handler serves every request of its connection
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        query = parse_qs(parsed.query)
        try:
            match = COMPAT.fullmatch(path)
            if match:
                self.server_files = False
                self.handle_compat(method, match.group(1), query)
            elif not self.signed_in(path):
                return
            elif path in ("/", "/great"):
                self.send_redirect("/great/", 301)
            elif path in ("/great/", "/great/index.html") and method == "GET":
                page = (WEB_DIR / "index.html").read_text(encoding="utf-8").replace("@CANVAS_BASE@", self.canvas.path)
                self.send_text(page, "text/html; charset=utf-8")
            elif path == "/great/rgreat.R":
                self.send_text(RGREAT_HELPER.replace("@SERVER@", self.public_base()))
            elif path == "/great/api/health":
                self.send_json({"ok": True, "version": great_tools.GREAT_VERSION})
            elif path.startswith("/great/api/"):
                self.handle_api(method, path[len("/great/api/"):], query)
            elif method == "GET" and path[len("/great/"):] in STATIC:
                name = path[len("/great/"):]
                self.send_body((WEB_DIR / name).read_bytes(), STATIC[name],
                               cache="public, max-age=86400, immutable" if "v" in query else "no-cache")
            elif method == "GET" and re.fullmatch(r"/great/fonts/[a-z0-9-]+\.woff2", path):
                self.send_body((WEB_DIR / "fonts" / Path(path).name).read_bytes(), "font/woff2",
                               cache="public, max-age=31536000, immutable")
            else:
                self.send_api_error(404, "Not found")
        except great_tools.InputError as exc:
            self.send_api_error(400, str(exc))
        except great_service.LimitError as exc:
            headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after else None
            self.send_json({"error": str(exc), "retryAfter": exc.retry_after}, exc.status, headers)
        except FileNotFoundError as exc:
            self.send_api_error(404, str(exc))
        except PermissionError as exc:
            self.send_api_error(403, str(exc))
        except (OSError, ValueError) as exc:
            self.send_api_error(400, str(exc))
        except RuntimeError as exc:
            print("GREAT error: {}".format(exc), file=sys.stderr)
            self.send_api_error(500, "GREAT could not finish: {}".format(exc))

    def signed_in(self, path):
        """True to answer; otherwise the sign-in page (pages) or HTTP 401 (API) was sent. Open: rgreat.R, health."""
        gate, module = self.server.sign_in
        if gate is None or path in ("/great/rgreat.R", "/great/api/health"):
            return True
        self.identity = gate.identity(self.headers, self.client_address[0])
        if self.identity:
            return True
        if module.wants_html(self.command, self.headers):
            self.send_redirect(module.login_location(self.path))
        else:
            if self.command == "POST":
                self.close_connection = True
            self.send_json({"error": "Sign in to Omics Canvas first", "signIn": "/auth/login"}, 401)
        return False

    identity = None

    def is_admin(self, workspace):
        if self.server.sign_in[0] is not None:
            return bool(self.identity and self.identity.get("admin"))
        return self.great.is_admin(workspace)  # no sign-in (a stand-alone GREAT): Genome Canvas workspaces decide

    def handle_api(self, method, route, query):
        q = lambda name, default="": query.get(name, [default])[0]
        great = self.great
        workspace = self.workspace()
        owner = workspace["id"] if workspace else None
        admin = self.is_admin(workspace)
        if method in ("POST", "DELETE") and not self.same_origin():
            raise PermissionError("Cross-site GREAT requests are not allowed; use the GREAT interfaces under great/public/cgi-bin/")
        if route == "status" and method == "GET":
            settings = great.settings()
            self.send_json({
                "version": great_tools.GREAT_VERSION, "assemblies": great.assemblies(),
                "defaults": great_tools.DEFAULT_PARAMS, "admin": admin, "greatBase": self.public_base(),
                "canvasBase": self.canvas.path, "workspace": workspace and {k: workspace.get(k) for k in ("id", "name", "kind")},
                "user": self.identity and {k: self.identity.get(k) for k in ("user", "method", "admin")},
                "limits": {k: settings[k] for k in ("minIntervalSeconds", "maxJobsPerHour", "maxRegions", "maxBackgroundRegions",
                                                    "maxUploadMB", "jobRetentionDays", "maxStorageMB", "allowRemoteURLs",
                                                    "apiEnabled")},
            })
        elif route == "settings":
            if not admin:
                raise PermissionError("Only an administrator workspace can see or change GREAT's settings")
            if method == "POST":
                self.send_json({"settings": great.update_settings(self.read_json_body(64 * 1024)),
                                "defaults": great_service.DEFAULT_SETTINGS})
            else:
                self.send_json({"settings": great.settings(), "defaults": great_service.DEFAULT_SETTINGS})
        elif route in ("admin", "admin/jobs", "admin/cleanup"):
            if not admin:
                raise PermissionError("Only an administrator workspace can see or clean GREAT's analyses")
            if route == "admin/jobs":
                names = {w.get("id"): w.get("name", "") for w in self.canvas.workspaces()}
                records = great.job_records()
                for record in records:
                    record["ownerName"] = names.get(record["owner"], "") if record["owner"] else ""
                settings = great.settings()
                self.send_json({"jobs": records, "totalBytes": sum(r["bytes"] for r in records),
                                "retentionDays": settings["jobRetentionDays"], "maxStorageMB": settings["maxStorageMB"]})
            elif route == "admin/cleanup" and method == "POST":
                payload = self.read_json_body(1024 * 1024)
                self.send_json(great.cleanup(str(payload.get("action", "")), payload.get("days"), payload.get("jobs"),
                                             payload.get("owner")))
            else:
                self.send_json(great.status())
        elif route == "jobs" and method == "GET":
            self.send_json({"jobs": great.jobs_of(owner)})
        elif route == "jobs" and method == "POST":
            fields = great_service.parse_form(self.headers.get("Content-Type"), self.read_body())
            fg, fg_label = self.input(fields, "fg")
            if fg is None:
                raise great_tools.InputError("Give the test regions: a file, pasted regions, a track or a URL")
            bg, bg_label = None, ""
            if fields.get("bgChoice", "wholeGenome") != "wholeGenome":
                bg, bg_label = self.input(fields, "bg")
                if bg is None:
                    raise great_tools.InputError("Give the background regions, or choose the whole genome")
            job = great.submit(fields.get("assembly", "hg38"), fg, bg, fields, self.client(), owner, "web",
                               fields.get("fgName") or fg_label, fields.get("bgName") or bg_label)
            self.send_json({"job": great_service.public_job(job)})
        elif route.startswith("jobs/"):
            parts = route.split("/")
            job = great.job(parts[1])
            rest = "/".join(parts[2:])
            if method == "DELETE" and not rest:
                great.delete(job["id"], owner)
                self.send_json({"deleted": job["id"]})
            elif not rest:
                self.send_json({"job": great_service.public_job(job)})
            elif len(parts) == 4 and parts[2] == "ontologies":
                self.send_json({"rows": great.rows(job["id"], parts[3])})
            elif rest == "term":
                self.send_json(great.term(job["id"], q("ontology"), q("term")))
            elif rest == "export.tsv":
                keys = [q("ontology")] if q("ontology") else None
                self.send_text(great_service.export_tsv(great, job, keys, limit=0), "text/tab-separated-values; charset=utf-8",
                               filename="great-{}{}.tsv".format(job["id"], "-" + q("ontology") if q("ontology") else ""))
            elif rest == "associations.tsv":
                table = "gene" if q("table") == "gene" else "region"
                self.send_text(great_service.association_table(great, job, table, q("ontology") or None, q("term") or None),
                               "text/tab-separated-values; charset=utf-8",
                               filename="great-{}-{}{}.tsv".format(job["id"], table, "-" + q("term") if q("term") else ""))
            elif rest == "regions.bed":
                self.send_text(great_service.regions_bed(great, job))
            elif rest == "term.bed":
                self.send_text(great_service.term_bed(great, job, q("ontology"), q("term")))
            else:
                raise FileNotFoundError("Not found")
        else:
            raise FileNotFoundError("Not found")

    def handle_compat(self, method, name, query):
        """GREAT's .php interfaces (rGREAT and the GREAT Programming Interface)."""
        great = self.great
        fields = {k: v[0] for k, v in query.items()}
        html_type = "text/html; charset=utf-8"
        try:
            if method == "POST":
                fields.update(great_service.parse_form(self.headers.get("Content-Type"), self.read_body()))
        except (great_tools.InputError, great_service.LimitError) as exc:
            self.send_text(great_service.error_page(str(exc)), html_type, getattr(exc, "status", 200))
            return
        if name == "showTermDetails":
            self.send_redirect(self.ui_url(fields.get("sessionName", ""), "&ontology={}&term={}".format(
                quote(fields.get("ontoName", "")), quote(fields.get("termId", "")))))
            return
        if not great.settings()["apiEnabled"]:
            self.send_text(great_service.error_page("GREAT's programming interface is turned off on this server."),
                           html_type, 403)
            return
        workspace = self.workspace()
        client, owner = self.client(), workspace["id"] if workspace else None
        agent = self.headers.get("User-Agent", "").lower()
        if name == "greatWeb":
            if method == "GET" and not fields:
                self.send_redirect(self.public_base() + "/")
                return
            try:
                fg, label = self.input(fields, "fg")
                if fg is None:
                    raise great_tools.InputError("No test regions were sent (fgFile)")
                bg, bg_label = None, ""
                if fields.get("bgChoice") in ("file", "data"):
                    bg, bg_label = self.input(fields, "bg")
                    if bg is None:
                        raise great_tools.InputError("No background regions were sent (bgFile)")
                job = great.submit(fields.get("species") or "hg19", fg, bg, fields, client, owner,
                                   "rGREAT" if "rgreat" in agent or "libcurl" in agent else "greatWeb", label, bg_label)
                self.send_text(great_service.session_page(job, self.ui_url(job["id"])), html_type)
            except great_tools.InputError as exc:
                self.send_text(great_service.error_page(str(exc)), html_type)
            except great_service.LimitError as exc:
                self.send_text(great_service.error_page(str(exc)), html_type, exc.status,
                               {"Retry-After": str(exc.retry_after)} if exc.retry_after else None)
        elif name == "readJsFromFile":
            match = re.search(r"results/([0-9]{8}-[0-9]{6}-[a-z0-9]{8})\.d/([A-Za-z0-9]+)\.js$", fields.get("path", ""))
            try:
                rows = great.rows(match.group(1), match.group(2)) if match else None
            except FileNotFoundError:
                rows = None
            if rows is None:
                self.send_text("", "application/json")  # rGREAT reads 0 bytes as an expired job
            else:
                self.send_json(rows)
        elif name == "downloadAllTSV":
            match = re.search(r"([0-9]{8}-[0-9]{6}-[a-z0-9]{8})\.d", fields.get("outputDir", "") or fields.get("outputPath", ""))
            try:
                job = great.job(match.group(1) if match else "")
            except FileNotFoundError:
                self.send_text("GREAT is unable to locate a session with this name; please submit the job again.\n")
                return
            key = fields.get("ontoName", "")
            counts = re.search(r"@([0-9]+)-([0-9]+|Inf)$", fields.get("ontoList", ""))
            self.send_text(great_service.export_tsv(great, job, [key] if key else None,
                                                    min_count=int(counts.group(1)) if counts else 1,
                                                    max_count=None if not counts or counts.group(2) == "Inf" else int(counts.group(2))))
        elif name == "downloadAssociations":
            try:
                job = great.job(fields.get("sessionName", ""))
            except FileNotFoundError:
                self.send_text("GREAT is unable to locate a session with this name; please submit the job again.\n")
                return
            try:
                text = great_service.association_table(great, job, "gene" if fields.get("table") == "gene" else "region",
                                                       fields.get("ontoName") or None, fields.get("termId") or None)
            except great_tools.InputError:
                text = "\n"  # an unknown term: rGREAT reports an empty table
            self.send_text(text)
        elif name == "greatStart":
            batch = fields.get("outputType", "web") == "batch"
            try:
                fg, label = self.input(fields, "request")
                if fg is None:
                    raise great_tools.InputError("Give requestURL (or upload requestFile / send requestData)")
                bg, bg_label = self.input(fields, "bg")
                title = ": ".join(x for x in (fields.get("requestSender", ""), fields.get("requestName", "")) if x)
                job = great.submit(fields.get("requestSpecies") or fields.get("species") or "hg19", fg, bg, fields, client,
                                   owner, "batch" if batch else "greatStart", title or label or "external data",
                                   fields.get("bgName") or bg_label or ("external background data" if bg else ""))
                if batch:
                    self.send_text(great_service.export_tsv(great, job))
                else:
                    self.send_redirect(self.ui_url(job["id"]))
            except great_tools.InputError as exc:
                if batch:
                    self.send_text("# GREAT error: {}\n".format(exc), status=400)
                else:
                    self.send_text(great_service.error_page(str(exc)), html_type, 400)
            except great_service.LimitError as exc:
                # GREAT answered HTTP 500 when busy and its greatBatchQuery.py retries on exactly that
                status = 500 if batch and exc.status == 429 else exc.status
                headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after else None
                if batch:
                    self.send_text("# GREAT busy: {}\n".format(exc), status=status, headers=headers)
                else:
                    self.send_text(great_service.error_page(str(exc)), html_type, status, headers)
        else:
            self.send_text(great_service.error_page("Unknown GREAT interface {}.php".format(name)), html_type, 404)


class GreatServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 128

    def __init__(self, address, great, canvas, sign_in=(None, None)):
        self.great = great
        self.canvas = canvas
        self.sign_in = sign_in
        super().__init__(address, GreatHandler)

    def handle_error(self, request, client_address):
        if isinstance(sys.exc_info()[1], (BrokenPipeError, ConnectionAbortedError, ConnectionResetError)):
            return
        super().handle_error(request, client_address)


def main():
    config = read_config()
    ap = argparse.ArgumentParser(description="GREAT server of Omics Canvas")
    ap.add_argument("--host", default=config.get("host", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(config.get("port", 8894)))
    ap.add_argument("--canvas", default=config.get("canvas", "http://127.0.0.1:8000"),
                    help="Genome Canvas's internal address (workspaces, track files)")
    ap.add_argument("--canvas-path", default=config.get("canvasPath", "/genome-canvas/"),
                    help="where the gateway publishes Genome Canvas (the page calls its API there)")
    ap.add_argument("--data", default=config.get("dataDir", str(APP_DIR / "data")),
                    help="genes and ontologies, built by tools/fetch_great_data.py (default great/data)")
    ap.add_argument("--state", default=os.environ.get("GREAT_STATE_DIR") or config.get("stateDir")
                    or str(APP_DIR.parent / "state" / "great"), help="jobs and settings (default ../state/great)")
    a = ap.parse_args()
    folder = lambda value: Path(os.path.expanduser(value)) if Path(os.path.expanduser(value)).is_absolute() \
        else APP_DIR / os.path.expanduser(value)  # relative to great/
    great = great_service.GreatService(folder(a.data), folder(a.state), admins=config.get("admins", []))
    server = GreatServer((a.host, a.port), great, Canvas(a.canvas, a.canvas_path), load_sign_in())
    great.warm()  # the default assembly, in a worker, in the background
    great.start_maintenance()  # retention and storage limit, every 10 minutes
    print("GREAT is running on http://{}:{}/great/ (data {}, state {})".format(a.host, a.port, a.data, a.state), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        great.pool.stop()
        server.server_close()


if __name__ == "__main__":
    main()
