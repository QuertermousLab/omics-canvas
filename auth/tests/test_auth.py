import base64
import http.client
import json
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path

import rsa
import server
import sessions
import verify

APP = Path(__file__).resolve().parent.parent
KEY = rsa.KeyPair(1024)  # smaller and faster for tests; the server uses 2048


class RSATests(unittest.TestCase):
    def test_round_trip_and_tampering(self):
        cipher = KEY.encrypt("{\"p\": \"pässwörd\"}".encode("utf-8"))
        self.assertEqual(KEY.decrypt(cipher).decode("utf-8"), "{\"p\": \"pässwörd\"}")
        with self.assertRaises(ValueError):
            KEY.decrypt(cipher[:-1])
        with self.assertRaises(ValueError):
            KEY.decrypt(bytes(len(cipher)))

    @unittest.skipUnless(shutil.which("node"), "needs node")
    def test_the_pages_javascript_encrypts_for_the_server(self):
        script = ("import('{}').then(({{ encrypt }}) => process.stdout.write(encrypt(process.argv[1], JSON.parse(process.argv[2]))))"
                  .format((APP / "web" / "login.mjs").as_uri()))
        # login.mjs touches the page at import time: give it a minimal document
        shim = ("globalThis.document = {querySelector: () => ({addEventListener() {}, hidden: false, focus() {}, value: '', "
                "setAttribute() {}, required: false}), querySelectorAll: () => []}; globalThis.localStorage = {getItem: () => null};"
                "globalThis.location = {search: ''};")
        message = "{\"u\": \"alice\", \"p\": \"päss wörd ✓\", \"nonce\": \"n1\"}"
        result = subprocess.run(["node", "--input-type=module", "-e", shim + script, message, json.dumps(KEY.public())],
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(KEY.decrypt(base64.b64decode(result.stdout)).decode("utf-8"), message)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.store = sessions.Store(self.temporary.name)
        self.store.update_settings({"admins": ["alice"]})

    def tearDown(self):
        self.temporary.cleanup()

    def test_sessions_remembered_or_idle(self):
        kept = self.store.create_session("alice", "account", True)
        brief = self.store.create_session("bob", "account", False)
        self.assertTrue(self.store.session(kept)["admin"])
        self.assertFalse(self.store.session(brief)["admin"])
        with self.store.connect() as db:  # 13 hours idle: the brief one has ended, the remembered one has not
            db.execute("UPDATE sessions SET last_seen = last_seen - 13 * 3600")
        self.assertIsNone(self.store.session(brief))
        self.assertIsNotNone(self.store.session(kept))
        with self.store.connect() as db:
            db.execute("UPDATE sessions SET last_seen = last_seen - 366 * 86400")
        self.assertIsNone(self.store.session(kept))
        self.assertIsNone(self.store.session("x" * 40))

    def test_access_passwords_end_their_sessions(self):
        password_id = self.store.add_password("visitor", "correct horse", time.time() + 86400)
        with self.assertRaises(ValueError):
            self.store.add_password("again", "correct horse")
        with self.assertRaises(ValueError):
            self.store.add_password("short", "1234567")
        self.assertIsNone(self.store.match_password("wrong horse"))
        match = self.store.match_password("correct horse")
        token = self.store.create_session("guest: visitor", "password", True, password_id=match["id"])
        self.assertFalse(self.store.session(token)["admin"])
        self.store.update_password(password_id, disabled=True)
        self.assertIsNone(self.store.session(token))
        self.assertIsNone(self.store.match_password("correct horse"))
        self.store.update_password(password_id, disabled=False, expires=time.time() - 1)
        self.assertIsNone(self.store.match_password("correct horse"))
        self.assertEqual(self.store.passwords()[0]["state"], "expired")
        self.assertEqual(self.store.delete_password(password_id), 1)

    def test_lockout(self):
        for _ in range(5):
            self.store.log("10.0.0.5", "alice", "account", False)
        self.assertGreater(self.store.locked("10.0.0.5"), 0)
        self.assertGreater(self.store.locked("10.0.0.9", "alice"), 0)  # the account too, from anywhere
        self.assertEqual(self.store.locked("10.0.0.9", "bob"), 0)
        self.store.log("10.0.0.5", "alice", "account", True)  # a success resets the count
        self.assertEqual(self.store.locked("10.0.0.5", "alice"), 0)

    def test_settings_are_checked(self):
        with self.assertRaises(ValueError):
            self.store.update_settings({"rememberDays": 0})
        with self.assertRaises(ValueError):
            self.store.update_settings({"admins": ["Not An Account!"]})
        self.assertEqual(self.store.update_settings({"lockoutAttempts": 3})["lockoutAttempts"], 3)

    def test_gate(self):
        gate = sessions.Gate(self.temporary.name)
        token = self.store.create_session("alice", "account", True)
        cookie = {"Cookie": "other=1; {}={}".format(sessions.COOKIE, token), "X-Forwarded-For": "10.0.0.1"}
        self.assertEqual(gate.identity(cookie, "127.0.0.1")["user"], "alice")
        self.assertIsNone(gate.identity({"X-Forwarded-For": "10.0.0.1"}, "127.0.0.1"))  # through the gateway
        self.assertEqual(gate.identity({}, "127.0.0.1")["method"], "internal")  # a backend calling another
        self.assertIsNone(gate.identity({}, "10.0.0.7"))
        self.assertEqual(sessions.login_location("/genome-canvas/?locus=chr1:1-2"),
                         "/auth/login?next=%2Fgenome-canvas%2F%3Flocus%3Dchr1%3A1-2")


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        store = sessions.Store(self.temporary.name)
        store.update_settings({"admins": ["alice"]})
        self.previous = (verify.check_password, verify.account_allowed, rsa.KeyPair)
        verify.check_password = lambda user, password, state_dir, how=None: (user, password) in (("alice", "a-secret"), ("bob", "b-secret"))
        verify.account_allowed = lambda user, settings: None if user in ("alice", "bob") else "no such account"
        rsa.KeyPair = lambda: KEY
        self.httpd = server.AuthServer(("127.0.0.1", 0), store)
        self.httpd.verifier = lambda: "pam"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        verify.check_password, verify.account_allowed, rsa.KeyPair = self.previous
        self.temporary.cleanup()

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=30)
        connection.request(method, path, body=json.dumps(body) if isinstance(body, dict) else body,
                           headers=dict({"Content-Type": "application/json"}, **(headers or {})))
        response = connection.getresponse()
        data = response.read().decode("utf-8")
        connection.close()
        try:
            payload = json.loads(data)
        except ValueError:
            payload = data
        return response.status, response.getheader("Set-Cookie") or "", payload, response

    def sign_in(self, user, password, mode="account", remember=True, next_path="/"):
        _, _, key, _ = self.request("GET", "/auth/api/key")
        cipher = base64.b64encode(KEY.encrypt(json.dumps({"u": user, "p": password, "nonce": key["nonce"]}).encode())).decode()
        return self.request("POST", "/auth/api/login", {"mode": mode, "cipher": cipher, "remember": remember, "next": next_path},
                            {"X-Forwarded-For": "10.1.1.1"})

    def test_sign_in_with_an_account(self):
        status, cookie, payload, _ = self.sign_in("alice", "a-secret", next_path="/genome-canvas/?locus=x")
        self.assertEqual((status, payload["next"]), (200, "/genome-canvas/?locus=x"))
        self.assertIn("Max-Age=31536000", cookie)
        self.assertIn("HttpOnly", cookie)
        jar = {"Cookie": cookie.split(";")[0]}
        status, _, me, _ = self.request("GET", "/auth/api/me", headers=jar)
        self.assertEqual((status, me["user"], me["admin"]), (200, "alice", True))
        status, cookie, _, _ = self.sign_in("bob", "b-secret", remember=False, next_path="//evil.example/")
        self.assertNotIn("Max-Age", cookie)
        status, _, _, _ = self.request("GET", "/admin/api/overview", headers={"Cookie": cookie.split(";")[0]})
        self.assertEqual(status, 403)  # bob is not an administrator
        status, _, _, _ = self.request("POST", "/auth/api/logout", headers=jar)
        self.assertEqual(self.request("GET", "/auth/api/me", headers=jar)[0], 401)

    def test_wrong_passwords_lock_out_and_old_forms_fail(self):
        for _ in range(5):
            status, _, _, _ = self.sign_in("alice", "wrong")
            self.assertEqual(status, 401)
        status, _, payload, _ = self.sign_in("alice", "a-secret")
        self.assertEqual(status, 429)
        self.assertIn("Too many", payload["error"])
        _, _, key, _ = self.request("GET", "/auth/api/key")
        cipher = base64.b64encode(KEY.encrypt(json.dumps({"u": "bob", "p": "b-secret", "nonce": "made-up"}).encode())).decode()
        status, _, _, _ = self.request("POST", "/auth/api/login", {"cipher": cipher})
        self.assertEqual(status, 400)  # a nonce the server did not issue (or one used before)

    def test_admin_manages_access_passwords_and_sessions(self):
        _, cookie, _, _ = self.sign_in("alice", "a-secret")
        jar = {"Cookie": cookie.split(";")[0]}
        status, _, created, _ = self.request("POST", "/admin/api/passwords", {"label": "visitor", "expires": "2099-12-31"}, jar)
        self.assertEqual(status, 200)
        status, guest_cookie, _, _ = self.sign_in("", created["password"], mode="password")
        self.assertEqual(status, 200)
        guest = {"Cookie": guest_cookie.split(";")[0]}
        self.assertEqual(self.request("GET", "/auth/api/me", headers=guest)[2]["user"], "guest: visitor")
        _, _, overview, _ = self.request("GET", "/admin/api/overview", headers=jar)
        self.assertEqual(overview["passwords"][0]["sessions"], 1)
        guest_session = next(s for s in overview["sessions"] if s["method"] == "password")
        self.assertEqual(self.request("DELETE", "/admin/api/sessions/" + guest_session["id"], headers=jar)[0], 200)
        self.assertEqual(self.request("GET", "/auth/api/me", headers=guest)[0], 401)
        self.assertEqual(self.request("DELETE", "/admin/api/sessions/" + overview["me"]["session"], headers=jar)[0], 400)
        status, _, _, _ = self.request("POST", "/admin/api/settings", {"admins": ["bob"]}, jar)
        self.assertEqual(status, 400)  # not without yourself
        status, _, _, _ = self.request("POST", "/admin/api/passwords", {"label": "x"}, dict(jar, Origin="http://evil.example"))
        self.assertEqual(status, 403)

    def test_pages(self):
        status, _, page, _ = self.request("GET", "/auth/login?next=%2Fgreat%2F")
        self.assertEqual(status, 200)
        self.assertIn("Sign in", page)
        status, _, _, response = self.request("GET", "/admin/", headers={"Accept": "text/html"})
        self.assertEqual((status, response.getheader("Location")), (302, "/auth/login?next=%2Fadmin%2F"))
        self.assertEqual(server.safe_next("/auth/login"), "/")
        self.assertEqual(server.safe_next("https://evil.example"), "/")


class VerifyTests(unittest.TestCase):
    def test_account_rules(self):
        settings = dict(sessions.DEFAULT_SETTINGS)
        self.assertEqual(verify.account_allowed("root", settings), "a system account")
        self.assertEqual(verify.account_allowed("Robert'); --", settings), "not an account name")
        self.assertEqual(verify.account_allowed("no-such-user-xyz", settings), "no such account")


if __name__ == "__main__":
    unittest.main()
