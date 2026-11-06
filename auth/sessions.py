"""Sign-in sessions of Omics Canvas, shared by every backend (hub, Genome Canvas, GREAT) and the auth server.

One cookie for the whole site, `omics_canvas_session` (Path=/, HttpOnly, SameSite=Lax), holds a random token; the
server keeps only its SHA-256 in <state>/auth/auth.sqlite3 with who signed in (a THOR account, or an access password
set in the admin page), when, and until when. "Remember me" sessions last `rememberDays` (default 365) after their
last use; others end with the browser and after `idleHours` without use. Sessions of an access password end when the
password expires, is disabled or deleted.

Backends call `Gate(state_dir).identity(headers, client_address)`: the signed-in identity, or None. Requests a backend
makes to another on 127.0.0.1 (no X-Forwarded-For: they did not come through the gateway) count as trusted. Results
are cached a few seconds, so a revoked session stops working within CACHE_SECONDS.

Standard library only (Python 3.8+): Genome Canvas and GREAT import it from ../auth.
"""

import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from http.cookies import SimpleCookie
from pathlib import Path

COOKIE = "omics_canvas_session"
CACHE_SECONDS = 15
TOUCH_SECONDS = 600  # how often a session's last use is written back
DEFAULT_SETTINGS = {
    "admins": [],            # THOR accounts that see the admin page (empty: the account that runs the server)
    "rememberDays": 365,     # "Remember me": signed in this long after the last use
    "idleHours": 12,         # without "Remember me": signed out after this long without use (and when the browser closes)
    "lockoutAttempts": 5,    # failed sign-ins from one address or for one account ...
    "lockoutMinutes": 15,    # ... within this window lock it for this long
    "minUid": 1000,          # THOR accounts below this uid (system accounts) cannot sign in
    "allowedAccounts": [],   # if set, only these THOR accounts can sign in
}
PBKDF2_ROUNDS = 200000
TRUSTED_NETWORKS = (ipaddress.ip_network("127.0.0.0/8"), ipaddress.ip_network("::1/128"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY, user TEXT NOT NULL, method TEXT NOT NULL, password_id TEXT,
    created REAL NOT NULL, last_seen REAL NOT NULL, remember INTEGER NOT NULL, ip TEXT, agent TEXT);
CREATE TABLE IF NOT EXISTS passwords (
    id TEXT PRIMARY KEY, label TEXT NOT NULL, salt TEXT NOT NULL, hash TEXT NOT NULL, created REAL NOT NULL,
    expires REAL, disabled INTEGER NOT NULL DEFAULT 0, last_used REAL, uses INTEGER NOT NULL DEFAULT 0, note TEXT);
CREATE TABLE IF NOT EXISTS log (
    time REAL NOT NULL, ip TEXT, user TEXT, method TEXT, ok INTEGER NOT NULL, detail TEXT);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def token_hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def password_digest(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), PBKDF2_ROUNDS).hex()


def cookie_token(cookie_header):
    if not cookie_header:
        return None
    jar = SimpleCookie()
    try:
        jar.load(cookie_header)
    except Exception:  # a malformed cookie from another site on the host: no session
        return None
    morsel = jar.get(COOKIE)
    token = morsel.value if morsel else ""
    return token if re.fullmatch(r"[A-Za-z0-9_-]{32,128}", token or "") else None


def set_cookie_header(token, remember, days):
    parts = ["{}={}".format(COOKIE, token), "Path=/", "HttpOnly", "SameSite=Lax"]
    if remember:
        parts.append("Max-Age={}".format(int(days * 86400)))
    return "; ".join(parts)


def clear_cookie_header():
    return "{}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0".format(COOKIE)


def is_trusted_internal(client_address, headers):
    """A request one backend sends another directly on this machine (not through the gateway)."""
    if headers.get("X-Forwarded-For"):
        return False
    try:
        address = ipaddress.ip_address(client_address)
    except ValueError:
        return False
    return any(address in network for network in TRUSTED_NETWORKS)


class Store:
    """<state>/auth/auth.sqlite3: sessions, access passwords, the sign-in log and settings."""

    def __init__(self, state_dir):
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.state_dir / "auth.sqlite3"
        self.lock = threading.Lock()
        with self.connect() as db:
            db.executescript(SCHEMA)

    def connect(self):
        db = sqlite3.connect(str(self.path), timeout=15)
        db.row_factory = sqlite3.Row
        return db

    # ---------- settings

    def settings(self):
        values = dict(DEFAULT_SETTINGS)
        with self.connect() as db:
            for row in db.execute("SELECT key, value FROM settings"):
                if row["key"] in values:
                    values[row["key"]] = json.loads(row["value"])
        if not values["admins"]:
            values["admins"] = [os.environ.get("USER") or Path.home().name]
        return values

    def update_settings(self, changes):
        limits = {"rememberDays": (1, 3650), "idleHours": (0.1, 720), "lockoutAttempts": (1, 1000),
                  "lockoutMinutes": (1, 10080), "minUid": (0, 1 << 31)}
        clean = {}
        for key, value in changes.items():
            if key not in DEFAULT_SETTINGS:
                raise ValueError("Unknown setting {}".format(key))
            if isinstance(DEFAULT_SETTINGS[key], list):
                if isinstance(value, str):
                    value = [v for v in re.split(r"[\s,]+", value) if v]
                if not isinstance(value, list) or not all(re.fullmatch(r"[a-z_][a-z0-9_.-]{0,31}", str(v)) for v in value):
                    raise ValueError("{} must be a list of account names".format(key))
            else:
                value = float(value)
                low, high = limits[key]
                if not low <= value <= high:
                    raise ValueError("{} must be between {:g} and {:g}".format(key, low, high))
                if key in ("lockoutAttempts", "minUid"):
                    value = int(value)
            clean[key] = value
        with self.connect() as db:
            for key, value in clean.items():
                db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, json.dumps(value)))
        return self.settings()

    # ---------- sessions

    def create_session(self, user, method, remember, ip="", agent="", password_id=None):
        token = secrets.token_urlsafe(32)
        now = time.time()
        with self.connect() as db:
            db.execute("INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                       (token_hash(token), user, method, password_id, now, now, 1 if remember else 0, ip, agent[:300]))
        return token

    def session(self, token, touch=False):
        """The live session of a token as a dict, or None (expired ones are deleted)."""
        if not token:
            return None
        key = token_hash(token)
        now = time.time()
        settings = self.settings()
        with self.connect() as db:
            row = db.execute("SELECT s.*, p.label AS password_label, p.expires AS password_expires, "
                             "p.disabled AS password_disabled, p.id AS password_exists FROM sessions s "
                             "LEFT JOIN passwords p ON p.id = s.password_id WHERE s.token_hash = ?", (key,)).fetchone()
            if row is None:
                return None
            lifetime = settings["rememberDays"] * 86400 if row["remember"] else settings["idleHours"] * 3600
            dead = now - row["last_seen"] > lifetime
            if row["method"] == "password":
                dead = dead or not row["password_exists"] or row["password_disabled"] or \
                    (row["password_expires"] is not None and now > row["password_expires"])
            if dead:
                db.execute("DELETE FROM sessions WHERE token_hash = ?", (key,))
                return None
            if touch and now - row["last_seen"] > TOUCH_SECONDS:
                db.execute("UPDATE sessions SET last_seen = ? WHERE token_hash = ?", (now, key))
        record = dict(row)
        record["admin"] = record["method"] == "account" and record["user"] in settings["admins"]
        record["expires"] = record["last_seen"] + lifetime
        return record

    def delete_session(self, token=None, key=None):
        with self.connect() as db:
            db.execute("DELETE FROM sessions WHERE token_hash = ?", (key or token_hash(token or ""),))

    def sessions(self):
        settings = self.settings()
        with self.connect() as db:
            rows = [dict(r) for r in db.execute(
                "SELECT s.token_hash, s.user, s.method, s.created, s.last_seen, s.remember, s.ip, s.agent, "
                "p.label AS password_label FROM sessions s LEFT JOIN passwords p ON p.id = s.password_id "
                "ORDER BY s.last_seen DESC")]
        now = time.time()
        out = []
        for r in rows:
            lifetime = settings["rememberDays"] * 86400 if r["remember"] else settings["idleHours"] * 3600
            if now - r["last_seen"] <= lifetime:
                r["id"] = r.pop("token_hash")[:16]  # enough to name it, not to use it
                r["expires"] = r["last_seen"] + lifetime
                out.append(r)
        return out

    def delete_session_by_id(self, short_id):
        if not re.fullmatch(r"[0-9a-f]{16}", short_id or ""):
            raise ValueError("Unknown session")
        with self.connect() as db:
            return db.execute("DELETE FROM sessions WHERE substr(token_hash, 1, 16) = ?", (short_id,)).rowcount

    # ---------- access passwords

    def add_password(self, label, password, expires=None, note=""):
        label = (label or "").strip()[:80]
        if not label:
            raise ValueError("Give the access password a name")
        if len(password or "") < 8:
            raise ValueError("Access passwords need at least 8 characters")
        if expires is not None and expires <= time.time():
            raise ValueError("The expiry date is in the past")
        salt = secrets.token_hex(16)
        digest = password_digest(password, salt)
        with self.connect() as db:
            for row in db.execute("SELECT id, salt, hash FROM passwords"):
                if hmac.compare_digest(password_digest(password, row["salt"]), row["hash"]):
                    raise ValueError("This access password is already in use")
            password_id = secrets.token_hex(6)
            db.execute("INSERT INTO passwords (id, label, salt, hash, created, expires, note) VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (password_id, label, salt, digest, time.time(), expires, (note or "")[:300]))
        return password_id

    def passwords(self):
        now = time.time()
        with self.connect() as db:
            rows = [dict(r) for r in db.execute(
                "SELECT p.id, p.label, p.created, p.expires, p.disabled, p.last_used, p.uses, p.note, "
                "(SELECT COUNT(*) FROM sessions s WHERE s.password_id = p.id) AS sessions FROM passwords p "
                "ORDER BY p.created DESC")]
        for r in rows:
            r["state"] = "disabled" if r["disabled"] else "expired" if r["expires"] and now > r["expires"] else "active"
        return rows

    def update_password(self, password_id, disabled=None, expires="keep", label=None, note=None):
        with self.connect() as db:
            if db.execute("SELECT 1 FROM passwords WHERE id = ?", (password_id,)).fetchone() is None:
                raise ValueError("Unknown access password")
            if disabled is not None:
                db.execute("UPDATE passwords SET disabled = ? WHERE id = ?", (1 if disabled else 0, password_id))
            if expires != "keep":
                db.execute("UPDATE passwords SET expires = ? WHERE id = ?", (expires, password_id))
            if label is not None and label.strip():
                db.execute("UPDATE passwords SET label = ? WHERE id = ?", (label.strip()[:80], password_id))
            if note is not None:
                db.execute("UPDATE passwords SET note = ? WHERE id = ?", (note[:300], password_id))

    def delete_password(self, password_id):
        with self.connect() as db:
            db.execute("DELETE FROM sessions WHERE password_id = ?", (password_id,))
            return db.execute("DELETE FROM passwords WHERE id = ?", (password_id,)).rowcount

    def match_password(self, password):
        """The live access password matching `password` (dict), or None."""
        now = time.time()
        with self.connect() as db:
            for row in db.execute("SELECT * FROM passwords WHERE disabled = 0"):
                if row["expires"] is not None and now > row["expires"]:
                    continue
                if hmac.compare_digest(password_digest(password, row["salt"]), row["hash"]):
                    db.execute("UPDATE passwords SET last_used = ?, uses = uses + 1 WHERE id = ?", (now, row["id"]))
                    return dict(row)
        return None

    # ---------- the sign-in log and lockout

    def log(self, ip, user, method, ok, detail=""):
        with self.connect() as db:
            db.execute("INSERT INTO log VALUES (?, ?, ?, ?, ?, ?)", (time.time(), ip, user, method, 1 if ok else 0, detail[:200]))
            db.execute("DELETE FROM log WHERE time < ?", (time.time() - 180 * 86400,))

    def recent_log(self, limit=300):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM log ORDER BY time DESC LIMIT ?", (limit,))]

    def locked(self, ip, user=None):
        """Seconds until a locked address (or account) may try again; 0 when it is not locked."""
        settings = self.settings()
        window = settings["lockoutMinutes"] * 60
        since = time.time() - window
        with self.connect() as db:
            checks = [("ip = ?", ip)] + ([("user = ?", user)] if user else [])
            wait = 0
            for clause, value in checks:
                rows = db.execute("SELECT time FROM log WHERE ok = 0 AND time > ? AND {} ORDER BY time DESC".format(clause),
                                  (since, value)).fetchall()
                last_ok = db.execute("SELECT MAX(time) FROM log WHERE ok = 1 AND {}".format(clause), (value,)).fetchone()[0] or 0
                failures = [r["time"] for r in rows if r["time"] > last_ok]
                if len(failures) >= settings["lockoutAttempts"]:
                    wait = max(wait, int(failures[settings["lockoutAttempts"] - 1] + window - time.time()) + 1)
        return max(0, wait)


class Gate:
    """What a backend asks for each request: who is signed in (cached), or None."""

    def __init__(self, state_dir):
        self.store = Store(state_dir)
        self.cache = {}
        self.lock = threading.Lock()

    def identity(self, headers, client_address):
        if is_trusted_internal(client_address, headers):
            return {"user": "(internal)", "method": "internal", "admin": False}
        token = cookie_token(headers.get("Cookie"))
        if not token:
            return None
        now = time.time()
        with self.lock:
            hit = self.cache.get(token)
            if hit and now - hit[0] < CACHE_SECONDS:
                return hit[1]
        try:
            record = self.store.session(token, touch=True)
        except sqlite3.Error:
            record = hit[1] if hit else None  # a busy database: keep the last answer
        identity = {"user": record["user"], "method": record["method"], "admin": record["admin"],
                    "label": record.get("password_label")} if record else None
        with self.lock:
            if len(self.cache) > 5000:
                self.cache.clear()
            self.cache[token] = (now, identity)
        return identity


def gate_from_environment():
    """The Gate of OMICS_CANVAS_AUTH (the auth state folder), or None when sign-in is not required."""
    folder = os.environ.get("OMICS_CANVAS_AUTH", "").strip()
    return Gate(folder) if folder else None


def wants_html(method, headers):
    return method in ("GET", "HEAD") and "text/html" in (headers.get("Accept") or "")


def login_location(next_path):
    from urllib.parse import quote
    return "/auth/login?next=" + quote(next_path or "/", safe="")
