# SPDX-License-Identifier: Apache-2.0
"""A failing configured check asks an approver when every obligation bound to
it declares ``default_disposition: ASK`` (D2, ``guards/engine.py``
``_decide``; ``packs/install.py`` ``ask_wickets``).

Before this, only action_class_gate selectors were read this way: r08 r09 r10
r21 r22 r26 (and r07 r19 r20) declare ASK on everyday 0.3.2, yet each failure
refused. Now the engine reads the declared disposition for every configured
check: a check bound to a NEVER, DO or undeclared obligation still refuses,
an integrity check (dedupe in one deal, verify_before_dispatch,
single_commitment, promise_never) refuses whatever its obligation declares,
and so does a class with no ``approver_role``.

Every decision runs through a pack-installed engine, each on a fresh ledger.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from capsule_ledger.ledger import LedgerStore

from capsule_engine.guards import Action, GuardEngine, LocalSigner
from capsule_engine.guards.capsule import DENY, ESCALATE
from capsule_engine.guards.checks import fields_basis, task_authority_record_digest
from capsule_engine.guards.engine import ASK_RULE_EXCLUDED_CHECKS
from capsule_engine.guards.wickets import load_definition_file
from capsule_engine.packs import build_engine, install_pack, load_pack_dir
from capsule_engine.packs.install import ask_wickets
from capsule_engine.packs.obligation_results import obligation_results
from capsule_engine.packs.schema import Obligation, PackDefinition

PACK_DIR = Path(__file__).parent.parent / "capsule_engine" / "packs" / "catalog" / "everyday"
PACK = load_pack_dir(PACK_DIR)
PACK_0_3_2_DIGEST = "6f333fa8b7a7e137abe6c61e5a32097ed06d493479a018807cbf4d2e4f5da7b2"
WICKETS = Path(__file__).parent.parent / "capsule_engine" / "guards" / "wickets" / "catalog_defs"
MATERIAL_BASIS = fields_basis(load_definition_file(WICKETS / "material_fields_changed.yaml").config["counted_fields"])
OFFER_BASIS = fields_basis(load_definition_file(WICKETS / "offer_fields_changed.yaml").config["counted_fields"])
OPERATOR = "household-wickets-ask"
SIGNER = LocalSigner(key_id="everyday-wickets-ask-key", secret=b"everyday-wickets-ask-fixed-key")
TASK_AUTHORITY = {"body": {"outcome_id": "household.pay_the_plumber/1.0.0", "allowed_actions": ["make_payment"],
                           "preconditions": [], "binding": {"subject": "service/plumber"}}}
TASK_AUTHORITY_REF = task_authority_record_digest(TASK_AUTHORITY)

# The configured checks on everyday 0.3.2 whose bound obligations all declare
# ASK and are not excluded: r07 r08 r09 r10 r13 r19 r20 r21 r22 r26.
# Never asked on, whatever a pack declares: the integrity checks, then the two
# with their own ask rule. Spelled out so a test fails if the engine's set shrinks.
EXCLUDED = ["dedupe", "verify_before_dispatch", "single_commitment", "promise_never",
            "action_class_gate", "counterparty_list"]
ASK_CHECKS = frozenset({
    "recurring_charge", "refundability", "material_fields_changed", "offer_fields_changed",
    "recipient_seen_before", "counterparty_identity_change", "destination_rail", "channel_change",
    "upfront_amount", "task_authority",
})


def _payment(n: int, **fields) -> Action:
    """A payment whose declared inputs are each inside their limit, to a payee
    of its own; ``fields`` moves one past its limit."""
    base = dict(
        amount_minor=2_000, currency="EUR", target=f"seller/bike-{n}", rail="card",
        counterparty_account_ref=f"acct-ref-bike-{n}", recurrence="one_time", refundable=True,
        material_fields_changed=0, material_fields_basis=MATERIAL_BASIS,
        offer_fields_changed=0, offer_fields_basis=OFFER_BASIS,
        channel="marketplace", first_contact_channel="marketplace", upfront_amount_minor=500,
    )
    base.update(fields)
    return Action(
        verb="make_payment",
        operator=f"{OPERATOR}-{n}",
        developer="household-assistant-wa@v1",
        action_class="money.transfer",
        action_id=f"make_payment/everyday-wickets-ask-{n}",
        timestamp=f"2026-08-10T14:{n:02d}:00Z",
        **base,
    )


def _non_refundable_booking(n: int, **fields) -> Action:
    return Action(
        verb="book_room",
        operator=f"{OPERATOR}-{n}",
        developer="household-assistant-wa@v1",
        action_class="booking.create",
        amount_minor=55_880,
        currency="USD",
        target="venue/hotel-non-refundable",
        refundable=False,
        action_id=f"book_room/everyday-wickets-ask-{n}",
        timestamp=f"2026-08-10T15:{n:02d}:00Z",
        **fields,
    )


def _with_disposition(pack: PackDefinition, rule: str, disposition: str | None) -> PackDefinition:
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


def _failing_checks(decision) -> set[str]:
    return {c.id for c in decision.constraints if c.result == "fail"}


def test_the_pack_this_runs_against_is_everyday_0_3_2_at_its_vendored_digest():
    assert PACK.pack_id == "asg/everyday/0.3.2"
    assert PACK.definition_digest() == PACK_0_3_2_DIGEST


def test_the_installed_engine_asks_on_exactly_the_checks_whose_rules_all_declare_ask(engine_for):
    # Left out: dedupe (r27 declares ASK) is integrity; counterparty_seen_before
    # mixes DO (r02) and ASK (r06); recipient_role is DO; credential_pattern is
    # NEVER; caps (r05) is a reference check, not a configured one.
    assert engine_for(PACK).ask_wickets == ASK_CHECKS
    assert ask_wickets(PACK) == ASK_CHECKS


def test_the_non_refundable_booking_asks_citing_r05_r08_and_r16(engine_for):
    decision = engine_for(PACK).check(_non_refundable_booking(1), dry_run=True)
    assert _failing_checks(decision) == {"caps", "action_class_gate", "refundability"}
    assert _failing_rules(decision) == [
        "r05-spending-limits", "r08-non-refundable-or-hard-to-undo", "r16-booking-with-a-commitment",
    ]
    assert decision.outcome == ESCALATE
    assert decision.capsule["disposition"]["decision"] == "needs_input"
    assert decision.capsule["disposition"]["verdict_class"] == "hitl_dispatched"


def test_the_escalation_reason_carries_no_value_from_the_action(engine_for):
    decision = engine_for(PACK).check(_non_refundable_booking(2), dry_run=True)
    for value in ("55880", "558.80", "hotel", "venue/", "USD", OPERATOR):
        assert value not in decision.reason


SINGLE_RULE_CASES = [
    ("refundability", {"refundable": False}, "r08-non-refundable-or-hard-to-undo"),
    ("material_fields_changed", {"material_fields_changed": 2}, "r09-material-terms-changed"),
    ("offer_fields_changed", {"offer_fields_changed": 1}, "r10-materially-different-offer"),
    ("channel_change", {"channel": "whatsapp"}, "r21-unexpected-channel-change"),
    ("upfront_amount", {"upfront_amount_minor": 501}, "r22-unusual-counterparty-behaviour"),
    ("recurring_charge", {"recurrence": "monthly"}, "r07-subscription-or-free-trial"),
    ("destination_rail", {"rail": "p2p"}, "r20-payment-method-or-destination-changed"),
]


@pytest.mark.parametrize(("check", "fields", "rule"), SINGLE_RULE_CASES)
def test_one_ask_declared_check_failing_asks_citing_only_its_own_rule(engine_for, check, fields, rule):
    decision = engine_for(PACK).check(_payment(3, **fields), dry_run=True)
    assert _failing_checks(decision) == {check}
    assert _failing_rules(decision) == [rule]
    assert decision.outcome == ESCALATE
    assert decision.capsule["disposition"]["decision"] == "needs_input"


def test_a_step_outside_the_task_asks_citing_r26(engine_for):
    action = _payment(4, target="service/roofer", task_authority_ref=TASK_AUTHORITY_REF)
    decision = engine_for(PACK).check(action, dry_run=True, task_authority_record=TASK_AUTHORITY)
    assert _failing_checks(decision) == {"task_authority"}
    assert _failing_rules(decision) == ["r26-stay-within-task-bounds"]
    assert decision.outcome == ESCALATE


def test_a_changed_payee_account_asks_citing_r19(engine_for):
    engine = engine_for(PACK)
    water = dict(target="utility/water-co", upfront_amount_minor=0)
    first = _payment(5, counterparty_account_ref="acct-ref-water-1", equivalence_key="water-co/2026-08", **water)
    assert engine.check(first).outcome != DENY
    # The next month's bill (its own equivalence key, so no dedupe hit), paid into another account.
    changed = _payment(6, counterparty_account_ref="acct-ref-water-2", equivalence_key="water-co/2026-09", **water)
    decision = engine.check(replace(changed, operator=first.operator), dry_run=True)
    assert _failing_checks(decision) == {"counterparty_identity_change"}
    assert _failing_rules(decision) == ["r19-counterparty-identity-changed"]
    assert decision.outcome == ESCALATE


def test_a_message_to_a_new_recipient_refuses_naming_the_missing_approver(engine_for):
    # recipient_seen_before (r13) declares ASK and is escalatable, but
    # communication.send names no approver_role in taxonomy 4, so it refuses.
    message = Action(
        verb="send_message", operator=f"{OPERATOR}-7", developer="household-assistant-wa@v1",
        action_class="comms.external", target="contact/new-friend", outgoing_content="See you at noon.",
        action_id="send_message/everyday-wickets-ask-7", timestamp="2026-08-10T14:07:00Z",
    )
    decision = engine_for(PACK).check(message, dry_run=True)
    assert _failing_checks(decision) == {"recipient_seen_before"}
    assert _failing_rules(decision) == ["r13-message-to-a-new-recipient"]
    assert decision.outcome == DENY
    assert "action class 'communication.send' names no approver_role" in decision.reason


@pytest.mark.parametrize("disposition", ["NEVER", "DO", None])
@pytest.mark.parametrize(("check", "fields", "rule"), SINGLE_RULE_CASES)
def test_an_ask_rule_re_declared_never_do_or_undeclared_refuses(engine_for, check, fields, rule, disposition):
    pack = _with_disposition(PACK, rule, disposition)
    decision = engine_for(pack).check(_payment(8, **fields), dry_run=True)
    assert _failing_checks(decision) == {check}
    assert decision.outcome == DENY
    assert "approver" not in decision.reason


@pytest.mark.parametrize(("check", "fields", "rule"), SINGLE_RULE_CASES)
def test_a_check_asks_only_when_every_rule_bound_to_it_declares_ask(engine_for, check, fields, rule):
    # A second rule on the same check, declaring NEVER, beside the ASK one.
    extra = Obligation(id=f"rx-never-{check}", statement="declared never", check=check, default_disposition="NEVER")
    pack = replace(PACK, obligations=(*PACK.obligations, extra))
    decision = engine_for(pack).check(_payment(14, **fields), dry_run=True)
    assert _failing_checks(decision) == {check}
    assert decision.outcome == DENY


@pytest.mark.parametrize(("check", "fields", "rule"), SINGLE_RULE_CASES)
def test_two_ask_rules_on_one_check_still_ask(engine_for, check, fields, rule):
    rule2 = Obligation(id=f"rx-ask-{check}", statement="declared ask", check=check, default_disposition="ASK")
    pack = replace(PACK, obligations=(*PACK.obligations, rule2))
    assert engine_for(pack).check(_payment(15, **fields), dry_run=True).outcome == ESCALATE


@pytest.mark.parametrize("extra", ["DO", None])
@pytest.mark.parametrize(("check", "fields", "rule"), SINGLE_RULE_CASES)
def test_a_do_or_undeclared_rule_beside_the_ask_one_refuses(engine_for, check, fields, rule, extra):
    rule2 = Obligation(id=f"rx-{check}", statement="declared", check=check, default_disposition=extra)
    pack = replace(PACK, obligations=(*PACK.obligations, rule2))
    assert engine_for(pack).check(_payment(16, **fields), dry_run=True).outcome == DENY


def test_caps_asks_whatever_its_rule_declares(engine_for):
    # caps is a reference check that has always asked; declared dispositions
    # neither add nor remove it. This pins that, so a change is deliberate.
    pack = _with_disposition(PACK, "r05-spending-limits", "NEVER")
    decision = engine_for(pack).check(_payment(17, amount_minor=3_000, upfront_amount_minor=0), dry_run=True)
    assert _failing_checks(decision) == {"caps"}
    assert decision.outcome == ESCALATE


def test_r26_re_declared_never_refuses_a_step_outside_the_task(engine_for):
    pack = _with_disposition(PACK, "r26-stay-within-task-bounds", "NEVER")
    action = _payment(9, target="service/roofer", task_authority_ref=TASK_AUTHORITY_REF)
    decision = engine_for(pack).check(action, dry_run=True, task_authority_record=TASK_AUTHORITY)
    assert _failing_checks(decision) == {"task_authority"}
    assert decision.outcome == DENY


@pytest.mark.parametrize(("check", "fields", "rule"), SINGLE_RULE_CASES)
def test_an_ask_failure_beside_a_never_failure_refuses(engine_for, check, fields, rule):
    # A card security code in outgoing content fails credential_pattern (r23 NEVER).
    action = _payment(10, outgoing_content="Your verification code is 482913", **fields)
    decision = engine_for(PACK).check(action, dry_run=True)
    assert _failing_checks(decision) == {check, "credential_pattern"}
    assert decision.outcome == DENY


@pytest.mark.parametrize(("check", "fields", "rule"), SINGLE_RULE_CASES)
def test_an_ask_failure_beside_a_same_deal_dedupe_hit_refuses(engine_for, check, fields, rule):
    engine = engine_for(PACK)
    action = _payment(11, **fields)
    assert engine.check(action).outcome == ESCALATE
    repeat = engine.check(action, dry_run=True)
    assert _failing_checks(repeat) == {check, "dedupe"}
    assert repeat.outcome == DENY


@pytest.mark.parametrize(("check", "fields", "rule"), SINGLE_RULE_CASES)
def test_an_ask_failure_citing_a_mandate_not_on_the_ledger_refuses(engine_for, check, fields, rule):
    action = _payment(12, cited_mandate_capsule_id="0" * 64, **fields)
    decision = engine_for(PACK).check(action, dry_run=True)
    assert _failing_checks(decision) == {check, "verify_before_dispatch"}
    assert decision.outcome == DENY


def test_the_non_refundable_booking_beside_a_never_failure_refuses(engine_for):
    pack = _with_disposition(PACK, "r08-non-refundable-or-hard-to-undo", "NEVER")
    assert engine_for(pack).check(_non_refundable_booking(13), dry_run=True).outcome == DENY


def test_the_engine_excludes_exactly_the_integrity_and_own_rule_checks():
    assert ASK_RULE_EXCLUDED_CHECKS == frozenset(EXCLUDED)


@pytest.mark.parametrize("check", EXCLUDED)
def test_an_integrity_or_own_rule_check_never_joins_the_ask_set_whatever_its_rule_declares(check):
    extra = Obligation(id=f"rx-{check}", statement="declared ask", check=check, default_disposition="ASK")
    assert check not in ask_wickets(replace(PACK, obligations=(*PACK.obligations, extra)))


@pytest.mark.parametrize("check", EXCLUDED)
def test_the_engine_refuses_an_integrity_or_own_rule_check_in_the_ask_set(tmp_path, check):
    store = LedgerStore(tmp_path / "direct")
    caps_fold = next(f for f in PACK.folds if f.fold_id.startswith("spend.weekly/"))
    try:
        with pytest.raises(ValueError, match="cannot be made to ask"):
            GuardEngine(ledger=store, caps_fold=caps_fold, signer_provider=lambda: SIGNER,
                        ask_wickets=frozenset({check}))
    finally:
        store.close()


def test_the_engine_refuses_an_ask_set_naming_a_check_no_wicket_runs(tmp_path):
    store = LedgerStore(tmp_path / "direct")
    caps_fold = next(f for f in PACK.folds if f.fold_id.startswith("spend.weekly/"))
    try:
        with pytest.raises(ValueError, match="no configured wicket runs"):
            GuardEngine(ledger=store, caps_fold=caps_fold, signer_provider=lambda: SIGNER,
                        ask_wickets=frozenset({"refundability"}))
    finally:
        store.close()
