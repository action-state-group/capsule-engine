# SPDX-License-Identifier: Apache-2.0
"""`capsule constraints list`: the registered guard checks + the starter
action-class taxonomy (T3's ``guards/classes.py``) -- a static catalog, not
a ledger query, so it needs no ``--ledger``."""
from __future__ import annotations

import argparse
from pathlib import Path

from ..folds.catalog import Catalog
from ..guards.classes import TAXONOMY, UNCLASSIFIED_DEFAULT

__all__ = ["add_parser", "run_list", "CHECKS", "DEFAULT_CAPS_FOLD_ID", "DEFAULT_CATALOG_DIR"]

DEFAULT_CATALOG_DIR = Path(__file__).resolve().parent.parent / "folds" / "catalog_defs"
# The caps check's fold is a per-deployment GuardEngine configuration
# (`GuardEngine(caps_fold=...)`); this listing resolves the one T3 wires up
# in its own tests/fixtures (`conftest.py`'s `caps_fold` fixture) as the
# representative default, rather than invent one of its own.
DEFAULT_CAPS_FOLD_ID = "spend.weekly/1.0.0"

# (check_id, check_type, method, description) -- method is None for `caps`,
# whose method is the configured fold_id, resolved at print time below.
# Public (not `_`-prefixed): the MCP server's `constraints.list` tool reuses
# this exact catalog rather than keeping its own copy in sync by hand.
CHECKS = [
    (
        "dedupe",
        "policy",
        "exact_match_index_v0",
        "equivalence lookup over a rolling window — exact-match only, no fuzzy/semantic matching",
    ),
    (
        "caps",
        "policy",
        None,
        "fold predicate: weekly_spend + amount <= cap (integer minor units only)",
    ),
    (
        "verify_before_dispatch",
        "policy",
        "agent_action_capsule.verify",
        "the cited mandate capsule must exist in the ledger and independently re-verify",
    ),
]


def add_parser(sub: argparse._SubParsersAction) -> argparse.ArgumentParser:
    constraints = sub.add_parser("constraints", help="the registered guard checks and the action-class taxonomy")
    csub = constraints.add_subparsers(dest="constraints_command")
    constraints.set_defaults(constraints_parser=constraints)

    p_list = csub.add_parser("list", help="list the registered guard checks and the action-class taxonomy")
    p_list.add_argument("--dir", help="fold catalog directory the caps check reads from (default: built-in catalog)")
    p_list.set_defaults(func=run_list)

    return constraints


def run_list(args: argparse.Namespace) -> int:
    catalog_dir = Path(args.dir) if getattr(args, "dir", None) else DEFAULT_CATALOG_DIR
    caps_entry = Catalog(catalog_dir).get(DEFAULT_CAPS_FOLD_ID)
    caps_method = caps_entry.definition.fold_id if caps_entry is not None else "(no caps fold configured)"

    print("checks:")
    for check_id, check_type, method, description in CHECKS:
        resolved_method = caps_method if check_id == "caps" else method
        print(f"  {check_id:<24} {check_type:<8} {resolved_method:<28} {description}")

    print()
    print("action classes (absent or unrecognized -> unclassified, fail-closed):")
    for ac in (*TAXONOMY.values(), UNCLASSIFIED_DEFAULT):
        print(f"  {ac.name:<20} consequential={str(ac.consequential):<6} fail_open_allowed={ac.fail_open_allowed}")
    return 0
