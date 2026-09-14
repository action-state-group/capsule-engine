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
# Resolve to a real path and validate it is a plausible, safe target: an absolute
# regular file, owned by us, not group/other-writable, no shell metacharacters.
guard_bin=$(cd -- "$(dirname -- "$guard_bin")" && printf '%s/%s' "$(pwd -P)" "$(basename -- "$guard_bin")")
case $guard_bin in
  *[!A-Za-z0-9._/-]*) echo "error: resolved binary path has unexpected characters: $guard_bin" >&2; exit 1;;
esac
[ -f "$guard_bin" ] || { echo "error: $guard_bin is not a regular file" >&2; exit 1; }
[ -O "$guard_bin" ] || { echo "error: $guard_bin is not owned by the current user" >&2; exit 1; }
case $(ls -ld -- "$guard_bin" | cut -c1-10) in
  *w??) echo "error: $guard_bin is other-writable" >&2; exit 1;;
  *w?) echo "error: $guard_bin is group-writable" >&2; exit 1;;
esac

root="$HOME/.local/lib/capsulectl/plugins"
mkdir -p "$root"
# Harden each directory up to the root; a swallowed failure would produce an
# install capsulectl silently refuses, so check the result instead of `|| true`.
for dir in "$HOME/.local" "$HOME/.local/lib" "$HOME/.local/lib/capsulectl" "$root"; do
  chmod go-w "$dir"
  case $(ls -ld -- "$dir" | cut -c1-10) in
    *w???|*w??) echo "error: $dir remains group/other-writable after chmod" >&2; exit 1;;
  esac
done

launcher="$root/capsulectl-guard"
{
  printf '#!/bin/sh\n'
  printf 'exec %s "$@"\n' "$guard_bin"
} > "$launcher"
chmod 0755 "$launcher"

# Confirm capsulectl actually accepts and lists the plugin; if the trusted-path
# check refuses it, fail rather than report a success the runtime won't honor.
if command -v capsulectl >/dev/null 2>&1; then
  if ! capsulectl plugin ls 2>/dev/null | grep -q '"name": *"guard"'; then
    echo "error: capsulectl did not list the guard plugin after install (trusted-path check refused it?)" >&2
    exit 1
  fi
  echo "installed and verified: capsulectl lists the guard plugin"
else
  echo "installed launcher $launcher -> $guard_bin (capsulectl not on PATH to verify; run: capsulectl plugin ls)"
fi
