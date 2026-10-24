# SPDX-License-Identifier: Apache-2.0
"""A reversal in a live check's history frees a partial charge's headroom
(capsule-engine#234, ruling A on #214 hold A). The conformance vectors
``history-spend.json`` pin the decisions; this pins what they do not reach:
several reversals of one charge, a repeated reversal, a reversal under
another operator, and a reversal sealed after the check."""
from __future__ import annotations

from datetime import datetime, timezone

from agent_action_capsule import json_digest

from capsule_engine.folds.definition import Reversal
from capsule_engine.report.replay import _history_freed

REVERSAL = Reversal(
    class_field="asg_payload.action_class", classes=("booking.cancel", "money.refund"),
    parent_field="chain.parent_capsule_id", relation_field="chain.relation", relation="supersedes",
)
CHECKED = datetime(2026, 10, 7, 21, 40, tzinfo=timezone.utc)
CHARGE = "c" * 64


def act(capsule_id: str, at: str, *, operator: str = "deal", accept: bool = True, parent: str | None = None,
        relation: str = "supersedes", **body: object) -> dict:
    shown = {"body": {"action": "pay", "direction": "out", **body}, "x-deal-v0": {"record_type": "action"}}
    entry = {
        "capsule_id": capsule_id, "action_id": f"deal-1/{capsule_id[:4]}", "timestamp": at, "operator": operator,
        "developer": "capsulectl-deal", "agent_input": shown,
        "model_attestation": {"compute_attestation": {"agent_input_digest": json_digest(shown)}},
    }
    if accept:
        entry["disposition"] = {"decision": "accept"}
    if parent is not None:
        entry["chain"] = {"parent_capsule_id": parent, "relation": relation}
    return entry


def charge(**kw: object) -> dict:
    return act(CHARGE, "2026-10-06T09:00:00Z", action_class="money.purchase", amount_minor=9000, spend_minor=3000, **kw)


def refund(capsule_id: str, returned: int, at: str = "2026-10-06T12:00:00Z", **kw: object) -> dict:
    return act(capsule_id, at, action_class="money.refund", amount_minor=returned, spend_minor=0, direction="in",
               parent=kw.pop("parent", CHARGE), **kw)


def freed(*entries: dict) -> dict[str, int]:
    return _history_freed(list(entries), REVERSAL, CHECKED)


def test_a_linked_refund_frees_what_it_returns() -> None:
    assert freed(charge(), refund("a" * 64, 2000)) == {CHARGE: 2000}


def test_refunds_of_one_charge_add_up_and_stop_at_its_spend() -> None:
    assert freed(charge(), refund("a" * 64, 4000), refund("b" * 64, 4000)) == {CHARGE: 6000}


def test_the_same_refund_given_twice_counts_once() -> None:
    assert freed(charge(), refund("a" * 64, 2000), refund("a" * 64, 2000)) == {CHARGE: 2000}


def test_a_charge_that_spent_its_stated_amount_frees_nothing() -> None:
    whole = act(CHARGE, "2026-10-06T09:00:00Z", action_class="money.purchase", amount_minor=9000, spend_minor=9000)
    assert freed(whole, refund("a" * 64, 9000)) == {}


def test_a_charge_with_no_spend_frees_nothing() -> None:
    unspent = act(CHARGE, "2026-10-06T09:00:00Z", action_class="money.purchase", amount_minor=9000)
    assert freed(unspent, refund("a" * 64, 9000)) == {}


def test_an_unaccepted_charge_frees_nothing() -> None:
    assert freed(charge(accept=False), refund("a" * 64, 9000)) == {}


def test_a_refund_under_another_operator_frees_nothing() -> None:
    assert freed(charge(), refund("a" * 64, 2000, operator="other")) == {}


def test_a_refund_sealed_after_the_check_frees_nothing() -> None:
    assert freed(charge(), refund("a" * 64, 2000, at="2026-10-08T00:00:00Z")) == {}


def test_an_unread_check_time_links_nothing() -> None:
    assert _history_freed([charge(), refund("a" * 64, 2000)], REVERSAL, None) == {}


def test_a_cancel_with_a_returned_amount_frees_it() -> None:
    cancel = act("d" * 64, "2026-10-06T12:00:00Z", action_class="booking.cancel", direction="in",
                 cancelled_amount_minor=1500, parent=CHARGE)
    assert freed(charge(), cancel) == {CHARGE: 1500}


def test_a_refund_with_no_returned_amount_or_a_bad_chain_frees_nothing() -> None:
    none = act("a" * 64, "2026-10-06T12:00:00Z", action_class="money.refund", direction="in", parent=CHARGE)
    malformed = refund("b" * 64, 2000)
    malformed["chain"] = {"parent_capsule_id": 7, "relation": "supersedes"}
    assert freed(charge(), none, malformed) == {}
