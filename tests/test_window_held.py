# SPDX-License-Identifier: Apache-2.0
"""A complete history the caps window cannot be summed over is held, and the
engine asks rather than allows (capsule-engine#214 group 3; Sherlock2, ops
u504): a history that reaches back fewer days than the window, or one with
acts classed under a taxonomy whose classes are not the engine's. The Go
guard holds the same window with the same reasons (go/guard/engine.go
windowCoverage)."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from cll.ledger import LedgerStore

from capsule_engine.guards.classes import ENGINE_TAXONOMY, same_classes
from capsule_engine.guards.history_state import WINDOW_HELD, history_state
from capsule_engine.packs import load_pack_dir
from capsule_engine.report.live_check import decide_check_input
from capsule_engine.report.live_history import _act_taxonomy_version, history_ledger

ROOT = Path(__file__).resolve().parent.parent
EMIT = ROOT / "go" / "guard" / "testdata" / "emit"
PACK = load_pack_dir(ROOT / "capsule_engine" / "packs" / "catalog" / "everyday")


def _envelope(name: str) -> dict:
    return json.loads((EMIT / f"{name}.json").read_text())


def _caps(envelope: dict):
    decision = decide_check_input(PACK, envelope).decision
    (caps,) = [c for c in decision.constraints if c.id == "caps"]
    return decision, caps


def test_a_history_shorter_than_the_window_is_held():
    decision, caps = _caps(_envelope("pay-history-5-days"))
    assert caps.result == "n/a"
    assert caps.reason == "the history supplied reaches back 5 day(s); the 7d total needs 7d"
    assert caps.evidence == {"constraint_id": "caps", "in_scope": True, "missing_field": None}
    assert decision.outcome == "escalate"
    assert decision.verdict == "not_evaluable"


def test_acts_under_another_taxonomy_hold_the_window():
    decision, caps = _caps(_envelope("pay-history-taxonomy-1-acts"))
    assert caps.result == "n/a"
    assert caps.reason == (
        f"3 earlier act(s) were classed under another taxonomy version than the engine's "
        f"({ENGINE_TAXONOMY.version}), so the 7d total cannot be computed"
    )
    assert decision.outcome == "escalate"


@pytest.mark.parametrize("days", [7, 31])
def test_a_history_that_reaches_over_the_window_is_summed(days):
    envelope = _envelope("pay-history-5-days")
    envelope["history_scope"]["days"] = days
    decision, caps = _caps(envelope)
    assert caps.result == "pass"
    assert decision.outcome == "allow"


def test_a_history_with_no_days_is_held_as_reaching_back_none():
    envelope = _envelope("pay-history-5-days")
    del envelope["history_scope"]["days"]
    _, caps = _caps(envelope)
    assert caps.reason == "the history supplied reaches back 0 day(s); the 7d total needs 7d"


def test_an_incomplete_history_is_held_as_incomplete_first():
    envelope = copy.deepcopy(_envelope("pay-history-5-days"))
    envelope["history_scope"]["complete"] = False
    _, caps = _caps(envelope)
    assert caps.reason.startswith("the history supplied is incomplete")


def test_a_carried_taxonomy_with_the_engines_classes_is_the_same_classes():
    assert same_classes(ENGINE_TAXONOMY.version)
    assert all(same_classes(v) for v in ("2", "3", "4", "5"))
    assert not same_classes("1")
    assert not same_classes(6)


def test_a_failing_per_purchase_limit_still_stands_under_a_held_window():
    envelope = json.loads((ROOT / "conformance-vectors" / "guard" / "v0" / "inputs" / "caps-held"
                           / "short-history-over-limit.json").read_text())
    decision, caps = _caps(envelope)
    assert caps.result == "fail"
    assert decision.outcome == "escalate"


def test_with_no_caps_fold_nothing_is_held(tmp_path):
    with LedgerStore(tmp_path / "ledger") as store:
        history_ledger(_envelope("pay-history-5-days"), store, caps_fold=None)
        assert not [r for r in store.scan() if history_state(r.capsule) == WINDOW_HELD]


def test_the_marker_changes_no_check_but_caps():
    short = decide_check_input(PACK, _envelope("pay-history-5-days")).decision
    whole = _envelope("pay-history-5-days")
    whole["history_scope"]["days"] = 31
    full = decide_check_input(PACK, whole).decision
    others = lambda d: [(c.id, c.result, c.evidence) for c in d.constraints if c.id != "caps"]  # noqa: E731
    assert others(short) == others(full)


@pytest.mark.parametrize(
    ("shown", "version"),
    [
        ({"body": {"taxonomy_version": "6", "unchecked": {"taxonomy_version": "1"}}}, "1"),
        ({"body": {"taxonomy_version": "1", "unchecked": {"amount_minor": 1}}}, "1"),
        ({"type": "action-outcome/v0", "body": {"taxonomy_version": "6", "attempted": {"taxonomy_version": "1"}}}, "1"),
        # attempted counts only on a typed outcome, as the Go guard reads it
        ({"body": {"taxonomy_version": "6", "attempted": {"taxonomy_version": "1"}}}, "6"),
        ({"body": {"taxonomy_version": "6"}}, "6"),
        (None, None),
    ],
)
def test_an_act_names_its_taxonomy_in_the_member_that_records_it(shown, version):
    assert _act_taxonomy_version(shown) == version
