#!/bin/sh
# Track A installer: expose the company-side (Python) capsulectl-* subcommands as
# capsulectl plugins on a trusted plugin root, without touching the core CLI.
#
#   uv tool install  -> installs `capsule-engine` and each `capsulectl-*` entry
#                        point into an isolated tool environment on PATH
#   launcher          -> a real (non-symlink) exec wrapper on the trusted root
#                        ~/.local/lib/capsulectl/plugins, so capsulectl's
#                        trusted-path check accepts it (a symlink pointing off the
#                        root would resolve outside and be refused)
#
# Re-runnable. Requires `uv` on PATH; run from the capsule-engine checkout.
set -eu
umask 022

here=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)

echo "installing capsule-engine as an isolated tool (uv tool install)…"
uv tool install --force "$here"

guard_bin=$(command -v capsulectl-guard || true)
if [ -z "$guard_bin" ]; then
  echo "error: capsulectl-guard is not on PATH after install." >&2
  echo "       ensure uv's tool bin dir is on PATH (uv tool update-shell), then re-run." >&2
  exit 1
fi

root="$HOME/.local/lib/capsulectl/plugins"
mkdir -p "$root"
# The trusted-path check refuses a group/other-writable launcher or ancestor.
chmod go-w "$root" "$HOME/.local/lib/capsulectl" "$HOME/.local/lib" "$HOME/.local" 2>/dev/null || true

launcher="$root/capsulectl-guard"
cat > "$launcher" <<EOF
#!/bin/sh
exec "$guard_bin" "\$@"
EOF
chmod 0755 "$launcher"

echo "installed launcher: $launcher -> $guard_bin"
echo "verify with: capsulectl plugin ls"
