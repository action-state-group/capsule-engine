# SPDX-License-Identifier: Apache-2.0
"""The four consumer COMMIT classes the everyday pack gates name an approver,
``account_holder``, so a failure that may ask an approver asks instead of
refusing (D2, ``guards/engine.py`` ``_decide``).

Before taxonomy version 3, money.purchase had no approver role: r06's
first-contact failure denied every first purchase at a new merchant, though
r06 declares ASK. Taxonomy 4 names the same approver on four more classes,
the ones the pack's other ASK gate rules flag, and taxonomy 5 on
communication.send, the class r13's first-message check flags. Integrity failures (dedupe, verify_before_dispatch) still
deny, alone or beside a failure that could ask.

Purchases run through a pack-installed engine, each on a fresh ledger.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from capsule_ledger.ledger import LedgerStore

from capsule_engine.guards import Action, GuardEngine, LocalSigner
from capsule_engine.guards.capsule import ALLOW, DENY, ESCALATE
from capsule_engine.guards.classes import TAXONOMY, TAXONOMY_VERSION
from capsule_engine.packs import build_engine, install_pack, load_pack_dir

PACK_DIR = Path(__file__).parent.parent / "capsule_engine" / "packs" / "catalog" / "everyday"
PER_ACTION_MINOR = 2_500  # caps/5.0.0's per-action default, cited by the pack
ACCOUNT_HOLDER_CLASSES = ("money.purchase", "money.subscription", "booking.create", "booking.modify")
# Taxonomy 4: the classes the everyday pack's remaining ASK gate rules flag
# (r11 r12 r15 r17 r18; tests/test_gate_rules_declared_ask.py).
GATED_ACCOUNT_HOLDER_CLASSES = ("booking.cancel", "data.delete", "communication.publish", "disclosure.personal")
# Taxonomy 5: r13 (recipient_seen_before) declares ASK on a message to a new
# recipient (tests/test_ask_declared_wickets.py).
MESSAGE_ACCOUNT_HOLDER_CLASSES = ("communication.send",)
OPERATOR = "household-account-holder"
SIGNER = LocalSigner(key_id="everyday-account-holder-key", secret=b"everyday-account-holder-fixed-key")
# A real accepted payment to the bakery makes it a known merchant.
KNOWN_MERCHANT = "shop/bakery"


def _purchase(n: int, target: str, amount_minor: int, **fields) -> Action:
    return Action(
        verb="make_purchase",
        operator=OPERATOR,
        developer="household-assistant-ah@v1",
        action_class="money.purchase",
        currency="EUR",
        rail="card",
        target=target,
        amount_minor=amount_minor,
        action_id=f"make_purchase/everyday-account-holder-{n}",
        timestamp=f"2026-08-10T12:{n:02d}:00Z",
        **fields,
    )


def _history_payment() -> Action:
    return Action(
        verb="make_payment",
        operator=OPERATOR,
        developer="household-assistant-ah@v1",
        action_class="money.transfer",
        currency="EUR",
        rail="card",
        target=KNOWN_MERCHANT,
        amount_minor=700,
        counterparty_account_ref="acct-ref-bakery-ah",
        recurrence="one_time",
        action_id="make_payment/everyday-account-holder-history",
        timestamp="2026-08-10T12:00:00Z",
    )


@pytest.fixture
def engine(tmp_path):
    store = LedgerStore(tmp_path / "ledger")
    installed = install_pack(load_pack_dir(PACK_DIR), project_dir=tmp_path / "project", mode="observe")
    yield build_engine(installed, ledger=store, signer_provider=lambda: SIGNER)
    store.close()


def _failing(decision) -> list[str]:
    return [c.id for c in decision.constraints if c.result == "fail"]


def test_exactly_the_consumer_commit_and_gated_classes_name_the_account_holder():
    assert TAXONOMY_VERSION == "5"
    named = {name: ac.approver_role for name, ac in TAXONOMY.items() if ac.approver_role is not None}
    assert named == {
        **dict.fromkeys(
            ACCOUNT_HOLDER_CLASSES + GATED_ACCOUNT_HOLDER_CLASSES + MESSAGE_ACCOUNT_HOLDER_CLASSES, "account_holder"
        ),
        "money.transfer": "treasury-approver",
    }


def test_a_first_purchase_at_an_unseen_merchant_under_the_caps_asks(engine):
    decision = engine.check(_purchase(1, "shop/garden-centre", 1_800))
    assert decision.outcome == ESCALATE
    assert _failing(decision) == ["counterparty_seen_before"]
    assert decision.capsule["disposition"]["decision"] == "needs_input"
    assert decision.capsule["disposition"]["verdict_class"] == "hitl_dispatched"


def test_an_over_limit_purchase_at_a_known_merchant_asks(engine):
    assert engine.check(_history_payment()).outcome == ALLOW
    decision = engine.check(_purchase(2, KNOWN_MERCHANT, PER_ACTION_MINOR + 1))
    assert decision.outcome == ESCALATE
    assert _failing(decision) == ["caps"]


def test_a_repeated_purchase_at_a_known_merchant_denies(engine):
    assert engine.check(_history_payment()).outcome == ALLOW
    assert engine.check(_purchase(3, KNOWN_MERCHANT, 900)).outcome == ALLOW
    decision = engine.check(_purchase(3, KNOWN_MERCHANT, 900))
    assert decision.outcome == DENY
    assert _failing(decision) == ["dedupe"]


def test_a_purchase_citing_a_missing_mandate_denies(engine):
    assert engine.check(_history_payment()).outcome == ALLOW
    decision = engine.check(_purchase(4, KNOWN_MERCHANT, 900, cited_mandate_capsule_id="0" * 64))
    assert decision.outcome == DENY
    assert _failing(decision) == ["verify_before_dispatch"]


def test_a_repeated_first_purchase_denies_though_first_contact_alone_would_ask(engine):
    # The first try is a dry run: it is on the ledger for dedupe, but it does
    # not make the merchant known.
    assert engine.check(_purchase(5, "shop/garden-centre", 1_800), dry_run=True).outcome == ESCALATE
    decision = engine.check(_purchase(5, "shop/garden-centre", 1_800))
    assert decision.outcome == DENY
    assert sorted(_failing(decision)) == ["counterparty_seen_before", "dedupe"]


@pytest.mark.parametrize("action_class", ACCOUNT_HOLDER_CLASSES)
def test_an_over_limit_action_in_each_class_asks_without_the_pack(store, caps_fold, signer, action_class):
    engine = GuardEngine(ledger=store, caps_fold=caps_fold, signer_provider=lambda: signer,
                         caps_minor={action_class: 1_000})
    action = Action(verb="commit", operator=OPERATOR, developer="household-assistant-ah@v1",
                    action_class=action_class, amount_minor=1_001, target="venue/first-contact")
    decision = engine.check(action)
    assert decision.outcome == ESCALATE
    assert _failing(decision) == ["caps"]


def test_an_over_limit_refund_still_denies_without_an_approver(store, caps_fold, signer):
    engine = GuardEngine(ledger=store, caps_fold=caps_fold, signer_provider=lambda: signer,
                         caps_minor={"money.refund": 1_000})
    action = Action(verb="refund", operator=OPERATOR, developer="household-assistant-ah@v1",
                    action_class="money.refund", amount_minor=1_001, target="shop/garden-centre")
    decision = engine.check(action)
    assert decision.outcome == DENY
    assert _failing(decision) == ["caps"]
