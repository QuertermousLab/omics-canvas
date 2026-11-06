"""Checking a THOR account's password, without running the site as root.

1. PAM, through the root-owned helper /usr/local/lib/omics-canvas/pam_check (install-pam-helper.sh; the sudoers rule
   allows only that command), the same check as console and SSH logins.
2. Until it is installed: a login to this machine's SSH server (which checks the password with PAM as root), with the
   password handed to ssh through SSH_ASKPASS. The account then runs `true`; failed attempts appear in auth.log.
"""

import os
import pwd
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HELPER = "/usr/local/lib/omics-canvas/pam_check"
ACCOUNT = re.compile(r"[a-z_][a-z0-9_.-]{0,31}")


def method():
    """How passwords are checked now: "pam" (the helper is installed and sudo allows it) or "ssh"."""
    if not os.path.isfile(HELPER):
        return "ssh"
    result = subprocess.run(["sudo", "-n", "-l", HELPER, ""], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return "pam" if result.returncode == 0 else "ssh"


def account_allowed(user, settings):
    """None if this account may sign in, else the reason (shown only in the log)."""
    if not ACCOUNT.fullmatch(user or ""):
        return "not an account name"
    try:
        entry = pwd.getpwnam(user)
    except KeyError:
        return "no such account"
    if entry.pw_uid < settings["minUid"] or entry.pw_uid == 65534:
        return "a system account"
    if settings["allowedAccounts"] and user not in settings["allowedAccounts"]:
        return "not in the allowed accounts"
    return None


def check_password(user, password, state_dir, how=None):
    """True when `password` is the account's password; False otherwise (errors count as False)."""
    how = how or method()
    if how == "pam":
        import json
        try:
            result = subprocess.run(["sudo", "-n", HELPER, ""], input=json.dumps({"user": user, "password": password}),
                                    universal_newlines=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
        except subprocess.TimeoutExpired:
            return False
        return result.returncode == 0
    return _check_with_ssh(user, password, Path(state_dir))


def _check_with_ssh(user, password, state_dir):
    known_hosts = state_dir / "ssh_known_hosts"
    with tempfile.TemporaryDirectory(prefix="omics-auth-") as folder:
        askpass = Path(folder) / "askpass"
        askpass.write_text("#!/bin/sh\nprintf '%s\\n' \"$OMICS_AUTH_SECRET\"\n")
        askpass.chmod(0o700)
        environment = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": folder, "SSH_ASKPASS": str(askpass),
                       "SSH_ASKPASS_REQUIRE": "force", "DISPLAY": ":0", "OMICS_AUTH_SECRET": password}
        command = ["ssh", "-T", "-o", "BatchMode=no", "-o", "PubkeyAuthentication=no", "-o", "GSSAPIAuthentication=no",
                   "-o", "PreferredAuthentications=password,keyboard-interactive", "-o", "NumberOfPasswordPrompts=1",
                   "-o", "StrictHostKeyChecking=accept-new", "-o", "UserKnownHostsFile=" + str(known_hosts),
                   "-o", "ConnectTimeout=10", "-o", "LogLevel=ERROR", "-l", user, "127.0.0.1", "true"]
        try:
            result = subprocess.run(command, env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.PIPE, timeout=30, start_new_session=True)
        except subprocess.TimeoutExpired:
            return False
    # 255: ssh itself failed (the password was refused, or no connection); anything else ran as the account
    if result.returncode == 255:
        if b"Permission denied" not in result.stderr:
            print("auth: ssh check failed: {}".format(result.stderr.decode("utf-8", "replace").strip()[:200]), file=sys.stderr)
        return False
    return True
