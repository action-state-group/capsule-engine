# SPDX-License-Identifier: Apache-2.0
"""`capsule-engine` CLI entry point: the verbs this repo owns -- `guard`
(dry-run replay + report, enforce marker), `telemetry` (disclosure/status,
funnel report), `init` (install a starter pack), `constraints` (list the
registered guard checks + action-class taxonomy), `tenant`
(engine-instance-per-tenant provisioning), plus the evidence verbs `verify`
(verify one ledger record or an offline bundle), `bundle` (produce a
self-contained verifiable slice of the ledger), and `console` (serve the
local console UI) -- registered only in the "full" packaging arm, same
two-arm switch (`capsule_engine.packaging`) the original monorepo used.

This is a deliberately minimal dispatcher: the original monorepo's
`cli/main.py` wired every verb across ledger, folds, guards, packs, policy,
console, telemetry, etc. Post-split, this repo only owns the verb modules
that actually live here -- the rest stayed with their owning repo.
`cli/format.py` is not wired as a subcommand: it has no `add_parser`, it is
shared output-formatting helpers used by the verbs above.

This module is a thin dispatcher; each verb's logic lives in its own
`cli/*_cmd.py` module.
"""
from __future__ import annotations

import argparse
import sys

from .. import packaging
from . import (
    console_cmd,
    constraints_cmd,
    guard_cmds,
    init_cmds,
    packs_cmds,
    telemetry_cmd,
    tenant_cmds,
    verify_cmd,
)

__all__ = ["main"]


def _build_parser(arm: str | None = None) -> argparse.ArgumentParser:
    arm = arm or packaging.current_arm()
    parser = argparse.ArgumentParser(prog="capsule-engine", description="capsule-engine control plane")
    parser.add_argument("--version", action="store_true", help="print the version and exit")
    sub = parser.add_subparsers(dest="command")

    init_cmds.add_parser(sub)
    constraints_cmd.add_parser(sub)
    guard_cmds.add_parser(sub)
    tenant_cmds.add_parser(sub)
    telemetry_cmd.add_parser(sub)
    packs_cmds.add_parser(sub)

    # The record-query/evidence verbs -- capsule verify, the shareable
    # bundle, and the local console UI -- are the "evidence": registered
    # only in the "full" arm. See ``packaging.py``'s module docstring for
    # why an env var, not a fork, drives this.
    if packaging.evidence_visible(arm):
        verify_cmd.add_parser(sub)
        console_cmd.add_parser(sub)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.version:
        from .. import __version__

        print(__version__)
        return 0

    from ..telemetry.record import record_install_seen

    record_install_seen(packaging.current_arm())

    if args.command is None:
        parser.print_help()
        return 0

    if args.command == "constraints":
        if getattr(args, "constraints_command", None) is None:
            args.constraints_parser.print_help()
            return 0
        return args.func(args)

    if args.command == "guard":
        if getattr(args, "guard_command", None) is None:
            args.guard_parser.print_help()
            return 0
        return args.func(args)

    if args.command == "tenant":
        if getattr(args, "tenant_command", None) is None:
            args.tenant_parser.print_help()
            return 0
        return args.func(args)

    if args.command == "telemetry":
        if getattr(args, "telemetry_command", None) is None:
            args.telemetry_parser.print_help()
            return 0
        return args.func(args)

    if args.command == "packs":
        if getattr(args, "packs_command", None) is None:
            args.packs_parser.print_help()
            return 0
        return args.func(args)

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
