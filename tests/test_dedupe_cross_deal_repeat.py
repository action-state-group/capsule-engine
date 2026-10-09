# SPDX-License-Identifier: Apache-2.0
"""An identical repeat of a checked act: refused inside one deal, asked about
across deals.

The deal is the ``x-deal-v0.deal_id`` the check's own sealed record states,
carried onto the action as ``deal_id`` and sealed on the decision. A repeat in
the same deal is a double commit and is refused, naming the earlier act. The
same act in another deal inside the window asks the class's approver; a class
with no approver refuses it. Outside the window it passes.

A repeat across deals can be with another merchant, and a checker's reason can
reach the current merchant's copy. So its reason names nothing from the earlier
act: the earlier act's capsule id, amount and time are in the evidence, which
is sealed only as a digest, and no chain link joins the two deals.
"""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import TypedDict

from agent_action_capsule import json_digest

from capsule_engine.folds.loader import load_definition_file as load_fold
from capsule_engine.guards.capsule import ALLOW, DENY, ESCALATE
from capsule_engine.guards.checks.dedupe import equivalence_key_for_action
from capsule_engine.guards.classes import TAXONOMY_VERSION
from capsule_engine.report.replay import action_for_record, replay

SPEND_WEEKLY = Path(__file__).parent.parent / "capsule_engine" / "folds" / "catalog_defs" / "spend.weekly.yaml"

ALG = "hmac-sha256-deal-key"
PAYEE = "a" * 64
DEAL_A = "deal-aaaaaaaaaaaaaaaa"
DEAL_B = "deal-bbbbbbbbbbbbbbbb"
DAY_1 = "2026-10-07T12:00:00Z"
DAY_2 = "2026-10-08T12:00:00Z"
DAY_3 = "2026-10-09T12:00:00Z"
DAY_40 = "2026-11-16T12:00:00Z"


class CheckBody(TypedDict):
    action: str
    action_class: str
    taxonomy_version: str
    currency: str
    amount_minor: int
    spend_minor: int
    direction: str


class Counterparty(TypedDict):
    fp_alg: str
    ids: dict[str, str]


class DealBlock(TypedDict, total=False):
    record_type: str
    deal_id: str
    seq: int
    counterparty: Counterparty


CheckRecord = TypedDict("CheckRecord", {"body": CheckBody, "x-deal-v0": DealBlock})


class BoundCapsule(TypedDict):
    capsule_id: str
    action_id: str
    action_type: str
    operator: str
    developer: str
    timestamp: str
    model_attestation: dict[str, dict[str, str]]


_SEQ = iter(range(1, 1_000))


def _check(deal: str | None, at: str, *, action_class: str = "money.purchase", amount: int = 4_500):
    seq = next(_SEQ)
    direction = "in" if action_class == "money.refund" else "out"
    body: CheckBody = {
        "action": "pay",
        "action_class": action_class,
        "taxonomy_version": TAXONOMY_VERSION,
        "currency": "USD",
        "amount_minor": amount,
        "spend_minor": 0 if direction == "in" else amount,
        "direction": direction,
    }
    block: DealBlock = {"record_type": "check", "seq": seq, "counterparty": {"fp_alg": ALG, "ids": {"payee": PAYEE}}}
    if deal is not None:
        block["deal_id"] = deal
    record: CheckRecord = {"body": body, "x-deal-v0": block}
    capsule: BoundCapsule = {
        "capsule_id": f"{seq:064x}",
        # One action_id prefix for every deal: the deal is never read from it.
        "action_id": f"deal-0000000000000000/{seq}",
        "action_type": "fyi",
        "operator": "household-a",
        "developer": "capsulectl-deal",
        "timestamp": at,
        "model_attestation": {"compute_attestation": {"agent_input_digest": json_digest(record)}},
    }
    return capsule, record


def _replay(*checks):
    result = replay(
        [capsule for capsule, _ in checks],
        caps_fold=load_fold(SPEND_WEEKLY),
        disclosed={capsule["capsule_id"]: record for capsule, record in checks},
    )
    return [sourced.decision for sourced in result.decisions]


def _dedupe(decision):
    return next(c for c in decision.constraints if c.id == "dedupe")


# -- the deal, from the sealed record ----------------------------------------------


def test_the_deal_comes_from_the_sealed_deal_record_and_is_sealed_on_the_decision():
    capsule, record = _check(DEAL_A, DAY_1)
    assert action_for_record(capsule, record).deal_id == DEAL_A
    (decision,) = _replay((capsule, record))
    assert decision.capsule["asg_payload"]["deal_id"] == DEAL_A


def test_a_record_stating_no_deal_seals_none():
    (decision,) = _replay(_check(None, DAY_1))
    assert "deal_id" not in decision.capsule["asg_payload"]


def test_the_deal_never_enters_the_act_key():
    a = action_for_record(*_check(DEAL_A, DAY_1))
    b = action_for_record(*_check(DEAL_B, DAY_1))
    assert equivalence_key_for_action(a) == equivalence_key_for_action(b)


# -- the four acceptance cases ---------------------------------------------------


def test_a_repeat_in_the_same_deal_is_refused_naming_the_earlier_act():
    first, second = _replay(_check(DEAL_A, DAY_1), _check(DEAL_A, DAY_2))
    out = _dedupe(second)
    assert (out.result, second.outcome) == ("fail", DENY)
    earlier = first.capsule["capsule_id"]
    assert earlier[:16] in out.reason
    assert out.evidence["matched_capsule_id"] == earlier
    assert second.capsule["chain"]["parent_capsule_id"] == earlier


def test_the_same_act_in_another_deal_in_the_window_asks_the_approver():
    first, second = _replay(_check(DEAL_A, DAY_1), _check(DEAL_B, DAY_2))
    out = _dedupe(second)
    assert (out.result, second.outcome) == ("fail", ESCALATE)
    assert out.evidence["repeat"] == "other_deal"
    assert out.evidence["matched_capsule_id"] == first.capsule["capsule_id"]
    assert out.evidence["matched_amount_minor"] == 4_500
    assert out.evidence["matched_currency"] == "USD"
    assert out.evidence["matched_at"] == DAY_1


def test_the_same_act_in_another_deal_with_no_approver_is_refused_naming_the_class():
    _, second = _replay(
        _check(DEAL_A, DAY_1, action_class="money.refund"), _check(DEAL_B, DAY_2, action_class="money.refund")
    )
    assert (_dedupe(second).result, second.outcome) == ("fail", DENY)
    assert "'money.refund'" in second.reason
    assert "approver_role" in second.reason


def test_outside_the_window_the_same_act_passes_in_either_deal():
    _, other = _replay(_check(DEAL_A, DAY_1), _check(DEAL_B, DAY_40))
    _, same = _replay(_check(DEAL_A, DAY_1), _check(DEAL_A, DAY_40))
    assert (_dedupe(other).result, other.outcome) == ("pass", ALLOW)
    assert (_dedupe(same).result, same.outcome) == ("pass", ALLOW)


# -- a same-deal match always wins -------------------------------------------------


def test_a_repeat_matching_both_its_own_deal_and_another_is_refused():
    """Deal A's act, then deal B's (asked about), then deal B's again: the
    third matches deal A and deal B both, and the same-deal match refuses it."""
    _, asked, third = _replay(_check(DEAL_A, DAY_1), _check(DEAL_B, DAY_2), _check(DEAL_B, DAY_3))
    assert asked.outcome == ESCALATE
    assert (_dedupe(third).result, third.outcome) == ("fail", DENY)
    assert _dedupe(third).evidence["matched_capsule_id"] == asked.capsule["capsule_id"]


def test_a_repeat_where_either_side_states_no_deal_is_refused():
    """No sealed deal on one side: nothing shows the deals differ."""
    _, unstated_now = _replay(_check(DEAL_A, DAY_1), _check(None, DAY_2))
    _, unstated_before = _replay(_check(None, DAY_1), _check(DEAL_B, DAY_2))
    assert unstated_now.outcome == DENY
    assert unstated_before.outcome == DENY


# -- privacy -------------------------------------------------------------------------


def _earlier_values(first) -> tuple[str, ...]:
    capsule_id = first.capsule["capsule_id"]
    return (capsule_id, capsule_id[:16], "4500", "45.00", DAY_1, "2026-10-07", PAYEE, DEAL_A)


def test_a_cross_deal_repeat_carries_no_earlier_act_value_out_of_its_evidence():
    """The reason, the decision summary, the sealed constraint record and the
    decision's chain hold nothing from the earlier act: not its capsule id,
    amount, time, payee or deal."""
    first, second = _replay(_check(DEAL_A, DAY_1), _check(DEAL_B, DAY_2))
    out = _dedupe(second)
    (record,) = [c for c in second.capsule["constraints"] if c["id"] == "dedupe"]
    outside_evidence = {**dataclasses.asdict(out), "evidence": None}
    sealed = {k: v for k, v in record.items() if k != "evidence_digest"}
    serialised = json.dumps([outside_evidence, sealed, second.reason, second.capsule.get("chain")])
    for value in _earlier_values(first):
        assert value not in serialised, value
    assert record["evidence_digest"] == json_digest(out.evidence)
    assert "chain" not in second.capsule


def test_a_repeat_against_an_earlier_act_stating_no_deal_names_nothing_from_it():
    """Refused, but the earlier act is not shown to be in this deal's chain."""
    first, second = _replay(_check(None, DAY_1), _check(DEAL_B, DAY_2))
    out = _dedupe(second)
    assert second.outcome == DENY
    serialised = json.dumps([{**dataclasses.asdict(out), "evidence": None}, second.reason, second.capsule.get("chain")])
    for value in _earlier_values(first):
        assert value not in serialised, value
