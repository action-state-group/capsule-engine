"""capsulectl plugin launchers for the company-side (Python) subcommands.

Each launcher is a `capsulectl-<name>` console entry point that capsulectl's
Docker-style discovery finds on a trusted plugin root. On the reserved
`cli-plugin-metadata` argument it prints the handshake object capsulectl checks
(name, vendor, version, plugin_api, subcommands); on anything else it delegates
to the corresponding `capsule-engine` verb. The core CLI never bundles or
installs these; an operator installs them onto a trusted root (see
`scripts/install-capsulectl-plugins.sh`).
"""

from __future__ import annotations

import json
import sys
from importlib import metadata

from .main import main as _run_cli

# Must match capsulectl's pluginAPI constant (internal/cli/plugin.go).
PLUGIN_API = "cli-plugin/v1"
VENDOR = "capsule-engine (Action State Group)"
METADATA_ARG = "cli-plugin-metadata"


def _version() -> str:
    try:
        return metadata.version("capsule-engine")
    except metadata.PackageNotFoundError:
        return "0+unknown"


def _launch(name: str, subcommands: list[str]) -> int:
    args = sys.argv[1:]
    if args[:1] == [METADATA_ARG]:
        json.dump(
            {
                "name": name,
                "vendor": VENDOR,
                "version": _version(),
                "plugin_api": PLUGIN_API,
                "subcommands": subcommands,
            },
            sys.stdout,
        )
        sys.stdout.write("\n")
        return 0
    # Delegate to the owning capsule-engine verb, args passed straight through.
    return _run_cli([name, *args])


def capsulectl_guard() -> int:
    """`capsulectl-guard` — the guard verb as a capsulectl plugin."""
    return _launch("guard", ["dry-run", "enforce"])


if __name__ == "__main__":
    raise SystemExit(capsulectl_guard())
