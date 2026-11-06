# Sign-in — guide for agents and new sessions

User documentation: [README.md](README.md). The repository rules in [../AGENTS.md](../AGENTS.md) apply.

- THOR: `auth/server.py` on 127.0.0.1:8895 (`VIZHUB_AUTH_PORT`), unit `vizhub-auth.service` (`../vizhubctl restart auth`);
  the gateway routes `/auth/` and `/admin/` there. State: `../state/auth/auth.sqlite3` (sessions, access passwords,
  log, settings; not in git). Restarting the auth server makes a new RSA key: open sign-in forms reload it on submit.
- The backends (hub, Genome Canvas, GREAT) load `sessions.py` by file path (`importlib`, module name
  `omics_canvas_sessions`) when `OMICS_CANVAS_AUTH` is set; keep `sessions.py` standard library only and Python 3.8+.
  A new backend or route must check the session the same way, and only health checks and GREAT's open interfaces
  may stay open.
- Never turn sign-in off or change who can sign in without the user's word. The PAM helper is installed with sudo by
  the user (`sudo auth/install-pam-helper.sh`); never copy anything from NFS to a root-owned path yourself.
- To test as someone signed in, create a session in the store (`sessions.Store(...).create_session(...)`) or an access
  password, and delete them (and their log rows) afterwards. Never ask for or store a THOR password.
- The site is HTTP for now: passwords are RSA-encrypted in the page (`web/login.mjs`, BigInt, no Web Crypto on HTTP);
  HTTPS (a Stanford ACME certificate for THOR-cvmed.stanford.edu) is the planned next step.
