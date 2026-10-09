# SPDX-License-Identifier: Apache-2.0
"""``dedupe`` keys a refund on the money it returns, never on its spend.

A deal check for money moving in (``direction: in``, a refund or a partial
cancel) is spend ``0``, so the caps fold never counts it. Its act key reads
the amount it returns (``returned_minor``) and the act it reverses
(``reverses_ref``), both sealed on the decision only when the money moves in.
So two partial refunds of different amounts to one payee both pass, the
same refund of the same act twice in the window is refused, and the weekly
spend total does not move.

Without ``reverses_ref`` the key is the class, the target and the returned
amount alone: two equal refunds to one payee in the window collide, and so do
two equal refunds whose checks seal no payee fingerprint. A producer that
states ``reverses_ref`` on the check separates them.
"""
from __future__ import annotations

from pathlib import Path
from typing import NotRequired, TypedDict

import pytest
from agent_action_capsule import json_digest

from capsule_engine.folds.loader import load_definition_file as load_fold
from capsule_engine.guards.capsule import DENY
from capsule_engine.guards.checks.dedupe import equivalence_key_for_action, equivalence_key_for_capsule
from capsule_engine.report.replay import action_for_record, replay

SPEND_WEEKLY = Path(__file__).parent.parent / "capsule_engine" / "folds" / "catalog_defs" / "spend.weekly.yaml"

ALG = "hmac-sha256-deal-key"
PAYEE = "a" * 64
PURCHASE = "1" * 64
OTHER_PURCHASE = "2" * 64
DAY_1 = "2026-10-07T12:00:00Z"
DAY_2 = "2026-10-08T12:00:00Z"
DAY_3 = "2026-10-09T12:00:00Z"


class TypedRef(TypedDict):
    type: str
    digest_alg: str
    digest: str


class CheckBody(TypedDict):
    action: str
    action_class: str
    taxonomy_version: str
    currency: str
    spend_minor: int
    direction: str
    amount_minor: NotRequired[int]
    cancelled_amount_minor: NotRequired[int]
    returned_minor: NotRequired[int]
    reverses_ref: NotRequired[TypedRef]


class Counterparty(TypedDict):
    fp_alg: str
    ids: dict[str, str]


class DealBlock(TypedDict):
    record_type: str
    deal_id: str
    seq: int
    counterparty: NotRequired[Counterparty]


CheckRecord = TypedDict("CheckRecord", {"body": CheckBody, "x-deal-v0": DealBlock})


class BoundCapsule(TypedDict):
    capsule_id: str
    action_id: str
    action_type: str
    operator: str
    developer: str
    timestamp: str
    model_attestation: dict[str, dict[str, str]]


def _ref(digest: str) -> TypedRef:
    return {"type": "deal-record", "digest_alg": "SHA-256", "digest": digest}


def _bound(seq: int, at: str, body: CheckBody, *, payee: str | None = PAYEE) -> tuple[BoundCapsule, CheckRecord]:
    block: DealBlock = {"record_type": "check", "deal_id": "deal-0000000000000000", "seq": seq}
    if payee is not None:
        block["counterparty"] = {"fp_alg": ALG, "ids": {"payee": payee}}
    record: CheckRecord = {"body": body, "x-deal-v0": block}
    capsule: BoundCapsule = {
        "capsule_id": f"{seq:064x}",
        "action_id": f"deal-0000000000000000/{seq}",
        "action_type": "fyi",
        "operator": "household-a",
        "developer": "capsulectl-deal",
        "timestamp": at,
        "model_attestation": {"compute_attestation": {"agent_input_digest": json_digest(record)}},
    }
    return capsule, record


def _refund(
    seq: int, at: str, amount: int, *, reverses: str | None = PURCHASE, payee: str | None = PAYEE
) -> tuple[BoundCapsule, CheckRecord]:
    body: CheckBody = {
        "action": "cancel",
        "action_class": "money.refund",
        "taxonomy_version": "2",
        "currency": "USD",
        "amount_minor": amount,
        "spend_minor": 0,
        "direction": "in",
    }
    if reverses is not None:
        body["reverses_ref"] = _ref(reverses)
    return _bound(seq, at, body, payee=payee)


def _purchase(seq: int, at: str, amount: int) -> tuple[BoundCapsule, CheckRecord]:
    body: CheckBody = {
        "action": "pay",
        "action_class": "money.purchase",
        "taxonomy_version": "2",
        "currency": "USD",
        "amount_minor": amount,
        "spend_minor": amount,
        "direction": "out",
    }
    return _bound(seq, at, body)


def _replay(*checks: tuple[BoundCapsule, CheckRecord], caps_minor: dict[str, int] | None = None):
    result = replay(
        [capsule for capsule, _ in checks],
        caps_fold=load_fold(SPEND_WEEKLY),
        caps_minor=caps_minor,
        disclosed={capsule["capsule_id"]: record for capsule, record in checks},
    )
    return [sourced.decision for sourced in result.decisions]


def _dedupe(decision):
    return next(c for c in decision.constraints if c.id == "dedupe")


def test_two_partial_refunds_of_different_amounts_to_one_payee_both_pass():
    first, second = _replay(_refund(2, DAY_1, 1_000), _refund(3, DAY_2, 2_500))
    assert (_dedupe(first).result, _dedupe(second).result) == ("pass", "pass")


def test_the_same_refund_of_the_same_act_twice_in_the_window_is_refused():
    first, second = _replay(_refund(2, DAY_1, 1_000), _refund(3, DAY_2, 1_000))
    assert (_dedupe(second).result, second.outcome) == ("fail", DENY)
    assert _dedupe(second).evidence["matched_capsule_id"] == first.capsule["capsule_id"]


def test_equal_refunds_reversing_different_acts_both_pass():
    _, second = _replay(_refund(2, DAY_1, 1_000), _refund(3, DAY_2, 1_000, reverses=OTHER_PURCHASE))
    assert _dedupe(second).result == "pass"


def test_without_reverses_ref_equal_refunds_to_one_payee_collide():
    _, second = _replay(_refund(2, DAY_1, 1_000, reverses=None), _refund(3, DAY_2, 1_000, reverses=None))
    assert (_dedupe(second).result, second.outcome) == ("fail", DENY)


def test_without_reverses_ref_or_a_payee_equal_refunds_collide_across_merchants():
    """The stated limit: nothing on the check tells the merchants apart."""
    _, second = _replay(
        _refund(2, DAY_1, 1_000, reverses=None, payee=None), _refund(3, DAY_2, 1_000, reverses=None, payee=None)
    )
    assert _dedupe(second).result == "fail"


def test_a_repeated_purchase_is_still_refused():
    _, second = _replay(_purchase(2, DAY_1, 4_500), _purchase(3, DAY_2, 4_500))
    assert (_dedupe(second).result, second.outcome) == ("fail", DENY)


def test_a_refund_never_matches_a_purchase_of_the_same_amount():
    _, refund = _replay(_purchase(2, DAY_1, 1_000), _refund(3, DAY_2, 1_000))
    assert _dedupe(refund).result == "pass"


def test_a_refund_is_spend_zero_and_the_weekly_total_ignores_what_it_returns():
    purchase, refund, again = _replay(
        _purchase(2, DAY_1, 4_500),
        _refund(3, DAY_2, 1_000),
        _purchase(4, DAY_3, 700),
        caps_minor={"money.purchase": 1_000_000, "money.refund": 1_000_000},
    )
    assert refund.capsule["asg_payload"]["amount_minor"] == 0
    caps = next(c for c in again.constraints if c.id == "caps")
    assert caps.evidence["weekly_spend_minor"] == 4_500


def test_returned_minor_and_reverses_ref_are_sealed_only_when_money_moves_in():
    (refund,) = _replay(_refund(2, DAY_1, 1_000))
    (purchase,) = _replay(_purchase(2, DAY_1, 4_500))
    payload = refund.capsule["asg_payload"]
    assert (payload["returned_minor"], payload["reverses_ref"]) == (1_000, PURCHASE)
    assert "returned_minor" not in purchase.capsule["asg_payload"]
    assert "reverses_ref" not in purchase.capsule["asg_payload"]


@pytest.mark.parametrize(
    ("amounts", "returned"),
    [
        ({"amount_minor": 1_000}, 1_000),
        ({"cancelled_amount_minor": 30_000}, 30_000),
        ({"amount_minor": 1_000, "cancelled_amount_minor": 30_000}, 1_000),
        ({"returned_minor": 900, "amount_minor": 1_000, "cancelled_amount_minor": 30_000}, 900),
    ],
    ids=["refund", "partial-cancel", "amount-wins-over-cancelled", "stated-returned-wins"],
)
def test_the_returned_amount_comes_from_the_first_stated_field(amounts, returned):
    capsule, record = _refund(2, DAY_1, 0)
    body = {k: v for k, v in record["body"].items() if k != "amount_minor"}
    record = {**record, "body": {**body, **amounts}}
    capsule = {**capsule, "model_attestation": {"compute_attestation": {"agent_input_digest": json_digest(record)}}}
    action = action_for_record(capsule, record)
    assert (action.returned_minor, action.amount_minor) == (returned, 0)


def test_money_out_never_carries_a_returned_amount_or_a_reversed_act():
    capsule, record = _purchase(2, DAY_1, 4_500)
    record = {**record, "body": {**record["body"], "reverses_ref": _ref(PURCHASE), "returned_minor": 5}}
    capsule = {**capsule, "model_attestation": {"compute_attestation": {"agent_input_digest": json_digest(record)}}}
    action = action_for_record(capsule, record)
    assert (action.returned_minor, action.reverses_ref) == (None, None)


def test_the_decision_on_a_refund_recomputes_the_key_of_the_refund():
    capsule, record = _refund(2, DAY_1, 1_000)
    (decision,) = _replay((capsule, record))
    assert equivalence_key_for_capsule(decision.capsule) == equivalence_key_for_action(action_for_record(capsule, record))


def test_a_purchase_keeps_the_key_it_had_before_refunds_were_keyed():
    """No money-in field set, so the pinned key is the one #106 defined."""
    action = action_for_record(*_purchase(2, DAY_1, 4_500))
    assert equivalence_key_for_action(action) == json_digest(
        {
            "operator": "household-a",
            "developer": "capsulectl-deal",
            "action_class": "money.purchase",
            "target": f"payee-fp:{ALG}:{PAYEE}",
            "amount_minor": 4_500,
        }
    )
