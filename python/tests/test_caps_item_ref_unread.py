# SPDX-License-Identifier: Apache-2.0
"""A history that declares itself complete but holds an act whose
``item_ref`` cannot be read, or that is absent or null, may be missing acts
(``report/live_history.py`` ``history_ledger``), so caps reports the window as
not evaluable and the pay is never allowed (capsule-engine #328, ops u503). A
per-action limit that fails still fails first; an empty history and a
readable ``item_ref`` stay complete. The Go guard decides the same inputs
(``conformance-vectors/guard/v0/item-ref-unread-vectors.json``,
``go/guard/caps_item_ref_unread_test.go``)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from capsule_engine.packs import load_pack_dir
from capsule_engine.report.live_check import decide_check_input

ROOT = Path(__file__).resolve().parent.parent
INPUTS = ROOT.parent / "go" / "guard" / "testdata" / "item-ref-unread-vectors"
PACK = load_pack_dir(ROOT / "capsule_engine" / "packs" / "catalog" / "everyday")


def _caps(name: str):
    decision = decide_check_input(PACK, json.loads((INPUTS / f"{name}.json").read_text())).decision
    (caps,) = [c for c in decision.constraints if c.id == "caps"]
    return decision, caps


@pytest.mark.parametrize(
    "name",
    ["item-ref-null", "item-ref-not-hex", "item-ref-null-days-short", "history-null", "history-absent"],
)
def test_a_history_that_may_be_missing_acts_leaves_the_window_unevaluated(name):
    decision, caps = _caps(name)
    assert caps.result == "n/a"
    assert caps.reason == (
        "the history supplied is incomplete (more acts than it holds, or some could not be read), "
        "so the 7d total cannot be computed"
    )
    assert decision.outcome != "allow"


def test_a_per_action_limit_that_fails_still_fails_first():
    decision, caps = _caps("item-ref-null-per-action-over")
    assert caps.result == "fail"
    assert decision.outcome != "allow"


@pytest.mark.parametrize("name", ["item-ref-readable", "history-empty"])
def test_a_readable_history_still_decides_the_window(name):
    _, caps = _caps(name)
    assert caps.result == "pass"
