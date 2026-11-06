#!/bin/sh
# Install the PAM password check for Omics Canvas sign-in (run once with sudo from the account that runs the site):
#   sudo auth/install-pam-helper.sh
# It installs, all owned by root:
#   /usr/local/lib/omics-canvas/pam_check   auth/pam_check.py (checks one account's password; nothing else)
#   /etc/pam.d/omics-canvas                 the PAM service: the same checks as console and SSH logins
#   /etc/sudoers.d/omics-canvas             lets that account run only pam_check as root, without a password
# The helper is copied to a local root-owned folder because files on NFS can be changed by other users.
# Undo: sudo rm -r /usr/local/lib/omics-canvas /etc/pam.d/omics-canvas /etc/sudoers.d/omics-canvas
set -eu
if [ "$(id -u)" != 0 ]; then echo "Run it with sudo: sudo $0" >&2; exit 1; fi
account="${SUDO_USER:-}"
if [ -z "$account" ] || [ "$account" = root ]; then echo "Run it with sudo from the account that runs Omics Canvas" >&2; exit 1; fi
here="$(cd "$(dirname "$0")" && pwd)"

install -d -o root -g root -m 755 /usr/local/lib/omics-canvas
install -o root -g root -m 755 "$here/pam_check.py" /usr/local/lib/omics-canvas/pam_check

cat > /etc/pam.d/omics-canvas <<'EOF'
# Omics Canvas sign-in (/usr/local/lib/omics-canvas/pam_check): the same password and account checks as a login
@include common-auth
@include common-account
EOF
chown root:root /etc/pam.d/omics-canvas
chmod 644 /etc/pam.d/omics-canvas

rule="$(mktemp)"
printf '# Omics Canvas sign-in: %s may run only the PAM password check as root, with no arguments\n%s ALL=(root) NOPASSWD: /usr/local/lib/omics-canvas/pam_check ""\n' "$account" "$account" > "$rule"
visudo -cf "$rule" >/dev/null
install -o root -g root -m 440 "$rule" /etc/sudoers.d/omics-canvas
rm -f "$rule"
echo "Installed: Omics Canvas now checks THOR passwords with PAM (for $account's auth server)."
