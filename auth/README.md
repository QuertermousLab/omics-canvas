# Sign-in

Omics Canvas asks everyone to sign in before any page, API or data file answers: with a **THOR account** (the
account's own password, checked by the system as a login would) or an **access password** from the admin page, for
people without an account. "Keep me signed in" lasts a year from the last use, in the same browser. JupyterLab
(`/lab`) keeps its own sign-in; GREAT's programming interfaces (rGREAT, `greatStart.php`) stay open, and never read
the server's files.

| Path | Role |
| --- | --- |
| `server.py` | the sign-in page and API (`/auth/`), the admin page (`/admin/`); 127.0.0.1:8895, `vizhub-auth.service` |
| `sessions.py` | the session store and `Gate`, imported by every backend (hub, Genome Canvas, GREAT) to check the cookie |
| `verify.py` | THOR account passwords: PAM through `pam_check`, or an SSH login to this machine until that is installed |
| `pam_check.py`, `install-pam-helper.sh` | the root-owned PAM helper and its one-time installation |
| `rsa.py`, `web/login.mjs` | the password is RSA-encrypted in the browser before it is sent (plain-HTTP sites) |
| `web/` | `login.html`, `admin.html`, their scripts and styles (`base.css`, `fonts/`: Genome Canvas's look) |
| `tests/` | `python3 -m unittest discover -s tests` (in CI) |

## How it works

- One cookie for the whole site, `omics_canvas_session` (`HttpOnly`, `SameSite=Lax`, `Path=/`), holds a random token;
  the server keeps its SHA-256 in `state/auth/auth.sqlite3` with who signed in, how, from where and when.
- The hub, Genome Canvas and GREAT each check it (`sessions.Gate`, cached 15 s) when `OMICS_CANVAS_AUTH` names the
  state folder (the units set it while `VIZHUB_SIGN_IN=on` in `deploy/site.env`). Without a session a page redirects
  to `/auth/login?next=<the page>` and returns there after signing in; the API and data answer 401. Requests one
  backend makes to another on 127.0.0.1 (no `X-Forwarded-For`, so not through the gateway) are trusted.
- Open without a session: `/auth/*`, the health checks, and GREAT's `/great/public*/cgi-bin/*.php` and `/great/rgreat.R`.
- Sessions with "keep me signed in" end `rememberDays` (365) after their last use, others when the browser closes or
  after `idleHours` (12) without use. A session of an access password ends when the password expires, is disabled or
  deleted.
- Workspaces (Genome Canvas's `genome_canvas_workspace` cookie, `Path=/genome-canvas/`, also used by the hub and
  GREAT): signing in with a THOR account selects that account's Home workspace (`/home/<account>`, `--home-root`);
  an access password, and signing out, clear it, and Genome Canvas keeps an access-password visitor's choice only for
  the browser session, so they choose a workspace on each visit (the home page opens the chooser).
- `/auth/logout?next=<page>` signs out and returns to that page after signing in again (the Mac apps' **Sign Out**).
- Failed sign-ins: 5 within 15 minutes lock that address, and that account, for 15 minutes (settings).

## THOR account passwords

The site runs as an ordinary account, which cannot read other accounts' passwords. Install the PAM helper once:

```bash
sudo auth/install-pam-helper.sh
```

It copies `pam_check.py` to `/usr/local/lib/omics-canvas/pam_check` (root-owned, local disk: files on NFS can be
changed by other users), adds the PAM service `/etc/pam.d/omics-canvas` (`common-auth` + `common-account`, as a login)
and `/etc/sudoers.d/omics-canvas`, which lets the account running the site run only that command as root. Undo with
`sudo rm -r /usr/local/lib/omics-canvas /etc/pam.d/omics-canvas /etc/sudoers.d/omics-canvas`.

Until then (and if sudo is refused) passwords are checked by an SSH login to 127.0.0.1 with the account and password
(SSH checks them with PAM as root; the account then runs `true`). That needs SSH password logins, which THOR allows;
failed attempts appear in `/var/log/auth.log`. The admin page shows which check is in use.

## The admin page

`/admin/` (administrators: `admins` in the settings, by default the account that runs the site):

- **Access passwords**: name, password (typed or generated; shown once, stored as a PBKDF2 hash), valid until,
  note; disable, re-enable, change the expiry, delete. The table shows the last use, uses and people signed in.
- **Signed-in sessions**: who, how, from where, browser, since, last use, until; sign any other session out.
- **Settings**: administrators, keep-me-signed-in days, idle hours, lockout attempts and minutes, the lowest uid that
  may sign in (1000: no system accounts), an optional list of the only accounts allowed.
- **Sign-in log**: the last 300 attempts (kept 180 days).
- **GREAT**: GREAT's request limits and its stored results (`great/README.md`).

## Turning it off

`VIZHUB_SIGN_IN=off` in `deploy/site.env`, then `./vizhubctl configure --yes && ./vizhubctl restart`: the backends no
longer check sessions. A stand-alone Genome Canvas or GREAT, the Mac app's This Mac mode and the tests never ask for it.

## Security notes

- Over plain HTTP the password itself is encrypted in the browser (RSA-2048, a key made when the server starts and a
  single-use nonce per form), but the session cookie is not: someone who can read the network traffic could reuse it.
  Serving the site over HTTPS closes that gap.
- Anyone who can run commands on THOR can reach the backends on 127.0.0.1 directly; that is by design (accounts on
  THOR may read each other's data anyway). The sign-in keeps out people on the network without an account or an
  access password.
