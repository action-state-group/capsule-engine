# SPDX-License-Identifier: Apache-2.0
"""``capsule-engine packs`` verbs -- the packs-runtime's own CLI surface.

  propose -- the GENERIC, READ-ONLY "would this pack
             work" measurability report for ANY pack: resolves/MISSING-
             INSTRUMENT per outcome, from the pack's own tier/mode/
             evidence_instrument fields, over a JSONL corpus. Ported from
             capsule-ledger's ``capsule setup propose --pack`` (moved here
             per Amendment H.4 -- the packs runtime's post-move home) --
             this mode never touches ``.capsule-setup/`` and persists
             nothing, so it carries no dependency on the setup/Candidate
             machinery that stayed in capsule-compiler.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..packs import PackDefinitionError, load_pack_dir
from ..packs import measurability_report as pack_measurability_report

__all__ = ["add_parser"]


def _load_units_jsonl(path: str) -> list[dict]:
    units = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                units.append(json.loads(line))
    return units


def _cmd_packs_propose(args: argparse.Namespace) -> int:
    """READ-ONLY: prints resolves/MISSING-INSTRUMENT per outcome, persists
    nothing -- there are no T1 declarations to confirm afterward. Confirmable
    persistence for a generic pack is a separate design decision (a
    MeasurabilityRow is not a Candidate/ProposedOutcome), not built here."""
    if args.corpus is None:
        print("capsule-engine packs propose: --corpus is required", file=sys.stderr)
        return 2
    if args.entity_key is None:
        print(
            "capsule-engine packs propose: --entity-key is required (no default -- names the unit field "
            "identifying a repeat entity for fold_counterparty/fold_cohort rows, e.g. task_id, session_id)",
            file=sys.stderr,
        )
        return 2
    try:
        pack = load_pack_dir(Path(args.pack))
    except PackDefinitionError as exc:
        print(f"capsule-engine packs propose: pack failed to load ({exc.reason}): {exc}", file=sys.stderr)
        return 1

    units = _load_units_jsonl(args.corpus)
    key = args.entity_key
    try:
        report = pack_measurability_report.build_measurability_report(
            pack, units, entity_key=pack_measurability_report.entity_key_field(key)
        )
    except pack_measurability_report.MissingEntityKeyField as exc:
        print(f"capsule-engine packs propose: {exc}", file=sys.stderr)
        return 2

    print(f"pack: {pack.pack_id}")
    print(f"corpus: {args.corpus} ({len(units)} unit(s))")
    print(f"entity_key: {key}")
    print("READ-ONLY report -- no declarations persisted")
    print()
    print(pack_measurability_report.render_terminal(report), end="")
    return 0


def add_parser(sub: argparse._SubParsersAction) -> argparse.ArgumentParser:
    packs = sub.add_parser("packs", help="packs-runtime verbs: propose (generic measurability report)")
    packs_sub = packs.add_subparsers(dest="packs_command")
    packs.set_defaults(packs_parser=packs)

    p_propose = packs_sub.add_parser(
        "propose",
        help="GENERIC READ-ONLY measurability report for any pack over a JSONL corpus",
    )
    p_propose.add_argument("--pack", required=True, help="path to a pack directory (pack.yaml)")
    p_propose.add_argument("--corpus", default=None, help="path to a JSONL file of units shaped {'messages': [...]}")
    p_propose.add_argument(
        "--entity-key",
        default=None,
        help="REQUIRED, no default: the unit field identifying a repeat entity for fold_counterparty/"
        "fold_cohort rows (e.g. task_id, session_id)",
    )
    p_propose.set_defaults(func=_cmd_packs_propose)

    return packs
