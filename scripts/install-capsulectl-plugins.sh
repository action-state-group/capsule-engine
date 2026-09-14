#!/bin/sh
# Track A installer: expose the company-side (Python) capsulectl-* subcommands as
# capsulectl plugins on a trusted plugin root, without touching the core CLI.
#
#   uv tool install  -> installs `capsule-engine` and each `capsulectl-*` entry
#                        point into an isolated tool environment on PATH
#   launcher          -> a real (non-symlink) exec wrapper on the trusted root
#                        ~/.local/lib/capsulectl/plugins that capsulectl's
#                        trusted-path check will accept
#
# Re-runnable. Requires `uv` on PATH; run from the capsule-engine checkout. The
# install FAILS LOUDLY (nonzero exit) if the resolved binary is untrusted, a
# hardening step cannot be applied, or capsulectl does not end up listing the
# plugin -- it never prints success over a config capsulectl will refuse.
set -eu
umask 022

here=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)

echo "installing capsule-engine as an isolated tool (uv tool install)…"
uv tool install --force "$here"

guard_bin=$(command -v capsulectl-guard || true)
if [ -z "$guard_bin" ]; then
  echo "error: capsulectl-guard is not on PATH after install (run: uv tool update-shell)." >&2
  exit 1
fi
# Validate a path fail-closed via Python (already a dependency): the resolved target
# must be an absolute regular file owned by the current user, and neither it nor any
# ancestor up to "/" may be group/other-writable. A stat error terminates the check
# (nonzero exit) rather than being read as "safe". Used for the exec target and the
# installed launcher, so a writable ancestor cannot let another user swap either.
trusted_file() {
  python3 - "$1" <<'PY'
import os, sys, stat
p = os.path.realpath(sys.argv[1])
if not os.path.isfile(p):
    sys.exit("not a regular file: %s" % p)
if os.stat(p).st_uid != os.getuid():
    sys.exit("not owned by the current user: %s" % p)
cur = p
while True:
    st = os.stat(cur)  # OSError -> uncaught -> nonzero exit (fail closed)
    if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        sys.exit("group/other-writable on the trusted path: %s" % cur)
    parent = os.path.dirname(cur)
    if parent == cur:
        break
    cur = parent
PY
}

guard_bin=$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$guard_bin")
case $guard_bin in
  *[!A-Za-z0-9._/-]*) echo "error: resolved binary path has unexpected characters: $guard_bin" >&2; exit 1;;
esac
trusted_file "$guard_bin" || { echo "error: $guard_bin is not a trusted exec target (see above)" >&2; exit 1; }

root="$HOME/.local/lib/capsulectl/plugins"
mkdir -p "$root"
chmod go-w "$HOME/.local" "$HOME/.local/lib" "$HOME/.local/lib/capsulectl" "$root"

launcher="$root/capsulectl-guard"
{
  printf '#!/bin/sh\n'
  printf 'exec %s "$@"\n' "$guard_bin"
} > "$launcher"
chmod 0755 "$launcher"
trusted_file "$launcher" || { echo "error: the installed launcher $launcher is not on a trusted path (see above); capsulectl would refuse it" >&2; exit 1; }

# Fail closed: capsulectl is a prerequisite, and we only report success once it
# actually lists the plugin (proving the trusted-path check accepts the launcher).
if ! command -v capsulectl >/dev/null 2>&1; then
  echo "error: capsulectl is not on PATH; install the core CLI first" >&2
  exit 1
fi
if ! capsulectl plugin ls 2>/dev/null | grep -q '"name": *"guard"'; then
  echo "error: capsulectl did not list the guard plugin after install (trusted-path check refused it?)" >&2
  exit 1
fi
echo "installed and verified: capsulectl lists the guard plugin"
