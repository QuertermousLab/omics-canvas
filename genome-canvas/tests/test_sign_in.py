import http.client
import importlib.util
import json
import tempfile
import threading
import unittest
from pathlib import Path

import server
from workspace_store import WorkspaceStore

AUTH = Path(server.APP_DIR).parent / "auth" / "sessions.py"


@unittest.skipUnless(AUTH.is_file(), "needs ../auth (the Omics Canvas repository)")
class SignInTests(unittest.TestCase):
    """With Omics Canvas sign-in on, pages send people to sign in and the API and data answer 401."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        spec = importlib.util.spec_from_file_location("omics_canvas_sessions", str(AUTH))
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.previous = (server.SIGN_IN, server.SIGN_IN_MODULE)
        server.SIGN_IN, server.SIGN_IN_MODULE = self.module.Gate(root / "auth"), self.module
        self.token = server.SIGN_IN.store.create_session("alice", "account", True)
        self.httpd = server.GenomeCanvasHTTPServer(("127.0.0.1", 0), server.GenomeCanvasHandler,
                                                   WorkspaceStore(root / "workspaces.sqlite3"), root)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        server.SIGN_IN, server.SIGN_IN_MODULE = self.previous
        self.temporary.cleanup()

    def request(self, path, headers=None, method="GET"):
        connection = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=10)
        # through the gateway: X-Forwarded-For set (direct calls from this machine are trusted)
        connection.request(method, path, headers=dict({"X-Forwarded-For": "10.0.0.8", "X-Forwarded-Prefix": "/genome-canvas"},
                                                      **(headers or {})))
        response = connection.getresponse()
        response.read()
        connection.close()
        return response.status, response.getheader("Location")

    def test_pages_api_and_data(self):
        self.assertEqual(self.request("/?locus=chr1:1-100", {"Accept": "text/html"}),
                         (302, "/auth/login?next=%2Fgenome-canvas%2F%3Flocus%3Dchr1%3A1-100"))
        self.assertEqual(self.request("/api/workspaces")[0], 401)
        self.assertEqual(self.request("/data/root/x.bam", {"Range": "bytes=0-10"})[0], 401)
        self.assertEqual(self.request("/api/sessions", method="POST")[0], 401)
        self.assertEqual(self.request("/api/health")[0], 200)
        cookie = {"Cookie": "{}={}".format(self.module.COOKIE, self.token)}
        self.assertEqual(self.request("/api/workspaces", cookie)[0], 200)
        self.assertEqual(self.request("/", dict(cookie, Accept="text/html"))[0], 200)
        server.SIGN_IN.store.delete_session(self.token)
        server.SIGN_IN.cache.clear()
        self.assertEqual(self.request("/api/workspaces", cookie)[0], 401)

    def test_access_password_workspaces_last_one_browser_session(self):
        # THOR accounts keep their workspace for a year; people with an access password choose it on each visit
        password_id = server.SIGN_IN.store.add_password("visitor", "a long access password")
        guest = server.SIGN_IN.store.create_session("guest: visitor", "password", True, password_id=password_id)

        def create(token, name):
            connection = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=10)
            connection.request("POST", "/api/workspaces", body=json.dumps({"name": name}), headers={
                "Content-Type": "application/json", "X-Forwarded-For": "10.0.0.8", "X-Forwarded-Prefix": "/genome-canvas",
                "Cookie": "{}={}".format(self.module.COOKIE, token)})
            response = connection.getresponse()
            response.read()
            connection.close()
            return response.status, response.getheader("Set-Cookie")

        status, cookie = create(self.token, "Alice lab")
        self.assertEqual(status, 201)
        self.assertIn("Max-Age=31536000", cookie)
        status, cookie = create(guest, "Visitor")
        self.assertEqual(status, 201)
        self.assertTrue(cookie.startswith("genome_canvas_workspace="))
        self.assertNotIn("Max-Age", cookie)


if __name__ == "__main__":
    unittest.main()
