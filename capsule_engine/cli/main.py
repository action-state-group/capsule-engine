# SPDX-License-Identifier: Apache-2.0
"""`capsule-engine` CLI entry point: the record-query/evidence verbs this
repo owns -- `verify` (verify one ledger record or an offline bundle) and
`bundle` (produce a self-contained verifiable slice of the ledger).

This is a deliberately minimal dispatcher: the original monorepo's
`cli/main.py` wired every verb across ledger, folds, guards, packs, policy,
console, telemetry, etc. Post-split, this repo only owns the verb modules
that actually live here (`cli/bundle_cmd.py`, `cli/verify_cmd.py`) -- the
rest stayed with their owning repo. `cli/format.py` is not wired as a
subcommand: it has no `add_parser`, it is shared output-formatting helpers
used by the verbs above.

This module is a thin dispatcher; each verb's logic lives in its own
`cli/*_cmd.py` module.
"""
from __future__ import annotations

import argparse
import sys

from . import bundle_cmd, verify_cmd

__all__ = ["main"]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="capsule-engine", description="capsule-engine control plane")
    parser.add_argument("--version", action="store_true", help="print the version and exit")
    sub = parser.add_subparsers(dest="command")

    verify_cmd.add_parser(sub)
    bundle_cmd.add_parser(sub)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.version:
        from .. import __version__

        print(__version__)
        return 0

    if args.command is None:
        parser.print_help()
        return 0

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
