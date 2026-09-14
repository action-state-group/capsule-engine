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
# Resolve the FULL path (including a symlinked final component) to a real target,
# then validate it: an absolute regular file, owned by us, not group/other-writable,
# no shell metacharacters.
guard_bin=$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$guard_bin")
case $guard_bin in
  *[!A-Za-z0-9._/-]*) echo "error: resolved binary path has unexpected characters: $guard_bin" >&2; exit 1;;
esac
[ -f "$guard_bin" ] || { echo "error: $guard_bin is not a regular file" >&2; exit 1; }
[ -O "$guard_bin" ] || { echo "error: $guard_bin is not owned by the current user" >&2; exit 1; }
# group-writable (-perm -0020) or other-writable (-perm -0002); find -perm -mode is
# POSIX and correct across GNU/BSD, unlike parsing ls columns.
writable() { [ -n "$(find "$1" -maxdepth 0 \( -perm -0020 -o -perm -0002 \) 2>/dev/null)" ]; }
if writable "$guard_bin"; then echo "error: $guard_bin is group- or other-writable" >&2; exit 1; fi

root="$HOME/.local/lib/capsulectl/plugins"
mkdir -p "$root"
# Harden each directory up to the root; a swallowed failure would produce an
# install capsulectl silently refuses, so check the result instead of `|| true`.
for dir in "$HOME/.local" "$HOME/.local/lib" "$HOME/.local/lib/capsulectl" "$root"; do
  chmod go-w "$dir"
  if writable "$dir"; then echo "error: $dir remains group/other-writable after chmod" >&2; exit 1; fi
done

launcher="$root/capsulectl-guard"
{
  printf '#!/bin/sh\n'
  printf 'exec %s "$@"\n' "$guard_bin"
} > "$launcher"
chmod 0755 "$launcher"

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
