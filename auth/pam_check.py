#!/usr/bin/python3 -I
"""Check one THOR account's password with PAM (service "omics-canvas": the same check as console and SSH logins).

Installed root-owned as /usr/local/lib/omics-canvas/pam_check by install-pam-helper.sh, and run by the auth server
as `sudo -n /usr/local/lib/omics-canvas/pam_check` (the only command its sudoers rule allows). Reads
{"user": ..., "password": ...} as JSON on stdin. Exit 0: the password is right and the account may log in;
1: it is not; 2: usage or PAM error. Prints nothing else. Standard library only (ctypes and libpam).
"""

import ctypes
import ctypes.util
import json
import pwd
import re
import sys
import time

PAM_SERVICE = b"omics-canvas"
PAM_PROMPT_ECHO_OFF, PAM_PROMPT_ECHO_ON = 1, 2
PAM_SUCCESS = 0


class PamHandle(ctypes.Structure):
    _fields_ = [("handle", ctypes.c_void_p)]


class PamMessage(ctypes.Structure):
    _fields_ = [("msg_style", ctypes.c_int), ("msg", ctypes.c_char_p)]


class PamResponse(ctypes.Structure):
    _fields_ = [("resp", ctypes.c_void_p), ("resp_retcode", ctypes.c_int)]


CONVERSATION = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.POINTER(PamMessage)),
                                ctypes.POINTER(ctypes.POINTER(PamResponse)), ctypes.c_void_p)


class PamConv(ctypes.Structure):
    _fields_ = [("conv", CONVERSATION), ("appdata_ptr", ctypes.c_void_p)]


def authenticate(user, password):
    libc = ctypes.CDLL(ctypes.util.find_library("c"))
    libpam = ctypes.CDLL(ctypes.util.find_library("pam"))
    libc.calloc.restype = ctypes.c_void_p
    libc.calloc.argtypes = [ctypes.c_size_t, ctypes.c_size_t]
    libc.strdup.restype = ctypes.c_void_p
    libc.strdup.argtypes = [ctypes.c_char_p]
    libpam.pam_start.restype = ctypes.c_int
    libpam.pam_start.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.POINTER(PamConv), ctypes.POINTER(PamHandle)]
    libpam.pam_authenticate.argtypes = [PamHandle, ctypes.c_int]
    libpam.pam_acct_mgmt.argtypes = [PamHandle, ctypes.c_int]
    libpam.pam_end.argtypes = [PamHandle, ctypes.c_int]
    secret = password.encode("utf-8")
    name = user.encode("utf-8")

    @CONVERSATION
    def conversation(count, messages, responses, _):
        # PAM frees the answers: allocate them with libc
        answers = libc.calloc(count, ctypes.sizeof(PamResponse))
        responses[0] = ctypes.cast(answers, ctypes.POINTER(PamResponse))
        for i in range(count):
            style = messages[i].contents.msg_style
            if style == PAM_PROMPT_ECHO_OFF:
                responses[0][i].resp = libc.strdup(secret)
            elif style == PAM_PROMPT_ECHO_ON:
                responses[0][i].resp = libc.strdup(name)
            responses[0][i].resp_retcode = 0
        return PAM_SUCCESS

    handle = PamHandle()
    conv = PamConv(conversation, 0)
    if libpam.pam_start(PAM_SERVICE, name, ctypes.byref(conv), ctypes.byref(handle)) != PAM_SUCCESS:
        return 2
    try:
        result = libpam.pam_authenticate(handle, 0)
        if result == PAM_SUCCESS:
            result = libpam.pam_acct_mgmt(handle, 0)  # expired or locked accounts
        return 0 if result == PAM_SUCCESS else 1
    finally:
        libpam.pam_end(handle, 0)


def main():
    try:
        request = json.loads(sys.stdin.read(65536))
        user, password = str(request["user"]), str(request["password"])
    except (ValueError, KeyError, TypeError):
        return 2
    if not re.fullmatch(r"[a-z_][a-z0-9_.-]{0,31}", user) or not password or len(password) > 1024:
        return 2
    try:
        pwd.getpwnam(user)
    except KeyError:
        time.sleep(1)
        return 1
    code = authenticate(user, password)
    if code:
        time.sleep(1)  # slows guessing; the auth server also locks addresses and accounts out
    return code


if __name__ == "__main__":
    sys.exit(main())
