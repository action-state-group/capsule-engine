# SPDX-License-Identifier: Apache-2.0
"""What the caps window counts for an accepted history act whose spend and
stated amount differ, or that seals no spend (capsule-engine#214 hold A;
Steven's ruling A):

1. with no ``spend_minor``, the stated ``amount_minor``, never below 0, under
   every fold, the frozen spend.weekly/3.0.0 and 1.0.0 included;
2. under spend.weekly/3.1.0, the larger of the stated amount and the spend;
3. a partial charge (spend below the stated amount) keeps the stated amount
   under 3.1.0. Under 3.0.0 and 1.0.0 the window still reads the spend, as
   the accepted-only replay ruling (u567) stands there apart from (1).

The Go guard counts the same (go/guard/fold.go ``summed``), and the shared
vectors (conformance-vectors/guard/v0 history-spend*) hold both to it."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from cll.ledger import LedgerStore

from capsule_engine.guards.capsule import act_payload
from capsule_engine.guards.history_state import LIVE_HISTORY, STATED_AMOUNT
from capsule_engine.packs import load_pack_dir
from capsule_engine.report.live_check import decide_check_input
from capsule_engine.report.live_history import history_ledger
from capsule_engine.report.replay import _executed_spend, action_for_history_entry

ROOT = Path(__file__).resolve().parent.parent
INPUTS = ROOT.parent / "conformance-vectors" / "guard" / "v0" / "inputs" / "history-spend"
CURRENT = load_pack_dir(ROOT / "capsule_engine" / "packs" / "catalog" / "everyday")  # spend.weekly/3.1.0
ACCEPTED_ONLY = load_pack_dir(ROOT / "tests" / "fixtures" / "packs" / "everyday-0.3.5")  # spend.weekly/3.0.0
SELLER_011 = load_pack_dir(ROOT / "tests" / "fixtures" / "packs" / "seller-0.1.1")  # spend.weekly/1.0.0


def _envelope(name: str) -> dict:
    return json.loads((INPUTS / f"{name}.json").read_text())


def _weekly(pack, name: str) -> tuple[str, int]:
    """The decision's outcome and the caps window total, for the check
    input ``name``. Each holds a 1299 purchase checked against a 10000
    window, after an accepted 2000 act and one edited act."""
    decision = decide_check_input(pack, _envelope(name)).decision
    (caps,) = [c for c in decision.constraints if c.id == "caps"]
    return decision.outcome, caps.evidence["weekly_spend_minor"]


@pytest.mark.parametrize("pack", [CURRENT, ACCEPTED_ONLY], ids=["3.1.0", "3.0.0"])
def test_an_accepted_act_with_no_spend_counts_at_its_stated_amount(pack):
    # Rule 1: 8000 stated, no spend_minor; never dropped from the window.
    assert _weekly(pack, "no-spend") == ("escalate", 8000 + 2000)


@pytest.mark.parametrize("pack", [CURRENT, ACCEPTED_ONLY, SELLER_011], ids=["3.1.0", "3.0.0", "1.0.0"])
def test_a_negative_stated_amount_with_no_spend_counts_as_0(pack):
    # Rule 1: -5000 stated never lowers the total, even under 1.0.0, which
    # has no reversal clause to keep a charge at 0 or more.
    assert _weekly(pack, "no-spend-negative-transfer") == ("allow", 2000)


def test_under_3_1_0_a_spend_above_the_stated_amount_counts():
    # Rule 2: 3000 stated, 9000 spent.
    assert _weekly(CURRENT, "spend-above-amount") == ("escalate", 9000 + 2000)


def test_under_3_1_0_a_partial_charge_keeps_its_stated_amount():
    # Rule 3: 9000 stated, 3000 spent.
    assert _weekly(CURRENT, "spend-below-amount") == ("escalate", 9000 + 2000)


def test_under_3_0_0_a_spend_that_differs_counts_at_the_spend():
    # The frozen fold reads the history copy, whose amount is the spend.
    assert _weekly(ACCEPTED_ONLY, "spend-below-amount") == ("allow", 3000 + 2000)
    assert _weekly(ACCEPTED_ONLY, "spend-above-amount") == ("escalate", 9000 + 2000)


@pytest.mark.parametrize("pack", [CURRENT, ACCEPTED_ONLY], ids=["3.1.0", "3.0.0"])
def test_a_chained_history_refund_still_takes_nothing_back(pack):
    # A history act's records carry no chain: the refund cites no charge, so
    # the 9000 charge (the 2000 act, raised) stays whole in the total.
    assert _weekly(pack, "chained-refund") == ("escalate", 9000)


def test_the_stated_amount_is_the_ledger_s_own_and_never_keys_dedupe(tmp_path):
    """The history copy of an act with no spend_minor keeps no amount_minor,
    so dedupe keys it as before (as the check it repeats is keyed), and
    carries the stated amount beside it only for the window. No decision
    seals it."""
    envelope = _envelope("no-spend")
    store = LedgerStore(tmp_path / "ledger")
    try:
        history_ledger(envelope, store)
        (copy,) = [r.capsule for r in store.scan() if r.capsule["capsule_id"] == envelope["history"][0]["capsule_id"]]
    finally:
        store.close()
    payload = copy["asg_payload"]
    assert "amount_minor" not in payload
    assert payload[STATED_AMOUNT] == 8000
    assert payload[LIVE_HISTORY] == "disposition"
    assert STATED_AMOUNT not in act_payload(action_for_history_entry(envelope["history"][0]))
    with_spend = _envelope("spend-below-amount")["history"][0]
    store = LedgerStore(tmp_path / "ledger-2")
    try:
        history_ledger({**envelope, "history": [with_spend]}, store)
        (copy,) = [r.capsule for r in store.scan() if r.capsule["capsule_id"] == with_spend["capsule_id"]]
    finally:
        store.close()
    assert copy["asg_payload"]["amount_minor"] == 3000
    assert STATED_AMOUNT not in copy["asg_payload"]


@pytest.mark.parametrize(("body", "accepted", "spend"), [
    ({"amount_minor": 9000, "spend_minor": 3000}, True, 9000),
    ({"amount_minor": 3000, "spend_minor": 9000}, True, 9000),
    ({"amount_minor": 8000}, True, 8000),
    ({"amount_minor": -5000}, True, 0),
    ({"spend_minor": 3000}, True, None),
    ({"amount_minor": 8000, "spend_minor": 5000, "direction": "in"}, True, 0),
    # Unchanged for an act not accepted.
    ({"amount_minor": 9000, "spend_minor": 3000}, False, 3000),
    ({"amount_minor": 9000}, False, None),
])
def test_the_executed_act_record_counts_an_accepted_act_at_the_larger(body, accepted, spend):
    assert _executed_spend(body, unchecked=False, accepted=accepted) == spend
