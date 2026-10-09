# SPDX-License-Identifier: Apache-2.0
"""An ``action_class_gate`` failure asks an approver when every failing
selector's obligations declare ``default_disposition: ASK`` (D2,
``guards/engine.py`` ``_decide``).

Before this, the gate was never escalatable: r16 declares ASK, yet every
booking under everyday 0.3.2 was refused, and so was every other ASK-declared
gate rule (r11 r12 r15 r17 r18). Now the engine reads the declared
disposition: a failing selector bound to a NEVER obligation still refuses,
and so does a class with no ``approver_role``, whose refusal names it.

Taxonomy 4 names the account holder as approver on the four classes those
rules gate (booking.cancel, data.delete, communication.publish,
disclosure.personal), so each of them now asks too.

Bookings run through a pack-installed engine, each on a fresh ledger; the
mixed and unbound-selector cases build the engine directly.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from capsule_ledger.ledger import LedgerStore

from capsule_engine.guards import Action, GuardEngine, LocalSigner
from capsule_engine.guards.capsule import DENY, ESCALATE
from capsule_engine.guards.checks.action_class_gate import Selector
from capsule_engine.guards.wickets.definition import WicketDefinition
from capsule_engine.packs import build_engine, install_pack, load_pack_dir
from capsule_engine.packs.obligation_results import obligation_results
from capsule_engine.packs.schema import PackDefinition

PACK_DIR = Path(__file__).parent.parent / "capsule_engine" / "packs" / "catalog" / "everyday"
PACK = load_pack_dir(PACK_DIR)
PACK_0_3_2_DIGEST = "6f333fa8b7a7e137abe6c61e5a32097ed06d493479a018807cbf4d2e4f5da7b2"
OPERATOR = "household-gate-asks"
SIGNER = LocalSigner(key_id="everyday-gate-asks-key", secret=b"everyday-gate-asks-fixed-key")
CAPS_FOLD = next(f for f in PACK.folds if f.fold_id.startswith("spend.weekly/"))


def _action(n: int, action_class: str, **fields) -> Action:
    return Action(
        verb="act",
        operator=OPERATOR,
        developer="household-assistant-ga@v1",
        action_class=action_class,
        action_id=f"act/everyday-gate-asks-{n}",
        timestamp=f"2026-08-10T13:{n:02d}:00Z",
        **fields,
    )


def _booking(n: int) -> Action:
    return _action(n, "booking.create", amount_minor=2_000, currency="EUR", target="venue/restaurant")


def _with_disposition(pack: PackDefinition, rule: str, disposition: str) -> PackDefinition:
    obligations = tuple(replace(o, default_disposition=disposition) if o.id == rule else o for o in pack.obligations)
    return replace(pack, obligations=obligations)


@pytest.fixture
def engine_for(tmp_path):
    stores = []

    def build(pack: PackDefinition) -> GuardEngine:
        n = len(stores)
        store = LedgerStore(tmp_path / f"ledger-{n}")
        stores.append(store)
        installed = install_pack(pack, project_dir=tmp_path / f"project-{n}", mode="observe")
        return build_engine(installed, ledger=store, signer_provider=lambda: SIGNER)

    yield build
    for store in stores:
        store.close()


def _failing_rules(decision) -> list[str]:
    return [r.obligation_id for r in obligation_results(PACK, decision.constraints) if r.result == "fail"]


def test_the_pack_this_runs_against_is_everyday_0_3_2_at_its_vendored_digest():
    assert PACK.pack_id == "asg/everyday/0.3.2"
    assert PACK.definition_digest() == PACK_0_3_2_DIGEST


def test_a_booking_asks_the_account_holder_citing_r16_only(engine_for):
    decision = engine_for(PACK).check(_booking(1), dry_run=True)
    assert decision.outcome == ESCALATE
    assert [c.id for c in decision.constraints if c.result == "fail"] == ["action_class_gate"]
    assert _failing_rules(decision) == ["r16-booking-with-a-commitment"]


def test_a_never_declared_gate_rule_still_refuses(engine_for):
    pack = _with_disposition(PACK, "r16-booking-with-a-commitment", "NEVER")
    decision = engine_for(pack).check(_booking(2), dry_run=True)
    assert decision.outcome == DENY
    assert "approver" not in decision.reason


@pytest.mark.parametrize(
    ("action_class", "fields", "rules"),
    [
        ("booking.cancel", {}, ["r17-cancellation"]),
        ("data.delete", {}, ["r18-delete-persistent-data"]),
        ("communication.publish", {}, ["r15-public-posting"]),
        # A shipping address shared with the merchant that fulfils the order:
        # recipient_role passes, so the gate is the only failing check. r11 and
        # r12 share the personal_disclosure selector, so both are cited.
        (
            "disclosure.personal",
            {"recipient_role": "fulfilling_merchant", "target": "shop/bakery"},
            ["r11-home-address-to-an-individual", "r12-personal-contact-to-a-new-party"],
        ),
    ],
)
def test_an_ask_gate_rule_asks_the_account_holder_citing_only_its_own_rules(engine_for, action_class, fields, rules):
    decision = engine_for(PACK).check(_action(3, action_class, **fields), dry_run=True)
    assert decision.outcome == ESCALATE
    assert decision.capsule["disposition"]["decision"] == "needs_input"
    assert [c.id for c in decision.constraints if c.result == "fail"] == ["action_class_gate"]
    assert _failing_rules(decision) == rules


@pytest.mark.parametrize(
    ("action_class", "rule"),
    [
        ("booking.cancel", "r17-cancellation"),
        ("data.delete", "r18-delete-persistent-data"),
        ("communication.publish", "r15-public-posting"),
        ("disclosure.personal", "r11-home-address-to-an-individual"),
    ],
)
def test_an_ask_gate_rule_re_declared_never_still_refuses(engine_for, action_class, rule):
    pack = _with_disposition(PACK, rule, "NEVER")
    decision = engine_for(pack).check(_action(4, action_class), dry_run=True)
    assert decision.outcome == DENY
    assert "approver" not in decision.reason


def test_an_ask_gate_rule_on_a_class_with_no_approver_refuses_naming_the_missing_approver(tmp_path):
    # external_commitment.other names no approver_role in the taxonomy.
    selectors: dict[str, Selector] = {"asks": {"action_classes": ["external_commitment.other"], "on_match": "fail"}}
    engine, store = _direct_engine(tmp_path, selectors, frozenset({"asks"}))
    try:
        decision = engine.check(_action(10, "external_commitment.other"), dry_run=True)
        assert decision.outcome == DENY
        assert "action class 'external_commitment.other' names no approver_role" in decision.reason
    finally:
        store.close()


def test_a_selector_asks_only_when_every_obligation_bound_to_it_declares_ask(engine_for):
    # r11 and r12 share personal_disclosure: one NEVER among them refuses.
    pack = _with_disposition(PACK, "r12-personal-contact-to-a-new-party", "NEVER")
    assert "personal_disclosure" in engine_for(PACK).ask_gate_selectors
    assert "personal_disclosure" not in engine_for(pack).ask_gate_selectors
    assert "booking_create" in engine_for(pack).ask_gate_selectors


def test_an_ask_gate_failure_beside_an_integrity_failure_refuses(engine_for):
    engine = engine_for(PACK)
    assert engine.check(_booking(4)).outcome == ESCALATE
    # The same booking again is a dedupe hit: integrity, never asks.
    repeat = engine.check(_booking(4), dry_run=True)
    assert {c.id for c in repeat.constraints if c.result == "fail"} == {"action_class_gate", "dedupe"}
    assert repeat.outcome == DENY


TAXONOMY_4_CLASSES = ["booking.cancel", "data.delete", "communication.publish", "disclosure.personal"]


@pytest.mark.parametrize("action_class", TAXONOMY_4_CLASSES)
def test_a_repeat_of_an_asking_class_is_a_dedupe_hit_and_refuses(engine_for, action_class):
    engine = engine_for(PACK)
    action = _action(11, action_class, target="service/repeat")
    assert engine.check(action).outcome == ESCALATE
    repeat = engine.check(action, dry_run=True)
    assert {c.id for c in repeat.constraints if c.result == "fail"} == {"action_class_gate", "dedupe"}
    assert repeat.outcome == DENY


@pytest.mark.parametrize("action_class", TAXONOMY_4_CLASSES)
def test_an_asking_class_citing_a_mandate_not_on_the_ledger_refuses(engine_for, action_class):
    action = _action(12, action_class, cited_mandate_capsule_id="0" * 64)
    decision = engine_for(PACK).check(action, dry_run=True)
    assert {c.id for c in decision.constraints if c.result == "fail"} == {"action_class_gate", "verify_before_dispatch"}
    assert decision.outcome == DENY


def _gate(selectors: dict[str, Selector]) -> WicketDefinition:
    return WicketDefinition(wicket_id="action_class_gate/9.0.0", check="action_class_gate", config={"selectors": selectors})


def _direct_engine(tmp_path, selectors: dict[str, Selector], ask: frozenset[str]) -> tuple[GuardEngine, LedgerStore]:
    store = LedgerStore(tmp_path / "direct")
    engine = GuardEngine(
        ledger=store, caps_fold=CAPS_FOLD, signer_provider=lambda: SIGNER, wickets=(_gate(selectors),),
        ask_gate_selectors=ask,
    )
    return engine, store


TWO_FAIL_SELECTORS: dict[str, Selector] = {
    "asks": {"action_classes": ["booking.create"], "on_match": "fail"},
    "refuses": {"trigger_classes": ["COMMIT"], "on_match": "fail"},
}


def test_mixed_ask_and_never_failing_selectors_refuse(tmp_path):
    engine, store = _direct_engine(tmp_path, TWO_FAIL_SELECTORS, frozenset({"asks"}))
    try:
        assert engine.check(_booking(5), dry_run=True).outcome == DENY
    finally:
        store.close()


def test_every_failing_selector_declared_ask_escalates(tmp_path):
    engine, store = _direct_engine(tmp_path, TWO_FAIL_SELECTORS, frozenset({"asks", "refuses"}))
    try:
        assert engine.check(_booking(6), dry_run=True).outcome == ESCALATE
    finally:
        store.close()


def test_with_no_declared_dispositions_the_gate_refuses_as_before(tmp_path):
    engine, store = _direct_engine(tmp_path, TWO_FAIL_SELECTORS, frozenset())
    try:
        assert engine.check(_booking(7), dry_run=True).outcome == DENY
    finally:
        store.close()


def test_an_off_taxonomy_class_fails_closed_and_refuses(tmp_path):
    engine, store = _direct_engine(tmp_path, TWO_FAIL_SELECTORS, frozenset({"asks", "refuses"}))
    try:
        assert engine.check(_action(8, "booking.unheard_of"), dry_run=True).outcome == DENY
    finally:
        store.close()


def test_a_pass_selector_matching_beside_the_failing_one_does_not_block_the_ask(tmp_path):
    selectors: dict[str, Selector] = {
        "asks": {"action_classes": ["booking.create"], "on_match": "fail"},
        "consequential": {"consequential": True, "on_match": "pass"},
    }
    engine, store = _direct_engine(tmp_path, selectors, frozenset({"asks"}))
    try:
        decision = engine.check(_booking(9), dry_run=True)
        gate = next(c for c in decision.constraints if c.id == "action_class_gate")
        assert gate.evidence["matched_selectors"] == ["asks", "consequential"]
        assert decision.outcome == ESCALATE
    finally:
        store.close()
