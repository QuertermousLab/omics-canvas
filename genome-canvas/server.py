#!/usr/bin/env python3
"""Genome Canvas local HTTP server.

Serves the browser UI, exposes configured server directories through a safe
file browser, supports HTTP byte ranges for indexed genomic files, and stores
short share links on this machine.
"""

import argparse
import gzip
import hashlib
import ipaddress
import json
import mimetypes
import os
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from email.utils import formatdate
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie
from pathlib import Path
from threading import Lock
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, unquote, urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from workspace_store import WorkspaceError, WorkspaceStore
from resource_cache import FileMemoryCache, ReferenceCache


APP_DIR = Path(__file__).resolve().parent
FONT_DIR = APP_DIR / "vendor" / "fonts"
mimetypes.add_type("font/woff2", ".woff2")
LOCAL_MODE = os.environ.get("GENOME_CANVAS_LOCAL_MODE", "").strip().lower() in ("1", "true", "yes", "on")
STATE_DIR = Path(os.path.expanduser(os.environ.get(
    "GENOME_CANVAS_STATE_DIR",
    str(APP_DIR / ".genomecanvas"),
)))
SESSION_DIR = STATE_DIR / "sessions"
PROFILE_DIR = STATE_DIR / "profiles"
WORKSPACE_DB_FILE = STATE_DIR / "workspaces.sqlite3"
CONFIG_FILE = APP_DIR / "genomecanvas.config.json"
REFERENCE_CACHE = ReferenceCache(os.environ.get(
    "GENOME_CANVAS_REFERENCE_DIR", str(STATE_DIR / "references")
))
FILE_MEMORY_CACHE = FileMemoryCache()
MAX_SESSION_BYTES = 5 * 1024 * 1024
MAX_PROFILE_BYTES = 5 * 1024 * 1024
BUFFER_SIZE = 1024 * 1024
MAX_HUB_DOWNLOAD_BYTES = 64 * 1024 * 1024
MAX_HUB_TRACKS = 30000
HUB_FETCH_TIMEOUT_SECONDS = 30
HUB_CACHE_TTL_SECONDS = 15 * 60
HUB_CACHE = {}
HUB_CACHE_LOCK = Lock()
RSID_CACHE = {}
RSID_CACHE_LOCK = Lock()
LD_CACHE = {}
LD_CACHE_LOCK = Lock()
LD_CACHE_TTL_SECONDS = 10 * 60
MAX_LD_CACHE_ENTRIES = 24
DEFAULT_MAX_LD_WINDOW = 2 * 1000 * 1000
WORKSPACE_COOKIE_NAME = "genome_canvas_workspace"
MAX_WORKSPACE_BYTES = 16 * 1024
# JSON responses and whole-file text tracks above this size are gzip encoded
# when the client accepts it. Byte-range responses are never re-encoded.
GZIP_MIN_BYTES = 1400
PLAIN_TEXT_TRACK_SUFFIXES = (
    ".bed", ".bedgraph", ".wig", ".gff", ".gff3", ".gtf", ".narrowpeak",
    ".broadpeak", ".bedpe", ".interact", ".seg", ".maf", ".mut", ".gwas",
    ".bp", ".qtl", ".bedmethyl", ".vcf", ".fa", ".fasta", ".fai",
)

TRACK_SUFFIXES = (
    ".bam", ".cram", ".vcf", ".vcf.gz", ".vcf.bgz", ".bcf",
    ".bed", ".bed.gz", ".bed.bgz", ".bedgraph", ".bedgraph.gz",
    ".bw", ".bigwig", ".bb", ".bigbed", ".wig", ".wig.gz",
    ".gff", ".gff.gz", ".gff3", ".gff3.gz", ".gtf", ".gtf.gz",
    ".narrowpeak", ".broadpeak", ".bedpe", ".bedpe.gz",
    ".interact", ".biginteract", ".seg", ".seg.gz", ".maf", ".mut",
    ".gwas", ".gwas.gz", ".gwas.bgz", ".bp", ".tdf", ".qtl", ".hic", ".bedmethyl", ".bedmethyl.gz",
    ".fa", ".fasta", ".fa.gz", ".fasta.gz", ".2bit",
)

STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/track-colors.mjs": ("track-colors.mjs", "text/javascript; charset=utf-8"),
    "/signal-style.mjs": ("signal-style.mjs", "text/javascript; charset=utf-8"),
    "/annotation-style.mjs": ("annotation-style.mjs", "text/javascript; charset=utf-8"),
    "/hic-heatmap.mjs": ("hic-heatmap.mjs", "text/javascript; charset=utf-8"),
    "/manhattan-style.mjs": ("manhattan-style.mjs", "text/javascript; charset=utf-8"),
    "/public-hubs.mjs": ("public-hubs.mjs", "text/javascript; charset=utf-8"),
    "/highlights.mjs": ("highlights.mjs", "text/javascript; charset=utf-8"),
    "/reference-resources.mjs": ("reference-resources.mjs", "text/javascript; charset=utf-8"),
    "/canvas-theme.mjs": ("canvas-theme.mjs", "text/javascript; charset=utf-8"),
    "/favicon.svg": ("public/favicon.svg", "image/svg+xml"),
    "/vendor/igv.min.js": ("vendor/igv.min.js", "text/javascript; charset=utf-8"),
    "/vendor/IGV-LICENSE.txt": ("vendor/IGV-LICENSE.txt", "text/plain; charset=utf-8"),
}


def read_json(path, fallback):
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return fallback
    except (OSError, json.JSONDecodeError) as exc:
        print("Warning: could not read {}: {}".format(path, exc), file=sys.stderr)
        return fallback


def load_settings():
    settings = read_json(CONFIG_FILE, {})
    configured = []

    env_roots = os.environ.get("GENOME_DATA_ROOTS", "").strip()
    if env_roots:
        configured.extend(item for item in env_roots.split(os.pathsep) if item)
    else:
        configured.extend(settings.get("dataRoots", []))

    if not configured:
        default_data = APP_DIR / "data"
        default_data.mkdir(exist_ok=True)
        configured = [{"label": "Genome Canvas data", "path": str(default_data)}]

    roots = {}
    for item in configured:
        if isinstance(item, str):
            label, raw_path = Path(item).name or item, item
        elif isinstance(item, dict):
            raw_path = item.get("path", "")
            label = item.get("label") or Path(raw_path).name or raw_path
        else:
            continue

        raw_path = os.path.expandvars(os.path.expanduser(str(raw_path)))
        path = Path(raw_path)
        if not path.is_absolute():
            path = APP_DIR / path
        try:
            path = path.resolve()
        except OSError:
            continue
        if not path.is_dir():
            print("Warning: data root does not exist: {}".format(path), file=sys.stderr)
            continue

        if LOCAL_MODE and path == Path("/"):
            label = "This Mac"

        # An "id" in the config pins the root's id, so share links keep working after the folder moves.
        pinned = item.get("id") if isinstance(item, dict) else None
        root_id = str(pinned) if pinned else hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:12]
        roots[root_id] = {"path": path, "label": str(label)}

    if not roots:
        raise RuntimeError("No readable genome data roots are configured")
    return settings, roots


SETTINGS, DATA_ROOTS = load_settings()
HOME_ROOT = Path(SETTINGS.get("homeRoot", "/home"))
LOCAL_DBSNP_HG38 = Path(SETTINGS.get("dbSnpHg38") or APP_DIR / "data" / "hg38.dbsnp156.gz")
LOCAL_WORKSPACE = {"id": "local", "name": "This Mac", "kind": "local", "home": None}


def within_root(candidate, root):
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False


def normalized_absolute(path):
    return Path(os.path.abspath(os.path.normpath(str(path))))


def home_workspace_id(path):
    return "home-{}".format(hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:18])


def discover_home_workspaces(home_root=HOME_ROOT):
    if LOCAL_MODE:
        return []
    home_root = normalized_absolute(home_root)
    workspaces = []
    try:
        entries = sorted(home_root.iterdir(), key=lambda item: item.name.casefold())
    except OSError:
        return workspaces
    for path in entries:
        if path.name.startswith("."):
            continue
        try:
            if not path.is_dir():
                continue
        except OSError:
            continue
        workspaces.append({
            "id": home_workspace_id(path),
            "name": path.name,
            "kind": "home",
            "home": normalized_absolute(path),
        })
    return workspaces


def workspace_catalog(store, home_root=HOME_ROOT):
    if LOCAL_MODE:
        return [LOCAL_WORKSPACE.copy()]
    hidden_ids = {item["id"] for item in store.hidden_homes()}
    system_workspaces = [item for item in discover_home_workspaces(home_root) if item["id"] not in hidden_ids]
    manual_workspaces = store.list_manual()
    return system_workspaces + manual_workspaces


def find_workspace(store, workspace_id, home_root=HOME_ROOT):
    workspace_id = str(workspace_id or "")
    if LOCAL_MODE:
        return LOCAL_WORKSPACE.copy()
    return next((item for item in workspace_catalog(store, home_root) if item["id"] == workspace_id), None)


def data_roots_for_workspace(workspace):
    roots = {}
    home = workspace.get("home") if workspace and workspace.get("kind") == "home" else None
    if home and home.is_dir():
        home_id = hashlib.sha256(str(home).encode("utf-8")).hexdigest()[:12]
        roots[home_id] = {"path": home, "label": "{} Home".format(workspace["name"])}
    roots.update(DATA_ROOTS)
    return roots


def default_file_location(workspace, roots=None):
    roots = roots or data_roots_for_workspace(workspace)
    if workspace.get("kind") == "home":
        home = workspace.get("home")
        for root_id, item in roots.items():
            if item["path"] == home:
                return {"root": root_id, "path": ""}
    for root_id, item in roots.items():
        if item["path"] == Path("/nfs") or item["label"] == "Shared NFS":
            return {"root": root_id, "path": ""}
    root_id = next(iter(roots))
    return {"root": root_id, "path": ""}


def authorized_resolved_path(path):
    try:
        path.resolve(strict=True)
    except (OSError, RuntimeError):
        raise FileNotFoundError("Symbolic link target is unavailable")
    # The user-visible path is still required to remain lexically inside a
    # configured root. Existing symlinks inside that root may intentionally
    # point to shared storage elsewhere on the server.
    return True


def resolve_data_path(root_id, relative_path, roots=None):
    roots = roots or DATA_ROOTS
    root = roots.get(root_id)
    if not root:
        raise FileNotFoundError("Unknown data root")
    requested = Path(relative_path)
    candidate = normalized_absolute(root["path"] / requested)
    if requested.is_absolute() and not within_root(candidate, root["path"]):
        # The browser displays root-relative locations with a leading slash.
        # Treat those as relative to the selected root unless they already name
        # an absolute path inside it.
        candidate = normalized_absolute(root["path"] / str(requested).lstrip(os.sep))
    if not within_root(candidate, root["path"]):
        raise PermissionError("Path leaves configured data root")
    if not authorized_resolved_path(candidate):
        raise PermissionError("Symbolic link target is outside configured data roots")
    return candidate


def relative_url(root_id, relative_path, workspace_id=None):
    encoded = "/".join(quote(part) for part in Path(relative_path).parts if part not in (".", ""))
    if workspace_id:
        return "/data/{}/{}/{}".format(quote(str(workspace_id)), root_id, encoded)
    return "/data/{}/{}".format(root_id, encoded)


def data_url_for_path(path, preferred_root_id=None, roots=None, workspace_id=None):
    roots = roots or DATA_ROOTS
    ordered = []
    if preferred_root_id in roots:
        ordered.append((preferred_root_id, roots[preferred_root_id]))
    ordered.extend(
        (root_id, item)
        for root_id, item in sorted(roots.items(), key=lambda pair: len(str(pair[1]["path"])), reverse=True)
        if root_id != preferred_root_id
    )
    absolute = normalized_absolute(path)
    for root_id, item in ordered:
        if within_root(absolute, item["path"]):
            return relative_url(root_id, absolute.relative_to(item["path"]), workspace_id)
    raise PermissionError("File is outside configured data roots")


def companion_data_url(source_path, companion_path, root_id, roots=None, workspace_id=None):
    roots = roots or DATA_ROOTS
    root = roots.get(root_id)
    if not root or not within_root(normalized_absolute(source_path), root["path"]):
        raise PermissionError("Track is outside configured data roots")
    if companion_path not in index_candidates(source_path):
        raise PermissionError("Unsupported companion file")
    source_relative = normalized_absolute(source_path).relative_to(root["path"])
    return "{}?companion={}".format(
        relative_url(root_id, source_relative, workspace_id),
        quote(companion_path.name),
    )


def detect_format(filename):
    name = filename.lower()
    checks = (
        ((".bigwig", ".bw"), "bigwig", "wig"),
        ((".bigbed", ".bb"), "bigbed", "annotation"),
        ((".bedgraph.gz", ".bedgraph"), "bedgraph", "wig"),
        ((".wig.gz", ".wig"), "wig", "wig"),
        ((".bam",), "bam", "alignment"),
        ((".cram",), "cram", "alignment"),
        ((".vcf.gz", ".vcf.bgz", ".vcf"), "vcf", "variant"),
        ((".bcf",), "bcf", "variant"),
        ((".bedpe.gz", ".bedpe", ".interact", ".biginteract"), "bedpe", "interact"),
        ((".narrowpeak",), "narrowpeak", "annotation"),
        ((".broadpeak",), "broadpeak", "annotation"),
        ((".bedmethyl.gz", ".bedmethyl"), "bedmethyl", "annotation"),
        ((".bed.gz", ".bed.bgz", ".bed"), "bed", "annotation"),
        ((".gff3.gz", ".gff3"), "gff3", "annotation"),
        ((".gff.gz", ".gff"), "gff", "annotation"),
        ((".gtf.gz", ".gtf"), "gtf", "annotation"),
        ((".seg.gz", ".seg"), "seg", "seg"),
        ((".maf", ".mut"), "mut", "mut"),
        ((".gwas.gz", ".gwas.bgz", ".gwas"), "gwas", "gwas"),
        ((".qtl",), "qtl", "qtl"),
        ((".bp",), "bp", "arc"),
        ((".tdf",), "tdf", "wig"),
        ((".hic",), "hic", "interact"),
        ((".fasta.gz", ".fasta", ".fa.gz", ".fa"), "fasta", "reference"),
        ((".2bit",), "2bit", "reference"),
    )
    for suffixes, file_format, track_type in checks:
        if name.endswith(suffixes):
            return file_format, track_type
    return "auto", "annotation"


def is_track_file(path):
    if path.name.lower().endswith(TRACK_SUFFIXES):
        return True
    if path.is_symlink():
        try:
            return path.resolve().name.lower().endswith(TRACK_SUFFIXES)
        except (OSError, RuntimeError):
            return False
    return False


def format_filename(path):
    if path.name.lower().endswith(TRACK_SUFFIXES):
        return path.name
    try:
        return path.resolve().name
    except (OSError, RuntimeError):
        return path.name


def index_candidates(path, sibling_names=None):
    def candidates_for(candidate):
        name = candidate.name.lower()
        candidates = []
        if name.endswith(".bam"):
            candidates = [Path(str(candidate) + ".bai"), candidate.with_suffix(".bai")]
        elif name.endswith(".cram"):
            candidates = [Path(str(candidate) + ".crai"), candidate.with_suffix(".crai")]
        elif name.endswith((".vcf.gz", ".vcf.bgz", ".bed.gz", ".bed.bgz", ".gff.gz", ".gff3.gz", ".gtf.gz", ".bedpe.gz", ".bedmethyl.gz", ".gwas.gz", ".gwas.bgz")):
            candidates = [Path(str(candidate) + ".tbi"), Path(str(candidate) + ".csi")]
        elif name.endswith(".bcf"):
            candidates = [Path(str(candidate) + ".csi")]
        elif name.endswith((".fa", ".fasta", ".fa.gz", ".fasta.gz")):
            candidates = [Path(str(candidate) + ".fai")]
        return candidates

    candidates = candidates_for(path)
    if path.is_symlink():
        try:
            candidates.extend(candidates_for(path.resolve()))
        except (OSError, RuntimeError):
            pass
    found = []
    seen = set()
    for candidate in candidates:
        # A directory listing already knows its own entries; skip stat calls
        # (each one is a network round trip on NFS) for absent siblings.
        if sibling_names is not None and candidate.parent == path.parent and candidate.name not in sibling_names:
            continue
        if candidate.is_file() and str(candidate) not in seen:
            found.append(candidate)
            seen.add(str(candidate))
    return found


def directory_payload(root_id, relative_path, roots=None):
    roots = roots or DATA_ROOTS
    directory = resolve_data_path(root_id, relative_path, roots)
    if not directory.is_dir():
        raise NotADirectoryError("Not a directory")

    root_path = roots[root_id]["path"]
    entries = []
    try:
        # scandir reports entry types from the directory read itself, so large
        # NFS directories only pay a stat for folders and track files.
        with os.scandir(str(directory)) as iterator:
            children = list(iterator)
    except PermissionError:
        raise PermissionError("Directory is not readable")
    sibling_names = {item.name for item in children}

    for item in children:
        if item.name.startswith("."):
            continue
        child = directory / item.name
        try:
            if item.is_dir():
                stat = item.stat()
                entries.append({
                    "kind": "directory",
                    "name": item.name,
                    "path": str(child.relative_to(root_path)),
                    "symlink": item.is_symlink(),
                    "modified": int(stat.st_mtime),
                })
            elif item.is_file() and is_track_file(child):
                stat = item.stat()
                file_format, track_type = detect_format(format_filename(child))
                indexes = index_candidates(child, sibling_names)
                entries.append({
                    "kind": "file",
                    "name": item.name,
                    "path": str(child.relative_to(root_path)),
                    "symlink": item.is_symlink(),
                    "size": stat.st_size,
                    "modified": int(stat.st_mtime),
                    "format": file_format,
                    "trackType": track_type,
                    "indexed": bool(indexes),
                    "indexName": indexes[0].name if indexes else None,
                })
        except (OSError, ValueError):
            continue

    entries.sort(key=lambda item: (item["kind"] != "directory", item["name"].lower()))
    current = "" if directory == root_path else str(directory.relative_to(root_path))
    parent = None if not current else str(Path(current).parent)
    if parent == ".":
        parent = ""
    return {"root": root_id, "path": current, "parent": parent, "entries": entries}


def validate_public_url(url):
    parsed = urlparse(str(url))
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("Public hub URL must use HTTP or HTTPS")
    if parsed.username or parsed.password:
        raise ValueError("Public hub URL cannot contain credentials")
    try:
        addresses = {
            item[4][0].split("%", 1)[0]
            for item in socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
        }
    except socket.gaierror as exc:
        raise ValueError("Public hub host could not be resolved") from exc
    if not addresses:
        raise ValueError("Public hub host could not be resolved")
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_unspecified:
            raise ValueError("Public hub URL cannot target a private or local address")
    return parsed.geturl()


class SafeHubRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


HUB_HTTP_OPENER = build_opener(SafeHubRedirectHandler())


def fetch_public_resource(url, maximum_bytes=MAX_HUB_DOWNLOAD_BYTES):
    validate_public_url(url)
    request = Request(url, headers={
        "Accept": "application/json,text/plain;q=0.9,*/*;q=0.5",
        "User-Agent": "GenomeCanvas/1.0 public-hub-reader",
    })
    try:
        with HUB_HTTP_OPENER.open(request, timeout=HUB_FETCH_TIMEOUT_SECONDS) as response:
            validate_public_url(response.geturl())
            declared_size = response.headers.get("Content-Length")
            if declared_size and int(declared_size) > maximum_bytes:
                raise ValueError("Public hub descriptor is too large")
            body = response.read(maximum_bytes + 1)
            if len(body) > maximum_bytes:
                raise ValueError("Public hub descriptor is too large")
            return body, response.geturl(), response.headers.get_content_charset() or "utf-8"
    except HTTPError as exc:
        raise ValueError("Remote hub returned HTTP {}".format(exc.code)) from exc
    except (URLError, TimeoutError, socket.timeout) as exc:
        reason = getattr(exc, "reason", exc)
        raise ValueError("Remote hub could not be reached: {}".format(reason)) from exc


def chromosome_for_refseq_accession(accession):
    match = re.fullmatch(r"NC_(\d{6})\.\d+", str(accession or ""))
    if not match:
        return None
    number = int(match.group(1))
    if 1 <= number <= 22:
        return "chr{}".format(number)
    if number == 23:
        return "chrX"
    if number == 24:
        return "chrY"
    if number == 12920:
        return "chrM"
    return None


def refsnp_location(record, genome):
    assembly_prefix = {"hg38": "GRCh38", "hg19": "GRCh37"}.get(str(genome))
    if not assembly_prefix:
        raise ValueError("rsID search currently supports hg38 and hg19")
    placements = record.get("primary_snapshot_data", {}).get("placements_with_allele", [])
    for placement in placements:
        annotation = placement.get("placement_annot", {})
        if annotation.get("seq_type") != "refseq_chromosome":
            continue
        traits = annotation.get("seq_id_traits_by_assembly", [])
        if not any(str(item.get("assembly_name", "")).startswith(assembly_prefix) for item in traits):
            continue
        chromosome = chromosome_for_refseq_accession(placement.get("seq_id"))
        if not chromosome:
            continue
        alleles = placement.get("alleles") or []
        spdi = next((item.get("allele", {}).get("spdi") for item in alleles if item.get("allele", {}).get("spdi")), None)
        if not spdi:
            continue
        position = int(spdi.get("position"))
        deleted = spdi.get("deleted_sequence", "")
        length = int(deleted) if isinstance(deleted, int) else max(1, len(str(deleted)))
        one_based = position + 1
        return {
            "chromosome": chromosome,
            "position": one_based,
            "start": position,
            "end": position + max(1, length),
            "locus": "{}:{}-{}".format(chromosome, max(1, one_based - 100), one_based + max(100, length)),
            "assembly": assembly_prefix,
        }
    raise ValueError("The rsID has no {} chromosome placement".format(assembly_prefix))


def resolve_rsid(rsid, genome):
    match = re.fullmatch(r"rs(\d+)", str(rsid or "").strip(), re.IGNORECASE)
    if not match:
        raise ValueError("Enter an rsID such as rs328")
    normalized = "rs{}".format(match.group(1))
    cache_key = (normalized, str(genome))
    now = time.time()
    with RSID_CACHE_LOCK:
        cached = RSID_CACHE.get(cache_key)
        if cached and now - cached[0] < 24 * 60 * 60:
            return cached[1]
    url = "https://api.ncbi.nlm.nih.gov/variation/v0/refsnp/{}".format(match.group(1))
    try:
        body, _, _ = fetch_public_resource(url, 8 * 1024 * 1024)
        record = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("NCBI could not resolve {}: {}".format(normalized, exc)) from exc
    location = {"rsid": normalized, **refsnp_location(record, genome), "source": "NCBI RefSNP"}
    if str(genome) == "hg38":
        local = verify_local_rsid(normalized, location)
        if local:
            location = local
    with RSID_CACHE_LOCK:
        RSID_CACHE[cache_key] = (now, location)
    return location


def verify_local_rsid(rsid, approximate_location):
    path = LOCAL_DBSNP_HG38
    if not path.is_file() or not Path(str(path) + ".tbi").is_file():
        return None
    chromosome = approximate_location["chromosome"]
    local_contig = chromosome[3:] if chromosome.startswith("chr") else chromosome
    if local_contig == "M":
        local_contig = "MT"
    region = "{}:{}-{}".format(
        local_contig,
        max(1, approximate_location["position"] - 5),
        approximate_location["position"] + 5,
    )
    try:
        result = subprocess.run(
            ["tabix", str(path), region],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    for line in result.stdout.splitlines():
        fields = line.split("\t")
        if len(fields) < 5 or rsid not in fields[2].split(";"):
            continue
        position = int(fields[1])
        length = max(1, len(fields[3]))
        return {
            "rsid": rsid,
            "chromosome": chromosome,
            "position": position,
            "start": position - 1,
            "end": position - 1 + length,
            "locus": "{}:{}-{}".format(chromosome, max(1, position - 100), position + max(100, length)),
            "assembly": "GRCh38",
            "source": "local dbSNP 156",
        }
    return None


def normalized_ld_chromosome(value):
    chromosome = str(value or "").strip()
    if chromosome.lower().startswith("chr"):
        chromosome = chromosome[3:]
    chromosome = chromosome.upper()
    if chromosome in ("X", "Y"):
        return chromosome
    if chromosome.isdigit() and 1 <= int(chromosome) <= 22:
        return str(int(chromosome))
    raise ValueError("LD currently supports chromosomes 1-22, X, and Y")


def alleles_match(first_ref, first_alt, second_ref, second_alt):
    first = {str(first_ref or "").upper(), str(first_alt or "").upper()}
    second = {str(second_ref or "").upper(), str(second_alt or "").upper()}
    if first == second:
        return True
    complement = str.maketrans("ACGT", "TGCA")
    complemented = {allele.translate(complement) for allele in first}
    return complemented == second


def parse_plink_ld_output(text, start=None, end=None):
    lines = str(text or "").splitlines()
    if not lines:
        return []
    header = lines[0].lstrip("#").split("\t")
    required = ("POS_B", "ID_B", "REF_B", "ALT1_B", "UNPHASED_R2")
    if any(column not in header for column in required):
        raise ValueError("PLINK LD output is missing required columns")
    indexes = {column: header.index(column) for column in required}
    records = []
    for line in lines[1:]:
        fields = line.split("\t")
        if len(fields) < len(header):
            continue
        try:
            position = int(fields[indexes["POS_B"]])
            r_squared = float(fields[indexes["UNPHASED_R2"]])
        except (ValueError, IndexError):
            continue
        if start is not None and position < start:
            continue
        if end is not None and position > end:
            continue
        records.append({
            "position": position,
            "id": fields[indexes["ID_B"]],
            "ref": fields[indexes["REF_B"]],
            "alt": fields[indexes["ALT1_B"]],
            "r2": max(0.0, min(1.0, r_squared)),
        })
    return records


def ld_reference_settings():
    value = SETTINGS.get("ldReferenceHg19")
    return value if isinstance(value, dict) else {}


def formatted_ld_path(template, chromosome):
    if not template:
        return None
    try:
        value = str(template).format(chrom=chromosome)
    except (KeyError, ValueError):
        raise ValueError("Invalid LD reference path template")
    return Path(os.path.expandvars(os.path.expanduser(value)))


def resolve_ld_reference_variant(vcf_path, chromosome, position, ref, alt):
    try:
        result = subprocess.run(
            ["tabix", str(vcf_path), "{}:{}-{}".format(chromosome, position, position)],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    for line in result.stdout.splitlines():
        fields = line.split("\t")
        if len(fields) < 5 or int(fields[1]) != position:
            continue
        for candidate_alt in fields[4].split(","):
            if alleles_match(ref, alt, fields[3], candidate_alt):
                variant_id = fields[2].split(";", 1)[0]
                if variant_id and variant_id != ".":
                    return {
                        "position": position,
                        "id": variant_id,
                        "ref": fields[3],
                        "alt": candidate_alt,
                    }
    return None


def calculate_local_ld(chromosome, start, end, lead_position, lead_ref, lead_alt):
    settings = ld_reference_settings()
    label = str(settings.get("label") or "1000 Genomes Phase 3 ALL")
    if not settings:
        return {"available": False, "label": label, "reason": "No local LD reference is configured", "variants": []}

    chromosome = normalized_ld_chromosome(chromosome)
    start, end, lead_position = int(start), int(end), int(lead_position)
    if start < 1 or end < start or lead_position < 1:
        raise ValueError("Invalid LD interval")
    configured_max = int(settings.get("maxWindow") or DEFAULT_MAX_LD_WINDOW)
    max_window = max(100000, min(configured_max, 5000000))
    rounded_start = max(1, (start // 50000) * 50000)
    rounded_end = ((end + 49999) // 50000) * 50000
    if rounded_end - rounded_start > max_window:
        return {
            "available": False,
            "label": label,
            "maxWindow": max_window,
            "reason": "Zoom to {} Mb or less for LD".format(max_window / 1000000),
            "variants": [],
        }

    cache_key = (chromosome, rounded_start, rounded_end, lead_position, str(lead_ref), str(lead_alt))
    now = time.time()
    with LD_CACHE_LOCK:
        cached = LD_CACHE.get(cache_key)
        if cached and now - cached[0] < LD_CACHE_TTL_SECONDS:
            return cached[1]

    bfile = formatted_ld_path(settings.get("bfileTemplate"), chromosome)
    vcf_path = formatted_ld_path(settings.get("vcfTemplate"), chromosome)
    if not bfile or not all(Path(str(bfile) + suffix).is_file() for suffix in (".bed", ".bim", ".fam")):
        return {"available": False, "label": label, "reason": "The local LD panel is unavailable for this chromosome", "variants": []}
    if not vcf_path or not vcf_path.is_file() or not Path(str(vcf_path) + ".tbi").is_file():
        return {"available": False, "label": label, "reason": "The local LD lookup index is unavailable", "variants": []}

    reference = resolve_ld_reference_variant(vcf_path, chromosome, lead_position, lead_ref, lead_alt)
    if not reference:
        return {
            "available": False,
            "label": label,
            "reason": "The lead variant is absent from the local LD panel",
            "variants": [],
        }

    try:
        with tempfile.TemporaryDirectory(prefix="genome-canvas-ld-") as temporary:
            prefix = Path(temporary) / "ld"
            window_kb = max(1, (rounded_end - rounded_start + 999) // 1000)
            result = subprocess.run(
                [
                    "plink2", "--bfile", str(bfile), "--chr", chromosome,
                    "--from-bp", str(rounded_start), "--to-bp", str(rounded_end),
                    "--ld-snp", reference["id"], "--r2-unphased",
                    "cols=chrom,pos,id,ref,alt1", "--ld-window", "99999999",
                    "--ld-window-kb", str(window_kb), "--ld-window-r2", "0",
                    "--threads", "2", "--out", str(prefix),
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=20,
            )
            output_path = Path(str(prefix) + ".vcor")
            if not output_path.is_file():
                raise ValueError("PLINK did not create an LD result")
            variants = parse_plink_ld_output(output_path.read_text(encoding="utf-8"), start, end)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        print("Warning: local LD calculation failed: {}".format(exc), file=sys.stderr)
        return {"available": False, "label": label, "reason": "Local LD calculation failed", "variants": []}

    if not any(item["position"] == reference["position"] for item in variants):
        variants.append({**reference, "r2": 1.0})
    payload = {
        "available": True,
        "label": label,
        "build": "GRCh37",
        "chromosome": chromosome,
        "start": start,
        "end": end,
        "maxWindow": max_window,
        "reference": reference,
        "variants": variants,
    }
    with LD_CACHE_LOCK:
        if len(LD_CACHE) >= MAX_LD_CACHE_ENTRIES:
            oldest = min(LD_CACHE, key=lambda key: LD_CACHE[key][0])
            LD_CACHE.pop(oldest, None)
        LD_CACHE[cache_key] = (now, payload)
    return payload


def decode_public_text(body, charset="utf-8"):
    try:
        return body.decode(charset or "utf-8-sig")
    except (LookupError, UnicodeDecodeError):
        return body.decode("utf-8-sig", errors="replace")


def parse_ucsc_records(text, record_key):
    records = []
    current = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("browser "):
            continue
        key, separator, value = line.partition(" ")
        value = value.strip() if separator else ""
        if key == record_key:
            current = {record_key: value.split()[0] if value else ""}
            records.append(current)
        elif current is not None:
            current[key] = value
    return records


def read_ucsc_trackdb(url, seen=None, depth=0):
    if depth > 8:
        raise ValueError("UCSC trackDb include nesting is too deep")
    seen = seen if seen is not None else set()
    if url in seen:
        return []
    seen.add(url)
    body, final_url, charset = fetch_public_resource(url)
    text = decode_public_text(body, charset)
    records = parse_ucsc_records(text, "track")
    for record in records:
        record["_base_url"] = final_url
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line.startswith("include "):
            continue
        include_path = line.split(None, 1)[1].strip()
        records.extend(read_ucsc_trackdb(urljoin(final_url, include_path), seen, depth + 1))
    return records


def ucsc_track_format(type_value, data_url):
    declared = str(type_value or "").split(None, 1)[0].lower()
    aliases = {
        "bigwig": "bigwig",
        "bigbed": "bigbed",
        "bedgraph": "bedgraph",
        "bed": "bed",
        "bam": "bam",
        "cram": "cram",
        "vcf": "vcf",
        "vcftabix": "vcf",
        "hic": "hic",
    }
    if declared in aliases:
        return aliases[declared]
    name = urlparse(str(data_url)).path.lower()
    for suffix, file_format in (
        (".bigwig", "bigwig"), (".bw", "bigwig"), (".bigbed", "bigbed"),
        (".bb", "bigbed"), (".bam", "bam"), (".cram", "cram"),
        (".vcf.gz", "vcf"), (".vcf", "vcf"), (".hic", "hic"),
        (".bedgraph", "bedgraph"), (".bed", "bed"),
    ):
        if name.endswith(suffix):
            return file_format
    return ""


def ucsc_parent_path(record, by_id):
    labels = []
    current = record
    visited = set()
    while current:
        parent_value = str(current.get("parent", "")).strip()
        if not parent_value:
            break
        parent_id = parent_value.split(None, 1)[0]
        if parent_id in visited:
            break
        visited.add(parent_id)
        current = by_id.get(parent_id)
        if current:
            label = current.get("shortLabel") or current.get("longLabel") or current.get("track")
            if label:
                labels.append(label)
    return " / ".join(reversed(labels))


def normalize_ucsc_trackdb(records, hub_url):
    by_id = {record.get("track"): record for record in records if record.get("track")}
    tracks = []
    skipped = 0
    hub_hash = hashlib.sha1(hub_url.encode("utf-8")).hexdigest()[:10]
    for record in records:
        data_path = record.get("bigDataUrl")
        if not data_path:
            skipped += 1
            continue
        data_url = urljoin(record.get("_base_url", hub_url), data_path)
        file_format = ucsc_track_format(record.get("type"), data_url)
        if not file_format:
            skipped += 1
            continue
        track_id = record.get("track") or hashlib.sha1(data_url.encode("utf-8")).hexdigest()[:12]
        groups = {}
        for item in str(record.get("subGroups", "")).split():
            key, separator, value = item.partition("=")
            if separator:
                groups[key.lower()] = value.replace("_", " ").strip()
        config = {
            "id": "ucsc-{}-{}".format(hub_hash, track_id),
            "name": record.get("shortLabel") or record.get("longLabel") or track_id,
            "url": data_url,
            "format": file_format,
            "description": record.get("longLabel") or record.get("description") or "UCSC public track",
            "hubGroup": ucsc_parent_path(record, by_id) or "UCSC track hub",
            "genomeCanvasAutoColor": True,
        }
        sample = groups.get("biosample") or groups.get("sample") or groups.get("tissue")
        assay = groups.get("assay") or groups.get("type")
        if sample:
            config["sample"] = sample
        if assay:
            config["assay"] = assay
        if record.get("bigDataIndex"):
            config["indexURL"] = urljoin(record.get("_base_url", hub_url), record["bigDataIndex"])
        if file_format == "hic":
            config["type"] = "interact"
        tracks.append(config)
        if len(tracks) >= MAX_HUB_TRACKS:
            break
    return {
        "tracks": tracks,
        "skipped": skipped,
        "truncated": len(tracks) >= MAX_HUB_TRACKS,
    }


def load_public_hub(url, genome, kind):
    cache_key = (url, genome, kind)
    now = time.monotonic()
    with HUB_CACHE_LOCK:
        cached = HUB_CACHE.get(cache_key)
        if cached and now - cached[0] < HUB_CACHE_TTL_SECONDS:
            return cached[1]

    if kind == "washu":
        body, final_url, charset = fetch_public_resource(url)
        try:
            payload = json.loads(decode_public_text(body, charset))
        except json.JSONDecodeError as exc:
            raise ValueError("WashU hub did not return valid JSON") from exc
        result = {"kind": "washu", "payload": payload, "sourceURL": final_url}
    elif kind == "ucsc":
        hub_body, final_hub_url, charset = fetch_public_resource(url, 2 * 1024 * 1024)
        hub_records = parse_ucsc_records(decode_public_text(hub_body, charset), "hub")
        if not hub_records or not hub_records[0].get("genomesFile"):
            raise ValueError("UCSC hub.txt does not define genomesFile")
        genomes_url = urljoin(final_hub_url, hub_records[0]["genomesFile"])
        genomes_body, final_genomes_url, genomes_charset = fetch_public_resource(genomes_url, 4 * 1024 * 1024)
        assemblies = parse_ucsc_records(decode_public_text(genomes_body, genomes_charset), "genome")
        assembly = next((item for item in assemblies if item.get("genome") == genome), None)
        if not assembly or not assembly.get("trackDb"):
            raise ValueError("UCSC hub does not contain assembly {}".format(genome))
        trackdb_url = urljoin(final_genomes_url, assembly["trackDb"])
        normalized = normalize_ucsc_trackdb(read_ucsc_trackdb(trackdb_url), final_hub_url)
        result = dict(normalized, kind="ucsc", sourceURL=final_hub_url)
    else:
        raise ValueError("Unsupported public hub type")

    with HUB_CACHE_LOCK:
        HUB_CACHE[cache_key] = (now, result)
    return result


def paginate_public_hub(result, query_text="", offset=0, limit=200):
    tracks = result.get("tracks", [])
    needle = str(query_text or "").strip().lower()
    if needle:
        tracks = [
            track for track in tracks
            if any(needle in str(track.get(key, "")).lower() for key in (
                "name", "format", "hubGroup", "sample", "assay", "description", "url"
            ))
        ]
    total = len(tracks)
    offset = max(0, min(int(offset), max(0, total - 1))) if total else 0
    limit = max(20, min(int(limit), 500))
    page = tracks[offset:offset + limit]
    return {
        "kind": result.get("kind", "ucsc"),
        "sourceURL": result.get("sourceURL"),
        "tracks": page,
        "total": total,
        "offset": offset,
        "limit": limit,
        "hasMore": offset + len(page) < total,
        "skipped": result.get("skipped", 0),
        "truncated": result.get("truncated", False),
        "query": needle,
    }


def normalized_profile_name(value):
    name = " ".join(str(value or "").split())
    if not name:
        raise ValueError("Profile name is required")
    if len(name) > 80:
        raise ValueError("Profile name must be 80 characters or fewer")
    return name


def profile_id_for_name(name):
    return hashlib.sha256(name.casefold().encode("utf-8")).hexdigest()[:20]


def normalized_profile_owner(value):
    owner = str(value or "").strip() or "genome"
    if len(owner) > 128 or any(ord(character) < 32 for character in owner):
        raise ValueError("Invalid authenticated user")
    return owner


def profile_owner_key(owner):
    return hashlib.sha256(normalized_profile_owner(owner).casefold().encode("utf-8")).hexdigest()[:24]


def profile_directory(owner):
    return PROFILE_DIR / profile_owner_key(owner)


def profile_candidates(owner, profile_id):
    candidates = [profile_directory(owner) / (profile_id + ".json")]
    if normalized_profile_owner(owner) == "genome":
        candidates.append(PROFILE_DIR / (profile_id + ".json"))
    return candidates


def profile_summary(record):
    state = record.get("state", {})
    reference = state.get("reference") if isinstance(state.get("reference"), dict) else {}
    tracks = state.get("tracks") if isinstance(state.get("tracks"), list) else []
    track_count = sum(1 for track in tracks if isinstance(track, dict) and track.get("type") not in ("sequence", "ruler"))
    return {
        "id": record.get("id"),
        "name": record.get("name"),
        "createdAt": record.get("createdAt"),
        "updatedAt": record.get("updatedAt"),
        "genome": state.get("genome") or reference.get("id") or "custom",
        "locus": state.get("locus") or "",
        "trackCount": track_count,
        "highlightCount": len(state.get("genomeCanvasHighlights", [])) if isinstance(state.get("genomeCanvasHighlights"), list) else 0,
    }


def list_profiles(owner="genome"):
    owner = normalized_profile_owner(owner)
    directory = profile_directory(owner)
    directory.mkdir(parents=True, exist_ok=True)
    paths = list(directory.glob("*.json"))
    # Profiles created before per-user isolation belong to the shared genome account.
    if owner == "genome":
        paths.extend(PROFILE_DIR.glob("*.json"))
    profiles = []
    seen = set()
    for path in paths:
        record = read_json(path, None)
        profile_id = record.get("id") if isinstance(record, dict) else None
        if profile_id in seen:
            continue
        if isinstance(record, dict) and isinstance(record.get("state"), dict):
            seen.add(profile_id)
            profiles.append(profile_summary(record))
    profiles.sort(key=lambda item: item.get("updatedAt") or "", reverse=True)
    return profiles



# Shared tracks: every file a workspace has loaded from a shared data root (not a Home folder), listed for the
# whole lab so others can load it without looking for its path. The list follows the files: a row whose file was
# deleted or moved is dropped, and a rewritten file (new size or mtime) is marked updated, when the list is read
# (at most every SHARED_TRACK_CHECK_SECONDS).
SHARED_TRACK_CHECK_SECONDS = 30
SHARED_TRACK_CHECK = {"at": 0.0}
SHARED_TRACK_LOCK = Lock()


def check_shared_tracks(store, force=False):
    with SHARED_TRACK_LOCK:
        if not force and time.monotonic() - SHARED_TRACK_CHECK["at"] < SHARED_TRACK_CHECK_SECONDS:
            return
        SHARED_TRACK_CHECK["at"] = time.monotonic()
        gone = []
        for item in store.list_shared_tracks(include_hidden=True):
            if item["root"] not in DATA_ROOTS:
                continue  # a root that is not configured now: kept for when it is back
            try:
                path = resolve_data_path(item["root"], item["path"], DATA_ROOTS)
                stat = path.stat()
                if not path.is_file():
                    raise FileNotFoundError(item["path"])
            except (OSError, PermissionError):
                gone.append((item["root"], item["path"]))
                continue
            if stat.st_size != item["size"] or int(stat.st_mtime) != item["modified"]:
                store.update_shared_track_file(item["root"], item["path"], stat.st_size, int(stat.st_mtime))
        store.delete_shared_tracks(gone)


def seed_shared_tracks(store, home_root=HOME_ROOT):
    """Server files in views saved before the shared list existed (share links, Favorites): add those still there."""
    if LOCAL_MODE:
        return 0
    workspaces = {item["id"]: item for item in workspace_catalog(store, home_root)}
    records = sorted(SESSION_DIR.glob("*.json")) + sorted(PROFILE_DIR.glob("**/*.json"))
    added = 0
    for record_path in records:
        record = read_json(record_path, None)
        if not isinstance(record, dict):
            continue
        view = record.get("state") if isinstance(record.get("state"), dict) else {}
        reference = view.get("reference")
        genome = str((reference.get("id") if isinstance(reference, dict) else reference) or view.get("genome") or "")[:40]
        created = str(record.get("createdAt") or record.get("updatedAt") or datetime.now(timezone.utc).isoformat())
        for track in view.get("tracks") or []:
            url = urlparse(str(track.get("url") or "") if isinstance(track, dict) else "")
            path = unquote(url.path)
            if "/data/" not in path:
                continue
            parts = [part for part in path.split("/data/", 1)[1].split("/") if part]
            owner = None
            if parts and re.fullmatch(r"(home|manual)-[0-9a-f]{18}", parts[0]):
                owner = workspaces.get(parts[0])
                parts = parts[1:]
            if len(parts) < 2 or parts[0] not in DATA_ROOTS:
                continue
            try:
                candidate = resolve_data_path(parts[0], "/".join(parts[1:]), DATA_ROOTS)
                if not candidate.is_file() or not is_track_file(candidate):
                    continue
                stat = candidate.stat()
                file_format, _ = detect_format(format_filename(candidate))
                relative = str(candidate.relative_to(DATA_ROOTS[parts[0]]["path"]))
            except (OSError, PermissionError, ValueError):
                continue
            store.seed_shared_track(parts[0], relative, candidate.name, file_format, "track", stat.st_size,
                                    int(stat.st_mtime), genome, owner, created)
            added += 1
    return added


def record_shared_track(store, root_id, path, kind, genome, workspace):
    """Remember a server file a workspace has just loaded (shared data roots only)."""
    if LOCAL_MODE or root_id not in DATA_ROOTS or not workspace:
        return
    try:
        relative = str(normalized_absolute(path).relative_to(DATA_ROOTS[root_id]["path"]))
        stat = path.stat()
        file_format, _ = detect_format(format_filename(path))
        store.record_shared_track(root_id, relative, path.name, file_format, kind, stat.st_size,
                                  int(stat.st_mtime), str(genome or "")[:40], workspace)
    except Exception as exc:  # the track loads either way
        print("Warning: could not record shared track {}: {}".format(path, exc), file=sys.stderr)


class GenomeCanvasHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "GenomeCanvas/1.0"
    disable_nagle_algorithm = True

    def log_message(self, fmt, *args):
        print("{} - {}".format(self.address_string(), fmt % args))

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        super().end_headers()

    def accepts_gzip(self):
        accepted = False
        for token in self.headers.get("Accept-Encoding", "").split(","):
            parts = token.strip().split(";")
            if parts[0].strip().lower() != "gzip":
                continue
            quality = next((part.strip()[2:] for part in parts[1:] if part.strip().startswith("q=")), "1")
            try:
                accepted = float(quality) > 0
            except ValueError:
                pass
        return accepted

    def send_json(self, payload, status=200, headers=None):
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        compressed = len(body) >= GZIP_MIN_BYTES and self.accepts_gzip()
        if compressed:
            body = gzip.compress(body, compresslevel=5, mtime=0)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Vary", "Accept-Encoding")
        if compressed:
            self.send_header("Content-Encoding", "gzip")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def send_api_error(self, status, message):
        self.send_json({"error": message}, status)

    @property
    def workspace_store(self):
        return self.server.workspace_store

    def cookie_path(self):
        prefix = self.headers.get("X-Forwarded-Prefix", "").strip()
        if prefix and re.fullmatch(r"/[A-Za-z0-9._~/-]+", prefix):
            return prefix.rstrip("/") + "/"
        return "/"

    def selected_workspace_id(self):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except Exception:
            return ""
        morsel = cookie.get(WORKSPACE_COOKIE_NAME)
        return morsel.value if morsel else ""

    def current_workspace(self):
        # BaseHTTPRequestHandler reuses one handler instance for all requests
        # on an HTTP/1.1 keep-alive connection. Cache only for the current
        # parsed headers object so a changed workspace cookie takes effect on
        # the very next request.
        if getattr(self, "_workspace_request_headers", None) is not self.headers:
            self._workspace_request_headers = self.headers
            self._current_workspace = find_workspace(
                self.workspace_store,
                self.selected_workspace_id(),
                self.server.home_root,
            )
        return self._current_workspace

    def discard_request_body(self, maximum_bytes=MAX_PROFILE_BYTES):
        """Consume a rejected request body so keep-alive stays synchronized."""
        if self.headers.get("Transfer-Encoding"):
            return False
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return False
        if length < 0 or length > maximum_bytes:
            return False
        remaining = length
        try:
            while remaining:
                chunk = self.rfile.read(min(BUFFER_SIZE, remaining))
                if not chunk:
                    return False
                remaining -= len(chunk)
        except OSError:
            return False
        return True

    def authenticated_user(self):
        workspace = self.current_workspace()
        if not workspace:
            raise PermissionError("Select a workspace first")
        if workspace.get("kind") == "home" and workspace.get("name") == Path.home().name:
            # Preserve Favorites saved by the original shared deployment.
            return "genome"
        return normalized_profile_owner(workspace["id"])

    def require_workspace(self):
        workspace = self.current_workspace()
        if not workspace:
            headers = None
            if self.command in ("POST", "PUT", "PATCH") and not self.discard_request_body():
                # Do not let unread body bytes become the next request line.
                self.close_connection = True
                headers = {"Connection": "close"}
            self.send_json(
                {"error": "Select a workspace first", "workspaceRequired": True},
                409,
                headers,
            )
            return None
        return workspace

    def reject_local_workspace_mutation(self, message):
        if not LOCAL_MODE:
            return False
        headers = None
        if self.command in ("POST", "PUT", "PATCH") and not self.discard_request_body(MAX_WORKSPACE_BYTES):
            self.close_connection = True
            headers = {"Connection": "close"}
        self.send_json({"error": message}, 403, headers)
        return True

    def same_origin_request(self):
        origin = self.headers.get("Origin")
        if not origin:
            return True
        return urlparse(origin).netloc == self.headers.get("Host", "")

    def workspace_cookie(self, workspace_id, maximum_age=31536000):
        pieces = [
            "{}={}".format(WORKSPACE_COOKIE_NAME, workspace_id),
            "Path={}".format(self.cookie_path()),
            "HttpOnly",
            "SameSite=Lax",
            "Max-Age={}".format(maximum_age),
        ]
        return "; ".join(pieces)

    @staticmethod
    def public_workspace(workspace):
        if not workspace:
            return None
        return {
            "id": workspace["id"],
            "name": workspace["name"],
            "kind": workspace["kind"],
        }

    def workspace_payload(self):
        workspaces = workspace_catalog(self.workspace_store, self.server.home_root)
        if LOCAL_MODE:
            workspace = LOCAL_WORKSPACE.copy()
            return {
                "selected": self.public_workspace(workspace),
                "workspaces": [self.public_workspace(workspace)],
                "hiddenHomeWorkspaces": [],
                "localMode": True,
            }
        existing_homes = {item["id"]: item for item in discover_home_workspaces(self.server.home_root)}
        hidden = [
            item for item in self.workspace_store.hidden_homes()
            if item["id"] in existing_homes
        ]
        return {
            "selected": self.public_workspace(self.current_workspace()),
            "workspaces": [self.public_workspace(item) for item in workspaces],
            "hiddenHomeWorkspaces": [self.public_workspace(item) for item in hidden],
        }

    def data_roots(self):
        return data_roots_for_workspace(self.current_workspace())

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Methods", "GET, HEAD, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Range")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        query = parse_qs(parsed.query)

        try:
            if path == "/api/health":
                self.send_json({"ok": True, "version": "1.0.0"})
            elif path == "/api/workspaces":
                self.send_json(self.workspace_payload())
            elif path in STATIC_FILES:
                self.handle_static(path)
            elif path.startswith("/vendor/fonts/"):
                name = path[len("/vendor/fonts/"):]
                font = FONT_DIR / name
                if not re.fullmatch(r"[a-z0-9-]+\.woff2", name) or not font.is_file():
                    raise FileNotFoundError("Font not found")
                # Font files are content-stable; renaming a file is the cache key.
                self.send_file(font, cache_control="public, max-age=31536000, immutable", memory_cache=True)
            elif path.startswith("/reference/"):
                reference = REFERENCE_CACHE.resolve(path[len("/reference/"):])
                self.send_file(reference, cache_control="public, max-age=3600", memory_cache=True)
            elif path.startswith("/data/"):
                # IGV.js may omit cookies on a subset of parallel byte-range
                # requests. New local-data URLs carry their workspace id so
                # these requests can be resolved consistently without a cookie.
                self.handle_data_file(parsed.path, query)
            elif not self.require_workspace():
                return
            elif path == "/api/config":
                workspace = self.current_workspace()
                roots = self.data_roots()
                default_file = default_file_location(workspace, roots)
                references = REFERENCE_CACHE.snapshot()
                self.send_json({
                    "appName": SETTINGS.get("appName", "Genome Canvas"),
                    "user": workspace["name"],
                    "workspaceId": workspace["id"],
                    "workspaceKind": workspace["kind"],
                    "localMode": LOCAL_MODE,
                    "defaultGenome": SETTINGS.get("defaultGenome", "hg38"),
                    "defaultLocus": SETTINGS.get("defaultLocus", "chr8:127,728,000-127,742,000"),
                    "defaultFileRoot": default_file["root"],
                    "defaultFilePath": default_file["path"],
                    "genomeList": references.get("genomes", []),
                    "referenceResources": references.get("resources", {}),
                    "indexedReferenceTracks": references.get("indexedTracks", {}),
                    "cachedGenomes": references.get("cachedGenomes", []),
                    "roots": [
                        {"id": root_id, "label": item["label"]}
                        for root_id, item in roots.items()
                    ],
                })
            elif path == "/api/files":
                roots = self.data_roots()
                root_id = query.get("root", [next(iter(roots))])[0]
                relative_path = query.get("path", [""])[0]
                self.send_json(directory_payload(root_id, relative_path, roots))
            elif path == "/api/track":
                self.handle_track_config(query)
            elif path == "/api/shared-tracks":
                self.handle_shared_tracks(query)
            elif path == "/api/public-hub":
                self.handle_public_hub(query)
            elif path == "/api/variant-search":
                rsid = query.get("q", [""])[0]
                genome = query.get("genome", [""])[0]
                self.send_json(resolve_rsid(rsid, genome))
            elif path == "/api/gwas-ld":
                self.send_json(calculate_local_ld(
                    query.get("chrom", [""])[0],
                    query.get("start", [""])[0],
                    query.get("end", [""])[0],
                    query.get("lead", [""])[0],
                    query.get("ref", [""])[0],
                    query.get("alt", [""])[0],
                ))
            elif path == "/api/profiles":
                workspace = self.current_workspace()
                self.send_json({"user": workspace["name"], "workspaceId": workspace["id"], "profiles": list_profiles(self.authenticated_user())})
            elif path.startswith("/api/profiles/"):
                self.handle_get_profile(path.rsplit("/", 1)[-1])
            elif path.startswith("/api/sessions/"):
                self.handle_get_session(path.rsplit("/", 1)[-1])
            else:
                self.send_error(404, "Not found")
        except FileNotFoundError as exc:
            self.send_api_error(404, str(exc))
        except NotADirectoryError as exc:
            self.send_api_error(400, str(exc))
        except PermissionError as exc:
            self.send_api_error(403, str(exc))
        except (OSError, ValueError) as exc:
            self.send_api_error(400, str(exc))

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/workspaces":
            self.handle_create_workspace()
            return
        if path == "/api/workspaces/select":
            self.handle_select_workspace()
            return
        if path == "/api/workspaces/restore":
            self.handle_restore_workspace()
            return
        if not self.require_workspace():
            return
        if path == "/api/sessions":
            self.handle_create_session()
        elif path == "/api/shared-tracks/hide":
            self.handle_hide_shared_track()
        elif path == "/api/profiles":
            self.handle_save_profile()
        else:
            self.send_api_error(404, "Not found")

    def read_json_body(self, maximum_bytes, label):

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ValueError("Invalid Content-Length")
        if length <= 0 or length > maximum_bytes:
            raise ValueError("{} must be between 1 byte and 5 MB".format(label))

        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError("Invalid JSON")
        if not isinstance(payload, dict):
            raise ValueError("{} must be a JSON object".format(label))
        return payload

    def read_workspace_body(self):
        return self.read_json_body(MAX_WORKSPACE_BYTES, "Workspace request")

    def handle_create_workspace(self):
        if self.reject_local_workspace_mutation("Workspaces are disabled in local mode"):
            return
        if not self.same_origin_request():
            self.send_api_error(403, "Cross-site workspace changes are not allowed")
            return
        try:
            payload = self.read_workspace_body()
            workspace = self.workspace_store.create(payload.get("name"))
        except (WorkspaceError, ValueError) as exc:
            self.send_api_error(400, str(exc))
            return
        self._current_workspace = workspace
        self.send_json(
            {"workspace": self.public_workspace(workspace)},
            201,
            {"Set-Cookie": self.workspace_cookie(workspace["id"])},
        )

    def handle_select_workspace(self):
        if self.reject_local_workspace_mutation("Workspace switching is disabled in local mode"):
            return
        if not self.same_origin_request():
            self.send_api_error(403, "Cross-site workspace changes are not allowed")
            return
        try:
            payload = self.read_workspace_body()
            workspace = find_workspace(self.workspace_store, payload.get("id"), self.server.home_root)
            if not workspace:
                raise WorkspaceError("Workspace was not found")
        except (WorkspaceError, ValueError) as exc:
            self.send_api_error(400, str(exc))
            return
        self._current_workspace = workspace
        self.send_json(
            {"workspace": self.public_workspace(workspace)},
            headers={"Set-Cookie": self.workspace_cookie(workspace["id"])},
        )

    def handle_restore_workspace(self):
        if self.reject_local_workspace_mutation("Workspaces are disabled in local mode"):
            return
        if not self.same_origin_request():
            self.send_api_error(403, "Cross-site workspace changes are not allowed")
            return
        try:
            payload = self.read_workspace_body()
            workspace_id = str(payload.get("id") or "")
            existing = next(
                (item for item in discover_home_workspaces(self.server.home_root) if item["id"] == workspace_id),
                None,
            )
            if not existing:
                raise WorkspaceError("Home directory was not found")
            self.workspace_store.restore_home(workspace_id)
        except (WorkspaceError, ValueError) as exc:
            self.send_api_error(400, str(exc))
            return
        self.send_json({"workspace": self.public_workspace(existing)})

    def handle_create_session(self):
        try:
            payload = self.read_json_body(MAX_SESSION_BYTES, "Session")
        except ValueError as exc:
            self.send_api_error(400, str(exc))
            return

        SESSION_DIR.mkdir(parents=True, exist_ok=True)
        session_id = secrets.token_urlsafe(8).replace("-", "a").replace("_", "b")
        record = {
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "state": payload,
        }
        target = SESSION_DIR / (session_id + ".json")
        temporary = SESSION_DIR / (session_id + ".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(record, handle, ensure_ascii=False, separators=(",", ":"))
        temporary.replace(target)
        self.send_json({"id": session_id}, 201)

    def handle_save_profile(self):
        try:
            payload = self.read_json_body(MAX_PROFILE_BYTES, "Profile")
            name = normalized_profile_name(payload.get("name"))
            state = payload.get("state")
            if not isinstance(state, dict):
                raise ValueError("Profile state must be a JSON object")
        except ValueError as exc:
            self.send_api_error(400, str(exc))
            return

        owner = self.authenticated_user()
        directory = profile_directory(owner)
        directory.mkdir(parents=True, exist_ok=True)
        profile_id = profile_id_for_name(name)
        target = directory / (profile_id + ".json")
        previous_path = next((candidate for candidate in profile_candidates(owner, profile_id) if candidate.is_file()), target)
        previous = read_json(previous_path, {})
        now = datetime.now(timezone.utc).isoformat()
        record = {
            "schema": "genome-canvas-profile",
            "version": 1,
            "id": profile_id,
            "owner": owner,
            "name": name,
            "createdAt": previous.get("createdAt", now),
            "updatedAt": now,
            "state": state,
        }
        temporary = directory / (profile_id + ".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(record, handle, ensure_ascii=False, separators=(",", ":"))
        temporary.replace(target)
        self.send_json({"profile": profile_summary(record)}, 201 if not previous else 200)

    def handle_get_profile(self, profile_id):
        if not re.fullmatch(r"[a-f0-9]{20}", profile_id):
            self.send_api_error(400, "Invalid profile id")
            return
        owner = self.authenticated_user()
        target = next((candidate for candidate in profile_candidates(owner, profile_id) if candidate.is_file()), None)
        record = read_json(target, None) if target else None
        if not record:
            self.send_api_error(404, "Favorite profile not found")
            return
        self.send_json(record)

    def do_DELETE(self):
        path = unquote(urlparse(self.path).path)
        if path.startswith("/api/workspaces/"):
            if self.reject_local_workspace_mutation("Workspaces are disabled in local mode"):
                return
            if not self.same_origin_request():
                self.send_api_error(403, "Cross-site workspace changes are not allowed")
                return
            workspace_id = path.rsplit("/", 1)[-1]
            workspace = self.workspace_store.get_manual(workspace_id)
            if workspace:
                try:
                    self.workspace_store.delete(workspace_id)
                except WorkspaceError as exc:
                    self.send_api_error(400, str(exc))
                    return
            else:
                workspace = next(
                    (item for item in discover_home_workspaces(self.server.home_root) if item["id"] == workspace_id),
                    None,
                )
                if not workspace:
                    self.send_api_error(404, "Workspace was not found")
                    return
                self.workspace_store.hide_home(workspace_id, workspace["name"])
            headers = {}
            if self.selected_workspace_id() == workspace_id:
                self._current_workspace = None
                headers["Set-Cookie"] = self.workspace_cookie("", 0)
            self.send_json({"deleted": workspace_id, "hidden": workspace["kind"] == "home"}, headers=headers)
            return
        if not self.require_workspace():
            return
        if not path.startswith("/api/profiles/"):
            self.send_api_error(404, "Not found")
            return
        profile_id = path.rsplit("/", 1)[-1]
        if not re.fullmatch(r"[a-f0-9]{20}", profile_id):
            self.send_api_error(400, "Invalid profile id")
            return
        owner = self.authenticated_user()
        targets = [candidate for candidate in profile_candidates(owner, profile_id) if candidate.is_file()]
        if not targets:
            self.send_api_error(404, "Favorite profile not found")
            return
        for target in targets:
            target.unlink()
        self.send_json({"deleted": profile_id})

    def handle_get_session(self, session_id):
        if not re.fullmatch(r"[A-Za-z0-9]{6,32}", session_id):
            self.send_api_error(400, "Invalid session id")
            return
        record = read_json(SESSION_DIR / (session_id + ".json"), None)
        if not record:
            self.send_api_error(404, "Shared view not found")
            return
        self.send_json(record["state"])

    def handle_track_config(self, query):
        workspace = self.current_workspace()
        workspace_id = workspace["id"]
        roots = self.data_roots()
        root_id = query.get("root", [""])[0]
        relative_path = query.get("path", [""])[0]
        path = resolve_data_path(root_id, relative_path, roots)
        if not path.is_file() or not is_track_file(path):
            raise FileNotFoundError("Track file not found or unsupported")

        file_format, track_type = detect_format(format_filename(path))
        root_path = roots[root_id]["path"]
        track = {
            "name": path.name,
            "url": relative_url(root_id, path.relative_to(root_path), workspace_id),
            "format": file_format,
        }
        if track_type not in ("annotation", "reference"):
            track["type"] = track_type

        if track_type == "gwas":
            # Region-oriented association view.  Indexed files are queried by
            # locus instead of being downloaded in full, which is essential
            # for cohort-scale summary statistics.
            track.update({
                "height": 220,
                "autoscale": True,
                "dotSize": 6,
                "useChrColors": False,
                "color": "#74868a",
                "visibilityWindow": 50000000,
                "genomeCanvasManhattan": True,
                "significancePValue": 5e-8,
            })
            ld_settings = ld_reference_settings()
            if ld_settings:
                track.update({
                    "ldEndpoint": "api/gwas-ld",
                    "ldLabel": str(ld_settings.get("label") or "1000 Genomes Phase 3 ALL"),
                    "ldMaxWindow": int(ld_settings.get("maxWindow") or DEFAULT_MAX_LD_WINDOW),
                })

        indexes = index_candidates(path)
        if indexes:
            try:
                track["indexURL"] = data_url_for_path(indexes[0], root_id, roots, workspace_id)
            except PermissionError:
                track["indexURL"] = companion_data_url(path, indexes[0], root_id, roots, workspace_id)
            track["indexed"] = True

        if track_type == "reference":
            reference = {
                "id": path.stem.replace(".fa", ""),
                "name": path.name,
            }
            if file_format == "2bit":
                reference["twoBitURL"] = track["url"]
            else:
                reference["fastaURL"] = track["url"]
                if track.get("indexURL"):
                    reference["indexURL"] = track["indexURL"]
            record_shared_track(self.workspace_store, root_id, path, "reference", query.get("genome", [""])[0], workspace)
            self.send_json({"kind": "reference", "reference": reference})
            return

        record_shared_track(self.workspace_store, root_id, path, "track", query.get("genome", [""])[0], workspace)
        self.send_json({"kind": "track", "track": track})

    def handle_shared_tracks(self, query):
        if LOCAL_MODE:
            self.send_json({"tracks": [], "disabled": True})
            return
        workspace = self.current_workspace()
        check_shared_tracks(self.workspace_store, force=query.get("refresh", [""])[0] == "1")
        tracks = []
        for item in self.workspace_store.list_shared_tracks():
            root = DATA_ROOTS.get(item["root"])
            if not root:
                continue
            item["rootLabel"] = root["label"]
            item["mine"] = item.pop("ownerId") == workspace["id"]
            item["loadedByMe"] = workspace["name"] in item["users"]
            tracks.append(item)
        self.send_json({"tracks": tracks})

    def handle_hide_shared_track(self):
        if not self.same_origin_request():
            self.send_api_error(403, "Cross-site changes are not allowed")
            return
        try:
            body = self.read_json_body(MAX_WORKSPACE_BYTES, "Shared track request")
            self.workspace_store.hide_shared_track(str(body.get("root", "")), str(body.get("path", "")),
                                                   self.current_workspace()["id"])
        except PermissionError as exc:
            self.send_api_error(403, str(exc))
            return
        except (WorkspaceError, ValueError) as exc:
            self.send_api_error(400, str(exc))
            return
        self.send_json({"hidden": True})

    def handle_public_hub(self, query):
        url = query.get("url", [""])[0].strip()
        genome = query.get("genome", [""])[0].strip()
        kind = query.get("kind", [""])[0].strip().lower()
        if not url or not genome:
            raise ValueError("Public hub URL and genome are required")
        result = load_public_hub(url, genome, kind)
        if kind == "ucsc":
            try:
                offset = int(query.get("offset", ["0"])[0])
                limit = int(query.get("limit", ["200"])[0])
            except ValueError:
                raise ValueError("Hub page offset and limit must be integers")
            self.send_json(paginate_public_hub(result, query.get("q", [""])[0], offset, limit))
        else:
            self.send_json(result)

    def handle_static(self, request_path):
        relative, content_type = STATIC_FILES[request_path]
        path = (APP_DIR / relative).resolve()
        if not within_root(path, APP_DIR) or not path.is_file():
            raise FileNotFoundError("Static file not found")
        stat = path.stat()
        replacements = ()
        if request_path == "/vendor/igv.min.js":
            mapping_url = "https://igv.org/data/url_mappings.tsv"
            local_mapping = REFERENCE_CACHE.snapshot().get("resources", {}).get(mapping_url)
            if local_mapping:
                replacements = ((mapping_url.encode(), ("." + local_mapping).encode()),)
        compressed = self.accepts_gzip()
        variant = hashlib.sha256(repr(replacements).encode()).hexdigest()[:12] if replacements else ""
        etag = '"{:x}-{:x}-{}{}"'.format(stat.st_mtime_ns, stat.st_size, variant, "-gzip" if compressed else "")
        has_version = "v" in parse_qs(urlparse(self.path).query)
        cache = "public, max-age=86400, immutable" if has_version else "no-cache"
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", cache)
            self.send_header("Vary", "Accept-Encoding")
            self.end_headers()
            return
        body = FILE_MEMORY_CACHE.get(path, stat, compressed, replacements)
        if body is None:
            body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.send_header("ETag", etag)
        self.send_header("Last-Modified", formatdate(stat.st_mtime, usegmt=True))
        self.send_header("Vary", "Accept-Encoding")
        if compressed:
            self.send_header("Content-Encoding", "gzip")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def handle_data_file(self, raw_path, query=None):
        remainder = raw_path[len("/data/"):]
        if "/" not in remainder:
            raise FileNotFoundError("Missing data path")
        first_segment, remaining = remainder.split("/", 1)
        scoped_parts = remaining.split("/", 1)
        scoped_workspace_id = unquote(first_segment)
        looks_scoped = scoped_workspace_id == "local" or bool(re.fullmatch(r"(?:home|manual)-[a-f0-9]{18}", scoped_workspace_id))
        workspace = find_workspace(
            self.workspace_store,
            scoped_workspace_id,
            self.server.home_root,
        )
        if workspace:
            if "/" not in remaining:
                raise FileNotFoundError("Missing data path")
            root_id, encoded_relative = remaining.split("/", 1)
            roots = data_roots_for_workspace(workspace)
        elif looks_scoped and len(scoped_parts) == 2 and scoped_parts[0] in DATA_ROOTS:
            # Shared roots do not belong to a workspace. Keep their scoped URLs
            # valid after a manual workspace is deleted or a Home is hidden.
            root_id, encoded_relative = scoped_parts
            roots = DATA_ROOTS
        elif looks_scoped:
            raise FileNotFoundError("Workspace not found")
        elif first_segment in DATA_ROOTS:
            # Shared configured roots are identical for every password-free
            # workspace. Supporting their legacy URLs also repairs tracks that
            # were already open when workspace-scoped URLs were introduced.
            root_id, encoded_relative = first_segment, remaining
            roots = DATA_ROOTS
        else:
            # A legacy Home-root URL still requires the selected workspace.
            # The frontend upgrades it when loading a saved profile.
            if not self.require_workspace():
                return
            root_id, encoded_relative = first_segment, remaining
            roots = self.data_roots()
        relative_path = "/".join(unquote(part) for part in encoded_relative.split("/"))
        path = resolve_data_path(root_id, relative_path, roots)
        companion = (query or {}).get("companion", [""])[0]
        if companion:
            matches = [candidate for candidate in index_candidates(path) if candidate.name == companion]
            if not matches:
                raise FileNotFoundError("Track companion file not found")
            path = matches[0]
        if not path.is_file():
            raise FileNotFoundError("Data file not found")

        self.send_file(path, compressible=path.name.lower().endswith(PLAIN_TEXT_TRACK_SUFFIXES))

    def send_compressed_file(self, path, stat, cache_control):
        """Send a whole plain-text track gzip encoded; returns False to fall back."""
        etag = '"{:x}-{:x}-gzip"'.format(stat.st_mtime_ns, stat.st_size)
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Vary", "Accept-Encoding")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return True
        body = FILE_MEMORY_CACHE.get(path, stat, True)
        if body is None:
            return False
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("ETag", etag)
        self.send_header("Last-Modified", formatdate(stat.st_mtime, usegmt=True))
        self.send_header("Cache-Control", cache_control)
        self.send_header("Vary", "Accept-Encoding")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Expose-Headers", "Content-Length, Content-Range, Accept-Ranges")
        self.end_headers()
        if self.command != "HEAD":
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass
        return True

    def send_file(self, path, cache_control="private, max-age=300", memory_cache=False, compressible=False):
        stat = path.stat()
        size = stat.st_size
        etag = '"{:x}-{:x}"'.format(stat.st_mtime_ns, size)
        range_header = self.headers.get("Range")
        start, end, status = 0, max(0, size - 1), 200

        if (compressible and not range_header and size >= GZIP_MIN_BYTES
                and size <= FILE_MEMORY_CACHE.item_limit and self.accepts_gzip()
                and self.send_compressed_file(path, stat, cache_control)):
            return

        if range_header:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip())
            if not match or (not match.group(1) and not match.group(2)):
                self.send_range_error(size)
                return
            if match.group(1):
                start = int(match.group(1))
                end = int(match.group(2)) if match.group(2) else size - 1
            else:
                suffix = int(match.group(2))
                start = max(0, size - suffix)
                end = size - 1
            if start >= size or start < 0 or end < start:
                self.send_range_error(size)
                return
            end = min(end, size - 1)
            status = 206
        elif self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        length = 0 if size == 0 else end - start + 1
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("ETag", etag)
        self.send_header("Last-Modified", formatdate(stat.st_mtime, usegmt=True))
        self.send_header("Cache-Control", cache_control)
        if compressible:
            self.send_header("Vary", "Accept-Encoding")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Expose-Headers", "Content-Length, Content-Range, Accept-Ranges")
        if status == 206:
            self.send_header("Content-Range", "bytes {}-{}/{}".format(start, end, size))
        self.end_headers()

        if self.command == "HEAD" or length == 0:
            return
        try:
            if memory_cache:
                body = FILE_MEMORY_CACHE.get(path, stat)
                if body is not None:
                    self.wfile.write(memoryview(body)[start:end + 1])
                    return
            with path.open("rb") as handle:
                handle.seek(start)
                remaining = length
                while remaining:
                    chunk = handle.read(min(BUFFER_SIZE, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def send_range_error(self, size):
        self.send_response(416)
        self.send_header("Content-Range", "bytes */{}".format(size))
        self.send_header("Content-Length", "0")
        self.end_headers()


class GenomeCanvasHTTPServer(ThreadingHTTPServer):
    """Threaded server that does not log normal client disconnects as errors."""

    request_queue_size = 256

    def __init__(self, server_address, request_handler, workspace_store, home_root=HOME_ROOT):
        self.workspace_store = workspace_store
        self.home_root = normalized_absolute(home_root)
        super().__init__(server_address, request_handler)

    def handle_error(self, request, client_address):
        error = sys.exc_info()[1]
        if isinstance(error, (BrokenPipeError, ConnectionAbortedError, ConnectionResetError)):
            return
        super().handle_error(request, client_address)


def main():
    parser = argparse.ArgumentParser(description="Run Genome Canvas on the local network")
    parser.add_argument("--host", default=os.environ.get("GENOME_CANVAS_HOST", SETTINGS.get("host", "0.0.0.0")))
    parser.add_argument("--port", type=int, default=int(os.environ.get("GENOME_CANVAS_PORT", SETTINGS.get("port", 8000))))
    parser.add_argument("--ready-file", help="Write the bound host and port as JSON after startup")
    args = parser.parse_args()

    if LOCAL_MODE and args.host not in ("127.0.0.1", "localhost", "::1"):
        parser.error("local mode must bind to a loopback address")

    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    workspace_store = WorkspaceStore(WORKSPACE_DB_FILE)
    try:
        seed_shared_tracks(workspace_store)
    except Exception as exc:
        print("Warning: could not read saved views for the shared track list: {}".format(exc), file=sys.stderr)
    server = GenomeCanvasHTTPServer((args.host, args.port), GenomeCanvasHandler, workspace_store, HOME_ROOT)
    server.daemon_threads = True
    bound_host, bound_port = server.server_address[:2]
    if args.ready_file:
        ready_file = Path(os.path.expanduser(args.ready_file))
        ready_file.parent.mkdir(parents=True, exist_ok=True)
        temporary_ready_file = ready_file.with_suffix(ready_file.suffix + ".tmp")
        temporary_ready_file.write_text(
            json.dumps({"host": bound_host, "port": bound_port}),
            encoding="utf-8",
        )
        temporary_ready_file.replace(ready_file)
    print("Genome Canvas is running on http://{}:{}".format(bound_host, bound_port))
    print("Serving {} configured data root(s):".format(len(DATA_ROOTS)))
    for item in DATA_ROOTS.values():
        print("  - {}: {}".format(item["label"], item["path"]))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Genome Canvas")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
