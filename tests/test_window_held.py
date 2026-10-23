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

from capsule_engine.guards.classes import ENGINE_TAXONOMY, same_classes
from capsule_engine.packs import load_pack_dir
from capsule_engine.report.live_check import decide_check_input

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
