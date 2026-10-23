# SPDX-License-Identifier: Apache-2.0
"""`capsulectl-engine`: the engine as a capsulectl plugin (cli-plugin/v1).

On a trusted plugin root, capsulectl wires it up as `capsulectl engine`. On
the reserved `cli-plugin-metadata` argument it prints the handshake object
capsulectl checks (capsule-cli docs/PLUGINS.md). Its one subcommand,
`check --ruleset FILE`, reads one external-check-input/v0 on stdin and prints
one external-check-result/v0 (``rulesets/check.py``): exit 0 means a result
was printed, whatever its verdict; any other exit is a refusal, named on
stderr, with nothing on stdout. That is capsule-cli #137's rules-checker
contract too, so the same launcher pins as a deal profile's
`--rules-checker` with the command
`["<root>/capsulectl-engine", "check", "--ruleset", "FILE"]`, FILE an
absolute path (capsulectl does not set the checker's working directory).

capsulectl pins this launcher's bytes, and the launcher is a console-script
stub: the code it runs is the installed capsule-engine package, which the pin
does not cover. So a check's name/version (``record_type_coverage/1``) is
frozen once released, and a change to what it decides is a new version.

Install: `capsulectl plugin install ./capsulectl-engine`, pointing at the
console script the package installs.
"""
from __future__ import annotations

import argparse
import json
import sys

from .. import __version__
from ..rulesets import Refusal, check, encode_result, load_ruleset

__all__ = ["main"]

# capsulectl's pluginAPI constant (capsule-cli internal/cli/plugin.go).
PLUGIN_API = "cli-plugin/v1"
METADATA_ARG = "cli-plugin-metadata"
NAME = "engine"


class _Parser(argparse.ArgumentParser):
    """A usage error is one line: capsulectl keeps a refusing checker's first
    stderr line as its reason, and a usage block would start with "usage:"."""

    def error(self, message: str):
        self.exit(2, f"{self.prog}: {message}\n")


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(prog=f"capsulectl {NAME}", description="Recompute a ruleset's clauses on a record.")
    sub = parser.add_subparsers(dest="command", required=True, parser_class=_Parser)
    c = sub.add_parser(
        "check",
        help="check one external-check-input/v0 (stdin) against a ruleset",
        description="Read one external-check-input/v0 on stdin and print one external-check-result/v0, "
        "tier recomputed, with a finding for every clause of the ruleset.",
    )
    c.add_argument("--ruleset", required=True, metavar="FILE", help="an engine-ruleset/v0 file (JSON)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args[:1] == [METADATA_ARG]:
        handshake = {
            "name": NAME,
            "vendor": "capsule-engine (Action State Group)",
            "version": __version__,
            "plugin_api": PLUGIN_API,
            "subcommands": ["check"],
        }
        sys.stdout.write(json.dumps(handshake) + "\n")
        return 0
    ns = _parser().parse_args(args)
    try:
        out = encode_result(check(load_ruleset(ns.ruleset), sys.stdin.buffer.read()))
    except Refusal as exc:
        print(f"capsulectl {NAME} check: {exc}", file=sys.stderr)
        return 1
    sys.stdout.buffer.write(out)
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
