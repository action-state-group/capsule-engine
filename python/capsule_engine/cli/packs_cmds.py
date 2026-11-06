# SPDX-License-Identifier: Apache-2.0
"""``capsule-engine packs`` verbs -- the packs-runtime's own CLI surface.

  propose -- the GENERIC, READ-ONLY "would this pack
             work" measurability report for ANY pack: resolves/MISSING-
             INSTRUMENT per outcome, from the pack's own tier/mode/
             evidence_instrument fields, over a JSONL corpus. Ported from
             capsule-ledger's ``capsule setup propose --pack`` (moved here
             -- the packs runtime's home) --
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


_EVALUATE_RESULTS = {"pass": "met", "fail": "not_met"}
# The placeholders a case's actions take when they name no operator or developer.
_EVALUATE_PRINCIPAL = "case-operator"


def _evaluate_step(raw: object) -> dict:
    """One case action as ``decide_steps`` takes it: ``at`` is the action's
    ``timestamp``, ``verb`` defaults to ``action_type``, and operator and
    developer default to placeholders. A key that is not an ``Action`` field
    is refused rather than ignored, so a typo cannot pass a case unevaluated."""
    import dataclasses

    from ..guards import Action

    if not isinstance(raw, dict):
        raise ValueError("each action must be a JSON object")
    step = dict(raw)
    if "at" in step:
        if "timestamp" in step:
            raise ValueError("an action gives both 'at' and 'timestamp'")
        step["timestamp"] = step.pop("at")
    step.setdefault("verb", step.get("action_type", "decide"))
    step.setdefault("operator", _EVALUATE_PRINCIPAL)
    step.setdefault("developer", _EVALUATE_PRINCIPAL)
    unknown = sorted(set(step) - {f.name for f in dataclasses.fields(Action)})
    if unknown:
        raise ValueError(f"unknown action field(s): {', '.join(unknown)}")
    return step


def _cmd_packs_evaluate(args: argparse.Namespace) -> int:
    """Decides the input's actions in order on one throwaway ledger (each
    decision is held before the next action is checked) and prints each of the
    pack's rows as the LAST action's decision leaves it: ``met`` (pass),
    ``not_met`` (fail), or ``not_evaluable`` (the row does not apply to that
    action, or no check measures it). Exit 0 when the rows were decided,
    whatever they say; 2 when they could not be, with nothing on stdout and
    the reason on stderr."""
    from .. import __version__
    from ..packs.obligation_results import obligation_results
    from ..report.live_check import decide_steps

    def unusable(why: str) -> int:
        print(f"capsule-engine packs evaluate: {why}", file=sys.stderr)
        return 2

    try:
        pack = load_pack_dir(Path(args.pack))
    except PackDefinitionError as exc:
        return unusable(f"pack failed to load ({exc.reason}): {exc}")
    try:
        text = sys.stdin.read() if args.input in (None, "-") else Path(args.input).read_text(encoding="utf-8")
        doc = json.loads(text)
        actions = doc.get("actions") if isinstance(doc, dict) else None
        if not isinstance(actions, list) or not actions:
            raise ValueError('input must be {"actions": [...]} with at least one action')
        steps = [_evaluate_step(a) for a in actions]
        decisions = decide_steps(pack, steps)
        by_id = {r.obligation_id: r for r in obligation_results(pack, decisions[-1].constraints)}
    except (OSError, ValueError, TypeError) as exc:  # json.JSONDecodeError is a ValueError
        return unusable(f"unusable input: {exc}")

    rows = []
    for o in pack.obligations:
        r = by_id.get(o.id)
        if r is None:
            rows.append({"row": o.id, "result": "not_evaluable", "reason": "no check measures this row"})
        else:
            rows.append({"row": o.id, "result": _EVALUATE_RESULTS.get(r.result, "not_evaluable"), "reason": r.reason})
    out = {
        "engine": {"name": "capsule-engine", "version": __version__},
        "pack": {"id": pack.pack_id, "digest": pack.definition_digest()},
        "rows": rows,
    }
    print(json.dumps(out, indent=2, sort_keys=True))
    return 0


def add_parser(sub: argparse._SubParsersAction) -> argparse.ArgumentParser:
    packs = sub.add_parser("packs", help="packs-runtime verbs: propose (measurability report), evaluate (decide a live pack's rows)")
    packs_sub = packs.add_subparsers(dest="packs_command")
    packs.set_defaults(packs_parser=packs)

    p_propose = packs_sub.add_parser(
        "propose",
        help="GENERIC READ-ONLY measurability report for any pack over a JSONL corpus",
    )
    p_propose.add_argument("--pack", required=True, help="path to a pack directory (pack-source.yaml)")
    p_propose.add_argument("--corpus", default=None, help="path to a JSONL file of units shaped {'messages': [...]}")
    p_propose.add_argument(
        "--entity-key",
        default=None,
        help="REQUIRED, no default: the unit field identifying a repeat entity for fold_counterparty/"
        "fold_cohort rows (e.g. task_id, session_id)",
    )
    p_propose.set_defaults(func=_cmd_packs_propose)

    p_evaluate = packs_sub.add_parser(
        "evaluate",
        help="decide a live pack's rows on a list of actions ({'actions': [...]}), as JSON",
    )
    p_evaluate.add_argument("--pack", required=True, help="path to a live pack directory (pack-source.yaml)")
    p_evaluate.add_argument("--input", default=None, help="JSON file {'actions': [...]}; stdin when empty or -")
    p_evaluate.set_defaults(func=_cmd_packs_evaluate)

    return packs
