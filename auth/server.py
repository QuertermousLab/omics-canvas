#!/usr/bin/env python3
"""Sign-in server of Omics Canvas: the sign-in page (/auth/) and the admin page (/admin/).

    python3 server.py --host 127.0.0.1 --port 8895 --state ../state/auth

Every page of the site (home, viewers, Genome Canvas, GREAT) sends people here until they sign in, with a THOR
account (its password checked with PAM, verify.py) or an access password set in the admin page; the backends check
the session cookie with sessions.Gate. The admin page manages access passwords, sessions, the sign-in log, these
settings, and GREAT's settings and stored results (through GREAT's own admin API).

Routes: /auth/login (page), /auth/api/key, /auth/api/login, /auth/api/logout, /auth/logout, /auth/api/me,
/auth/api/health, /admin/ (page), /admin/api/... Standard library only (Python 3.8+).
"""

import argparse
import base64
import hashlib
import json
import re
import secrets
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import rsa
import sessions
import verify

APP_DIR = Path(__file__).resolve().parent
WEB_DIR = APP_DIR / "web"
STATIC = {
    "login.mjs": "text/javascript; charset=utf-8", "admin.mjs": "text/javascript; charset=utf-8",
    "auth.css": "text/css; charset=utf-8", "base.css": "text/css; charset=utf-8", "favicon.svg": "image/svg+xml",
}
NONCE_SECONDS = 300
WORKSPACE_COOKIE = "genome_canvas_workspace"  # Genome Canvas's selected workspace (server.py), shared by hub and GREAT


def home_workspace_id(home):
    """Genome Canvas's id of a Home workspace (server.home_workspace_id)."""
    return "home-{}".format(hashlib.sha256(str(home).encode("utf-8")).hexdigest()[:18])


def safe_next(value):
    """Only paths on this site (never another host) are followed after signing in."""
    value = (value or "").strip()
    if not value.startswith("/") or value.startswith("//") or "\\" in value or value.startswith("/auth/"):
        return "/"
    return value


def parse_date(value):
    """'2026-12-31' (end of that day, local time) or '' -> epoch seconds or None."""
    if value in (None, "", "never"):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").replace(hour=23, minute=59, second=59).timestamp()
    except ValueError:
        raise ValueError("Give the expiry as YYYY-MM-DD")


class AuthHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "OmicsCanvasAuth/1.0"

    def log_message(self, fmt, *args):
        print("{} - {}".format(self.address_string(), fmt % args))

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    @property
    def store(self):
        return self.server.store

    # ---------- helpers

    def client(self):
        address = self.client_address[0]
        forwarded = self.headers.get("X-Forwarded-For", "")
        if forwarded and address in ("127.0.0.1", "::1"):
            address = forwarded.split(",")[-1].strip() or address
        return address

    def send_body(self, body, content_type, status=200, headers=None):
        body = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for name, value in (headers or {}).items():
            for item in value if isinstance(value, list) else [value]:  # several Set-Cookie headers
                self.send_header(name, item)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def workspace_cookie(self, user=None):
        """A THOR account opens in its Home workspace; access-password sign-ins and sign-outs clear the workspace,
        so Genome Canvas asks which one to use."""
        path = self.server.canvas_path
        home = self.server.home_root / user if user else None
        if home is not None and home.is_dir():
            return "{}={}; Path={}; HttpOnly; SameSite=Lax; Max-Age=31536000".format(
                WORKSPACE_COOKIE, home_workspace_id(home), path)
        return "{}=; Path={}; HttpOnly; SameSite=Lax; Max-Age=0".format(WORKSPACE_COOKIE, path)

    def send_json(self, payload, status=200, headers=None):
        self.send_body(json.dumps(payload, separators=(",", ":")), "application/json; charset=utf-8", status, headers)

    def send_redirect(self, location, headers=None):
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        for name, value in (headers or {}).items():
            for item in value if isinstance(value, list) else [value]:
                self.send_header(name, item)
        self.end_headers()

    def read_json(self, maximum=64 * 1024):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > maximum:
            raise ValueError("Send a JSON object")
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Send a JSON object")
        return payload

    def same_origin(self):
        origin = self.headers.get("Origin")
        return not origin or urlparse(origin).netloc == self.headers.get("Host", "")

    def session(self):
        token = sessions.cookie_token(self.headers.get("Cookie"))
        return (self.store.session(token, touch=True), token) if token else (None, None)

    # ---------- routing

    def do_HEAD(self):
        self.route("GET")

    def do_GET(self):
        self.route("GET")

    def do_POST(self):
        self.route("POST")

    def do_DELETE(self):
        self.route("DELETE")

    def route(self, method):
        parsed = urlparse(self.path)
        path, query = parsed.path, parse_qs(parsed.query)
        try:
            if method in ("POST", "DELETE") and not self.same_origin():
                raise PermissionError("Cross-site requests are not allowed")
            if path in ("/auth", "/auth/", "/auth/login") and method == "GET":
                record, _ = self.session()
                target = safe_next(query.get("next", [""])[0])
                if record:
                    self.send_redirect(target)
                else:
                    self.send_body((WEB_DIR / "login.html").read_text(encoding="utf-8"), "text/html; charset=utf-8")
            elif path == "/auth/api/health":
                self.send_json({"ok": True})
            elif path == "/auth/api/key" and method == "GET":
                self.send_json(dict(self.server.key.public(), nonce=self.server.new_nonce()))
            elif path == "/auth/api/login" and method == "POST":
                self.handle_login()
            elif path in ("/auth/api/logout", "/auth/logout"):
                _, token = self.session()
                if token:
                    self.store.delete_session(token)
                headers = {"Set-Cookie": [sessions.clear_cookie_header(), self.workspace_cookie()]}
                if path == "/auth/logout":  # ?next=: where to go after signing in again (the Mac apps' Sign Out)
                    target = safe_next(query.get("next", [""])[0])
                    self.send_redirect(sessions.login_location(target) if target != "/" else "/auth/login", headers)
                else:
                    self.send_json({"ok": True}, headers=headers)
            elif path == "/auth/api/me" and method == "GET":
                record, _ = self.session()
                if not record:
                    self.send_json({"error": "Not signed in"}, 401)
                else:
                    self.send_json({"user": record["user"], "method": record["method"], "admin": record["admin"],
                                    "label": record.get("password_label"), "expires": record["expires"],
                                    "remember": bool(record["remember"])})
            elif method == "GET" and path.startswith("/auth/") and path[len("/auth/"):] in STATIC:
                name = path[len("/auth/"):]
                self.send_body((WEB_DIR / name).read_bytes(), STATIC[name])
            elif method == "GET" and re.fullmatch(r"/auth/fonts/[a-z0-9-]+\.woff2", path):
                self.send_body((WEB_DIR / "fonts" / Path(path).name).read_bytes(), "font/woff2")
            elif path == "/admin" or path.startswith("/admin/"):
                self.handle_admin(method, path, query)
            else:
                self.send_json({"error": "Not found"}, 404)
        except PermissionError as exc:
            self.send_json({"error": str(exc)}, 403)
        except (ValueError, KeyError) as exc:
            self.send_json({"error": str(exc)}, 400)

    # ---------- signing in

    def handle_login(self):
        payload = self.read_json()
        try:
            plain = self.server.key.decrypt(base64.b64decode(payload.get("cipher", "")))
            secret = json.loads(plain.decode("utf-8"))
        except (ValueError, TypeError):
            raise ValueError("The sign-in form is out of date: reload the page")
        if not self.server.use_nonce(secret.get("nonce", "")):
            raise ValueError("The sign-in form is out of date: reload the page")
        mode = "password" if payload.get("mode") == "password" else "account"
        remember = bool(payload.get("remember"))
        password = str(secret.get("p", ""))
        ip = self.client()
        agent = self.headers.get("User-Agent", "")
        settings = self.store.settings()
        if mode == "account":
            user = str(secret.get("u", "")).strip().lower()
            wait = self.store.locked(ip, user)
            if wait:
                return self.send_json({"error": "Too many failed sign-ins: try again in {} minutes".format(
                    (wait + 59) // 60)}, 429, {"Retry-After": str(wait)})
            reason = verify.account_allowed(user, settings)
            ok = not reason and bool(password) and verify.check_password(user, password, self.store.state_dir,
                                                                          self.server.verifier())
            if not ok:
                self.store.log(ip, user, "account", False, reason or "wrong password")
                time.sleep(1)
                return self.send_json({"error": "The account name or password is not right"}, 401)
            token = self.store.create_session(user, "account", remember, ip, agent)
            self.store.log(ip, user, "account", True)
            workspace = self.workspace_cookie(user)
        else:
            wait = self.store.locked(ip)
            if wait:
                return self.send_json({"error": "Too many failed sign-ins: try again in {} minutes".format(
                    (wait + 59) // 60)}, 429, {"Retry-After": str(wait)})
            match = self.store.match_password(password) if password else None
            if not match:
                self.store.log(ip, "", "password", False, "wrong access password")
                time.sleep(1)
                return self.send_json({"error": "This access password is not right, or no longer valid"}, 401)
            token = self.store.create_session("guest: " + match["label"], "password", remember, ip, agent, match["id"])
            self.store.log(ip, "guest: " + match["label"], "password", True)
            workspace = self.workspace_cookie()
        self.send_json({"ok": True, "next": safe_next(payload.get("next"))},
                       headers={"Set-Cookie": [sessions.set_cookie_header(token, remember, settings["rememberDays"]), workspace]})

    # ---------- the admin page

    def handle_admin(self, method, path, query):
        record, token = self.session()
        if not record:
            if path.startswith("/admin/api/"):
                return self.send_json({"error": "Not signed in"}, 401)
            return self.send_redirect(sessions.login_location(self.path))
        if not record["admin"]:
            if path.startswith("/admin/api/"):
                raise PermissionError("Only administrators can use the admin page")
            return self.send_body("<!doctype html><meta charset=utf-8><title>Omics Canvas</title><body style='font:14px "
                                  "system-ui;margin:40px'><p>Only administrators can open the admin page. "
                                  "<a href='/'>Back to Omics Canvas</a></p>", "text/html; charset=utf-8", 403)
        if path in ("/admin", "/admin/") and method == "GET":
            return self.send_body((WEB_DIR / "admin.html").read_text(encoding="utf-8"), "text/html; charset=utf-8")
        route = path[len("/admin/api/"):] if path.startswith("/admin/api/") else None
        own = sessions.token_hash(token)[:16]
        if route == "overview" and method == "GET":
            self.send_json({"me": {"user": record["user"], "session": own}, "settings": self.store.settings(),
                            "defaults": sessions.DEFAULT_SETTINGS, "verifier": self.server.verifier(),
                            "passwords": self.store.passwords(), "sessions": self.store.sessions(),
                            "log": self.store.recent_log()})
        elif route == "settings" and method == "POST":
            changes = self.read_json()
            if "admins" in changes and record["user"] not in (changes["admins"] or [record["user"]]):
                raise ValueError("Keep your own account in the administrators, or you lock yourself out")
            self.send_json({"settings": self.store.update_settings(changes)})
        elif route == "passwords" and method == "POST":
            payload = self.read_json()
            password = payload.get("password") or "-".join(secrets.token_hex(2) for _ in range(4))
            password_id = self.store.add_password(payload.get("label"), password, parse_date(payload.get("expires")),
                                                  payload.get("note", ""))
            self.send_json({"id": password_id, "password": password})
        elif route and route.startswith("passwords/"):
            password_id = route.split("/", 1)[1]
            if method == "DELETE":
                self.send_json({"deleted": self.store.delete_password(password_id)})
            elif method == "POST":
                payload = self.read_json()
                self.store.update_password(password_id, payload.get("disabled"),
                                           parse_date(payload["expires"]) if "expires" in payload else "keep",
                                           payload.get("label"), payload.get("note"))
                self.send_json({"ok": True})
            else:
                raise KeyError("Not found")
        elif route and route.startswith("sessions/") and method == "DELETE":
            short_id = route.split("/", 1)[1]
            if short_id == own:
                raise ValueError("That is your own session: sign out instead")
            self.send_json({"deleted": self.store.delete_session_by_id(short_id)})
        else:
            self.send_json({"error": "Not found"}, 404)


class AuthServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, store, canvas_path="/genome-canvas/", home_root="/home"):
        self.store = store
        self.canvas_path = "/" + canvas_path.strip("/") + "/" if canvas_path.strip("/") else "/"
        self.home_root = Path(home_root)
        self.key = rsa.KeyPair()
        self.nonces = {}
        self.nonce_lock = threading.Lock()
        self._verifier = (0, None)
        super().__init__(address, AuthHandler)

    def new_nonce(self):
        nonce = secrets.token_urlsafe(18)
        now = time.time()
        with self.nonce_lock:
            self.nonces = {k: t for k, t in self.nonces.items() if now - t < NONCE_SECONDS}
            self.nonces[nonce] = now
        return nonce

    def use_nonce(self, nonce):
        with self.nonce_lock:
            issued = self.nonces.pop(nonce, None)
        return issued is not None and time.time() - issued < NONCE_SECONDS

    def verifier(self):
        """How account passwords are checked ("pam" or "ssh"), looked up at most once a minute."""
        stamp, how = self._verifier
        if time.time() - stamp > 60:
            how = verify.method()
            self._verifier = (time.time(), how)
        return how

    def handle_error(self, request, client_address):
        if isinstance(sys.exc_info()[1], (BrokenPipeError, ConnectionResetError)):
            return
        super().handle_error(request, client_address)


def main():
    ap = argparse.ArgumentParser(description="Sign-in server of Omics Canvas")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8895)
    ap.add_argument("--state", default=str(APP_DIR.parent / "state" / "auth"), help="sessions and settings (default ../state/auth)")
    ap.add_argument("--canvas-path", default="/genome-canvas/", help="where Genome Canvas is published (its workspace cookie)")
    ap.add_argument("--home-root", default="/home", help="Genome Canvas's homeRoot: THOR accounts open in their Home workspace")
    a = ap.parse_args()
    store = sessions.Store(a.state)
    server = AuthServer((a.host, a.port), store, a.canvas_path, a.home_root)
    print("Omics Canvas sign-in on http://{}:{}/auth/ (state {}, passwords checked with {})".format(
        a.host, a.port, a.state, server.verifier()), flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
