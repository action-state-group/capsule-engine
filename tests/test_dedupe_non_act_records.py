# SPDX-License-Identifier: Apache-2.0
"""``dedupe`` is not applicable to a deal record that states no act.

A capsulectl deal seals every step as its own record: a baseline, a check, a
verdict, an approval, an intent, the action that carries a check out. Only a
check states an act to the guard; the action step is the same payment its
check already stated. Whether a record is a check is read from its sealed
``x-deal-v0.record_type``, on a record bound to its capsule by digest, never
from the capsule's ``action_id`` prefix or ``action_type``. Every other deal
record's ``dedupe`` is ``n/a`` (out of scope), so it is never refused as a
duplicate of the deal's other records, and a repeated act is still refused.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest
from agent_action_capsule import json_digest

from capsule_engine.folds.loader import load_definition_file as load_fold
from capsule_engine.guards.capsule import DENY, not_applicable_evidence
from capsule_engine.report.replay import action_for_record, load_disclosed, load_records, replay

PACKAGE_DIR = Path(__file__).parent.parent / "capsule_engine"
SPEND_WEEKLY = PACKAGE_DIR / "folds" / "catalog_defs" / "spend.weekly.yaml"
BUNDLES = Path(__file__).parent / "fixtures" / "deal-bundles"
FIXTURES = ("deal-purchase-then-partial-cancel", "deal-purchase-then-refund")


def _replayed(records: list[dict], disclosed: dict[str, dict]):
    return replay(records, caps_fold=load_fold(SPEND_WEEKLY), disclosed=disclosed).decisions


def _bundle(name: str) -> tuple[list[dict], dict[str, dict]]:
    path = [BUNDLES / f"{name}.bundle.json"]
    return load_records(path), load_disclosed(path)


def _record_type(disclosed: dict[str, dict], record: dict) -> str:
    return disclosed[record["capsule_id"]]["x-deal-v0"]["record_type"]


def _dedupe(decision):
    return next(c for c in decision.constraints if c.id == "dedupe")


@pytest.mark.parametrize("name", FIXTURES)
def test_no_deal_record_but_a_check_gets_a_dedupe_finding(name):
    records, disclosed = _bundle(name)
    non_checks = [s for s in _replayed(records, disclosed) if _record_type(disclosed, s.record) != "check"]
    assert {_record_type(disclosed, s.record) for s in non_checks} == {"baseline", "verdict", "approval", "action", "intent"}
    for sourced in non_checks:
        dedupe = _dedupe(sourced.decision)
        assert (dedupe.result, dedupe.evidence) == ("n/a", not_applicable_evidence("dedupe", in_scope=False))
        assert sourced.decision.outcome != DENY


@pytest.mark.parametrize("name", FIXTURES)
def test_every_check_in_a_deal_fixture_is_still_deduped_and_passes(name):
    records, disclosed = _bundle(name)
    checks = [s for s in _replayed(records, disclosed) if _record_type(disclosed, s.record) == "check"]
    assert len(checks) == 2
    assert [_dedupe(s.decision).result for s in checks] == ["pass", "pass"]


def test_a_repeated_check_is_still_refused_across_the_deals_other_records():
    """The purchase check sealed again after every other record of the deal
    is the same act: refused, naming the first check's decision."""
    records, disclosed = _bundle("deal-purchase-then-refund")
    purchase = next(r for r in records if _record_type(disclosed, r) == "check")
    again = {**purchase, "capsule_id": "f" * 64, "action_id": "deal-1a2f6df4f4186be2/11"}
    decisions = _replayed([*records, again], {**disclosed, again["capsule_id"]: disclosed[purchase["capsule_id"]]})
    first = next(s.decision for s in decisions if s.record is purchase)
    last = decisions[-1].decision
    assert (_dedupe(last).result, last.outcome) == ("fail", DENY)
    assert _dedupe(last).evidence["matched_capsule_id"] == first.capsule["capsule_id"]


def test_the_sealed_record_type_decides_not_the_id_prefix_or_action_type():
    records, disclosed = _bundle("deal-purchase-then-refund")
    verdict = next(r for r in records if _record_type(disclosed, r) == "verdict")
    renamed = {**verdict, "action_id": "pay/elsewhere-1", "action_type": "decide"}
    assert not action_for_record(renamed, disclosed[verdict["capsule_id"]]).states_act


def test_a_record_its_disclosure_does_not_bind_is_deduped_as_before():
    """Nothing unbound is read: a disclosure whose digest does not match the
    capsule's ``agent_input_digest`` says nothing about the record."""
    records, disclosed = _bundle("deal-purchase-then-refund")
    verdict = next(r for r in records if _record_type(disclosed, r) == "verdict")
    forged = {**disclosed[verdict["capsule_id"]], "note": "not what the capsule sealed"}
    assert action_for_record(verdict, forged).states_act
    assert action_for_record(verdict).states_act


def test_a_check_the_bridge_declines_still_states_an_act():
    """A bound check without ``taxonomy_version`` is not bridged to its act;
    it is still a check, so it is deduped as any record is, never skipped."""
    records, disclosed = _bundle("deal-purchase-then-refund")
    check = next(r for r in records if _record_type(disclosed, r) == "check")
    record = {**disclosed[check["capsule_id"]]}
    record["body"] = {k: v for k, v in record["body"].items() if k != "taxonomy_version"}
    attestation = {"compute_attestation": {"agent_input_digest": json_digest(record)}}
    capsule = {**check, "model_attestation": attestation}
    action = action_for_record(capsule, record)
    assert (action.taxonomy_version, action.states_act) == (None, True)


def _calls_setting_taxonomy_version(tree: ast.AST) -> list[str]:
    """The enclosing function of every call that passes ``taxonomy_version=``
    to ``Action``, ``cls`` or ``replace`` (``dataclasses.replace``)."""
    found: list[str] = []
    for func in (n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))):
        for call in (n for n in ast.walk(func) if isinstance(n, ast.Call)):
            callee = call.func
            name = callee.id if isinstance(callee, ast.Name) else callee.attr if isinstance(callee, ast.Attribute) else None
            if name in ("Action", "cls", "replace") and any(k.arg == "taxonomy_version" for k in call.keywords):
                found.append(func.name)
    return found


def test_only_the_deal_bridge_and_the_capsule_round_trip_pin_a_taxonomy_on_an_action():
    """``dedupe`` keys an action on its act when ``taxonomy_version`` is set.
    That switch stays the deal bridge's: no other producer sets it, so no
    other action changes key. ``Action.from_capsule`` reads it back from a
    decision the bridge's action sealed."""
    setters = {
        (path.relative_to(PACKAGE_DIR).as_posix(), func)
        for path in PACKAGE_DIR.rglob("*.py")
        for func in _calls_setting_taxonomy_version(ast.parse(path.read_text(encoding="utf-8")))
    }
    assert setters == {("report/replay.py", "_bridge_deal_check"), ("guards/action.py", "from_capsule")}
