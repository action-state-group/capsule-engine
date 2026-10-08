# SPDX-License-Identifier: Apache-2.0
"""The checks that read one number, one member of a closed set, or one
opaque reference from an action: recipient_role, refundability,
material_fields_changed, offer_fields_changed, recipient_seen_before,
channel_change, upfront_amount and task_authority. Each passes, fails, and
records n/a with the field it lacked; every assertion names the field it
reads."""
from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest
from agent_action_capsule import json_digest

from capsule_engine.guards import Action, GuardEngine
from capsule_engine.guards.capsule import NotApplicableEvidence, not_applicable_evidence
from capsule_engine.guards.checks import (
    TaskAuthorityBody,
    check_channel_change,
    check_material_fields_changed,
    check_offer_fields_changed,
    check_recipient_role,
    check_refundability,
    check_task_authority,
    check_upfront_amount,
    fields_basis,
)
from capsule_engine.guards.wickets import load_definition_file

CATALOG = Path(__file__).parent.parent / "capsule_engine" / "guards" / "wickets" / "catalog_defs"
ROLE = load_definition_file(CATALOG / "recipient_role.yaml")
REFUND = load_definition_file(CATALOG / "refundability.yaml")
MATERIAL = load_definition_file(CATALOG / "material_fields_changed.yaml")
OFFER = load_definition_file(CATALOG / "offer_fields_changed.yaml")
RECIPIENT = load_definition_file(CATALOG / "recipient_seen_before.yaml")
CHANNEL = load_definition_file(CATALOG / "channel_change.yaml")
UPFRONT = load_definition_file(CATALOG / "upfront_amount.yaml")
TASK = load_definition_file(CATALOG / "task_authority.yaml")

# A sealed task-authority record's body: a plan in guards/plan.py's shape.
AUTHORITY: TaskAuthorityBody = {"outcome_id": "household.pay_the_plumber/1.0.0", "allowed_actions": ["pay"], "preconditions": [],
             "binding": {"subject": "service/plumber"}}
AUTHORITY_REF = json_digest(AUTHORITY)

# Each new Action field and the one scalar type it may hold.
SCALAR_FIELDS = {
    "recipient_role": str,
    "refundable": bool,
    "material_fields_changed": int,
    "material_fields_basis": str,
    "offer_fields_changed": int,
    "offer_fields_basis": str,
    "channel": str,
    "first_contact_channel": str,
    "upfront_amount_minor": int,
    "task_authority_ref": str,
}


def _action(**overrides) -> Action:
    fields = dict(
        verb="pay",
        operator="household",
        developer="assistant@v1",
        action_class="money.transfer",
        amount_minor=2_000,
        currency="EUR",
        target="service/plumber",
        timestamp="2026-10-08T09:00:00Z",
    )
    fields.update(overrides)
    return Action(**fields)


def _missing(constraint_id: str, field: str) -> NotApplicableEvidence:
    return not_applicable_evidence(constraint_id, in_scope=True, missing_field=field)


def _out_of_scope(constraint_id: str) -> NotApplicableEvidence:
    return not_applicable_evidence(constraint_id, in_scope=False)


# -- the input shape ------------------------------------------------------------


def test_every_new_action_field_is_one_optional_scalar():
    hints = {f.name: f.type for f in dataclasses.fields(Action)}
    for name, scalar in SCALAR_FIELDS.items():
        assert hints[name] == f"{scalar.__name__} | None", name
        assert Action.__dataclass_fields__[name].default is None, name


# -- recipient_role -------------------------------------------------------------


def _role(**overrides):
    return check_recipient_role(
        _action(action_class="disclosure.personal", **overrides),
        roles=ROLE.config["roles"],
        allowed_roles=ROLE.config["allowed_roles"],
        action_classes=ROLE.config["action_classes"],
    ).constraint


def test_recipient_role_passes_for_the_fulfilling_merchant_and_the_user():
    for role in ("fulfilling_merchant", "self"):
        out = _role(recipient_role=role)
        assert out.result == "pass", role
        assert out.evidence == {"recipient_role": role, "recognised": True,
                                "allowed_roles": ["fulfilling_merchant", "self"]}


def test_recipient_role_fails_for_a_third_party():
    out = _role(recipient_role="third_party")
    assert out.result == "fail"
    assert out.evidence["recognised"] is True


def test_recipient_role_outside_the_closed_set_fails_closed():
    out = _role(recipient_role="neighbour")
    assert out.result == "fail"
    assert out.evidence == {"recipient_role": "neighbour", "recognised": False,
                            "allowed_roles": ["fulfilling_merchant", "self"]}


def test_a_role_outside_the_closed_set_fails_even_when_allowed_names_it():
    out = check_recipient_role(_action(action_class="disclosure.personal", recipient_role="neighbour"),
                               roles=ROLE.config["roles"], allowed_roles=[*ROLE.config["allowed_roles"], "neighbour"],
                               action_classes=ROLE.config["action_classes"]).constraint
    assert out.result == "fail"
    assert out.evidence["recognised"] is False


def test_recipient_role_missing_and_out_of_scope_seal_different_facts():
    assert _role().evidence == _missing("recipient_role", "recipient_role")
    out = check_recipient_role(_action(recipient_role="self"), roles=ROLE.config["roles"],
                               allowed_roles=ROLE.config["allowed_roles"],
                               action_classes=ROLE.config["action_classes"]).constraint
    assert (out.result, out.evidence) == ("n/a", _out_of_scope("recipient_role"))


# -- refundability --------------------------------------------------------------


def _refund(**overrides):
    return check_refundability(_action(**overrides), action_classes=REFUND.config["action_classes"]).constraint


def test_refundability_fails_a_payment_that_cannot_be_refunded_and_records_the_rail():
    out = _refund(refundable=False, rail="card")
    assert out.result == "fail"
    assert out.evidence == {"refundable": False, "rail": "card"}


def test_refundability_passes_a_refundable_payment_without_a_rail():
    out = _refund(refundable=True)
    assert out.result == "pass"
    assert out.evidence == {"refundable": True, "rail": None}


def test_refundability_without_the_field_names_it():
    out = _refund()
    assert (out.result, out.evidence) == ("n/a", _missing("refundability", "refundable"))


def test_refundability_does_not_apply_to_a_message():
    out = _refund(action_class="comms.external", refundable=False)
    assert (out.result, out.evidence) == ("n/a", _out_of_scope("refundability"))


# -- material_fields_changed / offer_fields_changed ----------------------------

COUNTS = [
    (check_material_fields_changed, MATERIAL, "material_fields_changed", "material_fields_basis"),
    (check_offer_fields_changed, OFFER, "offer_fields_changed", "offer_fields_basis"),
]


def _count(check, wicket, **overrides):
    return check(_action(**overrides), counted_fields=wicket.config["counted_fields"],
                 max_changed=wicket.config["max_changed"], action_classes=wicket.config["action_classes"]).constraint


@pytest.mark.parametrize(("check", "wicket", "count_field", "basis_field"), COUNTS)
def test_a_count_over_the_pinned_list_fails_above_the_threshold(check, wicket, count_field, basis_field):
    basis = fields_basis(wicket.config["counted_fields"])
    out = _count(check, wicket, **{count_field: 2, basis_field: basis})
    assert out.id == count_field
    assert out.result == "fail"
    assert out.evidence == {"changed": 2, "fields_basis": basis, "max_changed": 0}


@pytest.mark.parametrize(("check", "wicket", "count_field", "basis_field"), COUNTS)
def test_a_count_of_zero_over_the_pinned_list_passes(check, wicket, count_field, basis_field):
    basis = fields_basis(wicket.config["counted_fields"])
    assert _count(check, wicket, **{count_field: 0, basis_field: basis}).result == "pass"


@pytest.mark.parametrize(("check", "wicket", "count_field", "basis_field"), COUNTS)
def test_a_count_over_another_list_is_not_read(check, wicket, count_field, basis_field):
    other = fields_basis(wicket.config["counted_fields"][:-1])
    for basis in (other, None):
        out = _count(check, wicket, **{count_field: 5, basis_field: basis})
        assert (out.result, out.evidence) == ("n/a", _missing(count_field, basis_field)), basis


@pytest.mark.parametrize(("check", "wicket", "count_field", "basis_field"), COUNTS)
def test_an_action_without_a_count_names_the_count(check, wicket, count_field, basis_field):
    out = _count(check, wicket)
    assert (out.result, out.evidence) == ("n/a", _missing(count_field, count_field))


def test_the_basis_is_the_jcs_digest_of_the_pinned_list_in_order():
    fields = MATERIAL.config["counted_fields"]
    assert fields_basis(fields) == json_digest(fields)
    assert fields_basis(fields) != fields_basis(list(reversed(fields)))


# -- upfront_amount -------------------------------------------------------------


def _upfront(**overrides):
    return check_upfront_amount(_action(**overrides), upfront_max_minor=UPFRONT.config["upfront_max_minor"],
                                upfront_max_bps=UPFRONT.config["upfront_max_bps"],
                                action_classes=UPFRONT.config["action_classes"]).constraint


def test_upfront_at_exactly_a_quarter_of_the_total_passes():
    out = _upfront(amount_minor=2_000, upfront_amount_minor=500)
    assert out.result == "pass"
    assert (out.evidence["over_absolute"], out.evidence["over_share"]) == (False, False)


def test_upfront_above_a_quarter_of_the_total_fails_on_the_share():
    out = _upfront(amount_minor=2_000, upfront_amount_minor=501)
    assert out.result == "fail"
    assert (out.evidence["over_absolute"], out.evidence["over_share"]) == (False, True)


def test_upfront_above_the_absolute_limit_fails_on_it():
    out = _upfront(amount_minor=100_000, upfront_amount_minor=10_001)
    assert out.result == "fail"
    assert (out.evidence["over_absolute"], out.evidence["over_share"]) == (True, False)


def test_upfront_at_the_absolute_limit_passes():
    assert _upfront(amount_minor=100_000, upfront_amount_minor=10_000).result == "pass"


def test_upfront_without_a_total_checks_the_absolute_limit_only():
    out = _upfront(amount_minor=None, upfront_amount_minor=9_000)
    assert out.result == "pass"
    assert out.evidence["amount_minor"] is None
    assert out.evidence["over_share"] is False


def test_upfront_without_the_field_names_it():
    out = _upfront()
    assert (out.result, out.evidence) == ("n/a", _missing("upfront_amount", "upfront_amount_minor"))


# -- task_authority -------------------------------------------------------------


def _task(body, **overrides):
    return check_task_authority(_action(**overrides), body, action_classes=TASK.config["action_classes"]).constraint


def test_task_authority_passes_an_action_inside_the_bound_record():
    out = _task(AUTHORITY, task_authority_ref=AUTHORITY_REF)
    assert out.result == "pass"
    assert out.evidence["task_authority_ref"] == AUTHORITY_REF
    assert out.evidence["containment"] == "pass"


def test_task_authority_fails_an_action_outside_the_bound_record():
    out = _task(AUTHORITY, task_authority_ref=AUTHORITY_REF, verb="refund")
    assert out.result == "fail"
    assert out.evidence["containment"] == "fail"


def test_a_body_the_reference_does_not_bind_is_never_read():
    widened = {**AUTHORITY, "allowed_actions": ["pay", "refund"]}
    out = _task(widened, task_authority_ref=AUTHORITY_REF, verb="refund")
    assert (out.result, out.evidence) == ("n/a", _missing("task_authority", "task_authority"))


def test_a_bound_record_that_is_not_a_plan_is_not_read():
    body = {"allowed_classes": ["money.transfer"]}
    out = _task(body, task_authority_ref=json_digest(body))
    assert (out.result, out.evidence) == ("n/a", _missing("task_authority", "task_authority"))


def test_a_body_with_no_digest_is_not_read():
    body = {**AUTHORITY, "limit": 1.5}
    out = _task(body, task_authority_ref=AUTHORITY_REF)
    assert (out.result, out.evidence) == ("n/a", _missing("task_authority", "task_authority"))


def test_task_authority_with_no_body_supplied_names_it():
    out = _task(None, task_authority_ref=AUTHORITY_REF)
    assert (out.result, out.evidence) == ("n/a", _missing("task_authority", "task_authority"))


def test_task_authority_without_the_reference_names_it():
    out = _task(AUTHORITY)
    assert (out.result, out.evidence) == ("n/a", _missing("task_authority", "task_authority_ref"))


# -- channel_change -------------------------------------------------------------


def _channel(**overrides):
    return check_channel_change(_action(**overrides), seeded_channels=CHANNEL.config["seeded_channels"],
                                action_classes=CHANNEL.config["action_classes"]).constraint


def test_channel_change_fails_when_the_channel_moved_from_the_first_contact_one():
    out = _channel(channel="whatsapp", first_contact_channel="marketplace")
    assert out.result == "fail"
    assert out.evidence == {"channel": "whatsapp", "first_contact_channel": "marketplace",
                            "channel_seeded": True, "first_contact_channel_seeded": True}


def test_channel_change_passes_on_the_first_contact_channel():
    assert _channel(channel="marketplace", first_contact_channel="marketplace").result == "pass"


def test_a_namespaced_channel_is_compared_and_recorded_as_not_seeded():
    out = _channel(channel="org.example.chat", first_contact_channel="org.example.chat")
    assert out.result == "pass"
    assert (out.evidence["channel_seeded"], out.evidence["first_contact_channel_seeded"]) == (False, False)


def test_channel_change_names_whichever_channel_is_missing():
    assert _channel(first_contact_channel="web").evidence == _missing("channel_change", "channel")
    assert _channel(channel="web").evidence == _missing("channel_change", "first_contact_channel")


# -- through the engine: the ledger-reading checks, and sealing -----------------


def _engine(store, caps_fold, signer, *wickets) -> GuardEngine:
    return GuardEngine(ledger=store, caps_fold=caps_fold, signer_provider=lambda: signer, wickets=wickets)


def _constraint(decision, constraint_id):
    (out,) = [c for c in decision.constraints if c.id == constraint_id]
    return out


def test_recipient_seen_before_fails_a_first_message_and_passes_once_one_was_accepted(store, caps_fold, signer):
    engine = _engine(store, caps_fold, signer, RECIPIENT)
    message = dict(verb="send", action_class="comms.external", amount_minor=None, currency=None,
                   target="contact/landlord")
    first = engine.check(_action(**message, action_id="send/1"))
    out = _constraint(first, "recipient_seen_before")
    assert out.result == "fail"
    assert out.evidence["prior_count"] == 0
    # A payment the operator went ahead with, addressed to the same target.
    engine.check(_action(target="contact/landlord", action_id="pay/1"))
    second = engine.check(_action(**message, action_id="send/2"))
    out = _constraint(second, "recipient_seen_before")
    assert out.result == "pass"
    assert out.evidence["fold_key"] == {"path": "asg_payload.target", "value": "contact/landlord"}


def test_recipient_seen_before_does_not_apply_to_a_payment(store, caps_fold, signer):
    decision = _engine(store, caps_fold, signer, RECIPIENT).check(_action())
    assert _constraint(decision, "recipient_seen_before").evidence == _out_of_scope("recipient_seen_before")


def test_task_authority_reads_the_body_supplied_with_the_decision(store, caps_fold, signer):
    engine = _engine(store, caps_fold, signer, TASK)
    bound = engine.check(_action(task_authority_ref=AUTHORITY_REF), task_authority=AUTHORITY)
    assert _constraint(bound, "task_authority").result == "pass"
    unsupplied = engine.check(_action(task_authority_ref=AUTHORITY_REF, action_id="pay/2", equivalence_key="2"))
    assert _constraint(unsupplied, "task_authority").evidence == _missing("task_authority", "task_authority")

def test_each_new_field_is_sealed_when_set_and_absent_when_not(store, caps_fold, signer):
    values = {
        "recipient_role": "self",
        "refundable": False,
        "material_fields_changed": 0,
        "material_fields_basis": "a" * 64,
        "offer_fields_changed": 1,
        "offer_fields_basis": "b" * 64,
        "channel": "email",
        "first_contact_channel": "web",
        "upfront_amount_minor": 0,
        "task_authority_ref": "c" * 64,
    }
    assert set(values) == set(SCALAR_FIELDS)
    engine = _engine(store, caps_fold, signer)
    sealed = engine.check(_action(**values, action_id="pay/1")).capsule["asg_payload"]
    assert {k: sealed[k] for k in values} == values
    bare = engine.check(_action(action_id="pay/2", equivalence_key="2")).capsule["asg_payload"]
    assert not set(values) & set(bare)
