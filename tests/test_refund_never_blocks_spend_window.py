# SPDX-License-Identifier: Apache-2.0
"""A refund or a partial cancel never blocks the spend window.

A deal check that states the money moved in (``direction: "in"``, the
bridge's ``MONEY_IN``) is never spend: the bridge carries its spend as ``0``.
So a refund recorded before a purchase leaves the purchase's ``caps`` result
evaluated, a pass or a fail and never ``n/a``, and leaves the rolling total
where the earlier purchases put it.

The synthetic refunds here state a ``spend_minor`` above zero, as a producer
error might, so a bridge that read it would move the total. The real
capsulectl bundles seal ``spend_minor: 0`` on a money-in check; those are
replayed to show their checks are bridged the same way.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from agent_action_capsule import json_digest

from capsule_engine.folds.loader import load_definition_file
from capsule_engine.guards import Action, GuardEngine
from capsule_engine.guards.classes import TAXONOMY_VERSION
from capsule_engine.report.replay import MONEY_IN, action_for_record, load_disclosed, load_records, replay

FOLDS = Path(__file__).parent.parent / "capsule_engine" / "folds" / "catalog_defs"
BUNDLES = Path(__file__).parent / "fixtures" / "deal-bundles"

PURCHASE = 300
REFUND = 500  # the money-in check's stated spend_minor, which is never spend
LATER = 200

# A refund, and a partial cancel as capsulectl classes it.
MONEY_IN_BODIES = {
    "refund": {"action": "cancel", "action_class": "money.refund", "amount_minor": REFUND},
    "partial_cancel": {"action": "cancel", "action_class": "external_commitment.other", "cancelled_amount_minor": REFUND},
}
CAPPED_CLASSES = ("money.purchase", "money.refund", "external_commitment.other")


def _engine(store, signer, *, window: int) -> GuardEngine:
    return GuardEngine(
        ledger=store,
        caps_fold=load_definition_file(FOLDS / "spend.weekly.v3.yaml"),
        signer_provider=lambda: signer,
        caps_minor=dict.fromkeys(CAPPED_CLASSES, window),
    )


def _purchase(n: int, amount: int) -> Action:
    return Action(
        verb="buy",
        operator="household-a",
        developer="shopping-assistant@v1",
        action_class="money.purchase",
        action_id=f"buy/{n}",
        amount_minor=amount,
        currency="USD",
        target=f"shop/{n}",
        timestamp=f"2026-10-0{n}T10:00:00Z",
    )


def _money_in(kind: str) -> Action:
    """The action the bridge reads from a bound money-in deal check of ``kind``."""
    record = {
        "body": {
            **MONEY_IN_BODIES[kind],
            "taxonomy_version": TAXONOMY_VERSION,
            "currency": "USD",
            "direction": MONEY_IN,
            "spend_minor": REFUND,
        },
        "x-deal-v0": {"record_type": "check", "deal_id": "deal-0000000000000000", "seq": 7},
    }
    capsule = {
        "capsule_id": "0" * 64,
        "action_id": "deal-0000000000000000/7",
        "action_type": "fyi",
        "operator": "household-a",
        "developer": "capsulectl-deal",
        "timestamp": "2026-10-02T10:00:00Z",
        "model_attestation": {"compute_attestation": {"agent_input_digest": json_digest(record)}},
    }
    return action_for_record(capsule, record)


def _caps(decision):
    (caps,) = [c for c in decision.constraints if c.id == "caps"]
    return caps


@pytest.mark.parametrize("kind", sorted(MONEY_IN_BODIES))
def test_a_money_in_check_is_bridged_as_a_spend_of_zero(kind):
    assert _money_in(kind).amount_minor == 0


@pytest.mark.parametrize("kind", sorted(MONEY_IN_BODIES))
def test_a_purchase_after_a_money_in_act_passes_with_the_total_unchanged(store, signer, kind):
    engine = _engine(store, signer, window=1_000)
    assert engine.check(_purchase(1, PURCHASE)).outcome == "allow"
    money_in = _caps(engine.check(_money_in(kind)))
    assert (money_in.result, money_in.evidence["amount_minor"], money_in.evidence["weekly_spend_minor"]) == ("pass", 0, PURCHASE)

    caps = _caps(engine.check(_purchase(3, LATER)))
    assert caps.result == "pass"
    assert caps.evidence["weekly_spend_minor"] == PURCHASE
    assert caps.evidence["projected_minor"] == PURCHASE + LATER


@pytest.mark.parametrize("kind", sorted(MONEY_IN_BODIES))
def test_a_purchase_over_the_limit_after_a_money_in_act_still_fails_the_window(store, signer, kind):
    engine = _engine(store, signer, window=PURCHASE + LATER - 1)
    assert engine.check(_purchase(1, PURCHASE)).outcome == "allow"
    assert _caps(engine.check(_money_in(kind))).result == "pass"

    caps = _caps(engine.check(_purchase(3, LATER)))
    assert caps.result == "fail"
    assert caps.evidence["weekly_spend_minor"] == PURCHASE
    assert caps.evidence["projected_minor"] == PURCHASE + LATER


@pytest.mark.parametrize("bundle", ["deal-purchase-then-refund", "deal-purchase-then-partial-cancel"])
def test_a_real_money_in_check_is_decided_with_its_caps_evaluated_at_zero(bundle):
    path = BUNDLES / f"{bundle}.bundle.json"
    result = replay(
        load_records([path]),
        caps_fold=load_definition_file(FOLDS / "spend.weekly.v3.yaml"),
        caps_minor=dict.fromkeys(CAPPED_CLASSES, 100_000),
        disclosed=load_disclosed([path]),
    )
    shown = load_disclosed([path])
    money_in = [s for s in result.decisions if shown[s.record["capsule_id"]]["body"].get("direction") == MONEY_IN]
    assert len(money_in) == 1
    caps = _caps(money_in[0].decision)
    assert (caps.result, caps.evidence["amount_minor"]) == ("pass", 0)
