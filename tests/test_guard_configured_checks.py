# SPDX-License-Identifier: Apache-2.0
"""The three configured checks -- destination_rail,
counterparty_identity_change, credential_pattern -- and how ``GuardEngine``
runs them. Every assertion names the field it reads."""
from __future__ import annotations

from pathlib import Path

import pytest
from agent_action_capsule import json_digest

from capsule_engine.guards import Action, GuardEngine
from capsule_engine.guards.capsule import not_applicable_evidence
from capsule_engine.guards.checks import (
    check_counterparty_identity_change,
    check_credential_pattern,
    check_destination_rail,
    check_recurring_charge,
)
from capsule_engine.guards.wickets import load_definition_file

CATALOG = Path(__file__).parent.parent / "capsule_engine" / "guards" / "wickets" / "catalog_defs"
RAIL = load_definition_file(CATALOG / "destination_rail.yaml")
IDENTITY = load_definition_file(CATALOG / "counterparty_identity_change.yaml")
CREDENTIAL = load_definition_file(CATALOG / "credential_pattern.yaml")
RECURRING = load_definition_file(CATALOG / "recurring_charge.yaml")


def _action(**overrides) -> Action:
    fields = dict(
        verb="pay",
        operator="household",
        developer="assistant@v1",
        action_class="money.transfer",
        amount_minor=1_000,
        currency="EUR",
        target="utility/acct-1",
        timestamp="2026-08-10T09:00:00Z",
    )
    fields.update(overrides)
    return Action(**fields)


# -- destination_rail ----------------------------------------------------------


def test_destination_rail_fails_on_a_watched_rail():
    out = check_destination_rail(_action(rail="p2p"), watched_rails=RAIL.config["watched_rails"], action_classes=RAIL.config["action_classes"]).constraint
    assert out.result == "fail"
    assert out.evidence == {"rail": "p2p", "watched_rails": ["crypto", "gift_card", "p2p"]}


def test_destination_rail_passes_on_an_unwatched_rail():
    out = check_destination_rail(_action(rail="card"), watched_rails=RAIL.config["watched_rails"], action_classes=RAIL.config["action_classes"]).constraint
    assert out.result == "pass"


def test_destination_rail_without_a_rail_is_in_scope_and_names_the_missing_field():
    out = check_destination_rail(_action(), watched_rails=RAIL.config["watched_rails"], action_classes=RAIL.config["action_classes"]).constraint
    assert out.result == "n/a"
    assert out.evidence == not_applicable_evidence("destination_rail", in_scope=True, missing_field="rail")


def test_destination_rail_outside_its_action_classes_is_out_of_scope():
    out = check_destination_rail(
        _action(action_class="info.query"),
        watched_rails=RAIL.config["watched_rails"],
        action_classes=RAIL.config["action_classes"],
    ).constraint
    assert out.result == "n/a"
    assert out.evidence == not_applicable_evidence("destination_rail", in_scope=False)


# -- credential_pattern ----------------------------------------------------------


@pytest.mark.parametrize(
    "content,pattern_id",
    [
        ("Your verification code is 482913", "one_time_code"),
        ("the password: hunter2", "password_value"),
        ("card cvv 123", "card_security_code"),
    ],
)
def test_credential_pattern_fails_on_each_configured_pattern(content, pattern_id):
    out = check_credential_pattern(_action(outgoing_content=content), patterns=CREDENTIAL.config["patterns"]).constraint
    assert out.result == "fail"
    assert out.evidence["matched_pattern_ids"] == [pattern_id]


def test_credential_pattern_passes_on_ordinary_content():
    content = "Thanks, the invoice for March is attached."
    out = check_credential_pattern(_action(outgoing_content=content), patterns=CREDENTIAL.config["patterns"]).constraint
    assert out.result == "pass"
    assert out.evidence["matched_pattern_ids"] == []


def test_credential_pattern_never_puts_the_content_on_the_capsule(store, caps_fold, signer):
    secret = "Your verification code is 482913"
    engine = GuardEngine(ledger=store, caps_fold=caps_fold, signer_provider=lambda: signer, wickets=(CREDENTIAL,))
    capsule = engine.check(_action(outgoing_content=secret, action_id="pay/c1"), dry_run=True).capsule
    assert secret not in repr(capsule)
    assert "outgoing_content" not in capsule["asg_payload"]


def test_credential_evidence_digest_does_not_depend_on_the_content(store, caps_fold, signer):
    """Two different one-time codes that match the same pattern must seal the
    same evidence_digest; otherwise the sealed digest is a guessable hash of
    the content."""
    engine = GuardEngine(ledger=store, caps_fold=caps_fold, signer_provider=lambda: signer, wickets=(CREDENTIAL,))
    first = engine.check(_action(outgoing_content="Your verification code is 482913", action_id="pay/d1",
                                 equivalence_key="d1"), dry_run=True).capsule
    second = engine.check(_action(outgoing_content="Your verification code is 105377", action_id="pay/d2",
                                  equivalence_key="d2"), dry_run=True).capsule
    a, b = _record_for(first, "credential_pattern"), _record_for(second, "credential_pattern")
    assert a["result"] == b["result"] == "fail"
    assert a["evidence_digest"] == b["evidence_digest"]
    assert a["evidence_digest"] == json_digest(
        {"matched_pattern_ids": ["one_time_code"], "pattern_ids": ["card_security_code", "one_time_code", "password_value"]}
    )


def _evidence_reads_of_content(source: str) -> list[str]:
    """Names of evidence keys whose value reads ``outgoing_content``, in every
    ``evidence = {...}`` / ``evidence = SomeType(...)`` assignment of a module."""
    import ast

    offenders = []
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "evidence" for t in node.targets)):
            continue
        match node.value:
            case ast.Dict(keys=keys, values=values):
                pairs = [(getattr(k, "value", "?"), v) for k, v in zip(keys, values, strict=True)]
            case ast.Call(keywords=keywords):
                pairs = [(k.arg, k.value) for k in keywords]
            case _:
                pairs = []
        for key, expr in pairs:
            if any(isinstance(n, ast.Attribute) and n.attr == "outgoing_content" for n in ast.walk(expr)):
                offenders.append(key)
    return offenders


def test_credential_evidence_has_no_key_derived_from_outgoing_content():
    planted = 'evidence = {"content_sha256": sha(action.outgoing_content.encode()), "ids": ids}\n'
    assert _evidence_reads_of_content(planted) == ["content_sha256"]  # positive control
    source = (Path(__file__).parent.parent / "capsule_engine" / "guards" / "checks" / "credential_pattern.py").read_text()
    assert _evidence_reads_of_content(source) == []


def test_credential_pattern_without_content_is_out_of_scope():
    out = check_credential_pattern(_action(), patterns=CREDENTIAL.config["patterns"]).constraint
    assert out.result == "n/a"
    assert out.evidence == not_applicable_evidence("credential_pattern", in_scope=False)


# -- counterparty_identity_change ------------------------------------------------


def _engine(store, caps_fold, signer, *wickets):
    return GuardEngine(ledger=store, caps_fold=caps_fold, signer_provider=lambda: signer, wickets=wickets)


# Reads raw capsule or fixture JSON/YAML: the test's decoding boundary.
def _record_for(capsule: dict, constraint_id: str) -> dict:
    (record,) = [c for c in capsule["constraints"] if c["id"] == constraint_id]
    return record


def test_counterparty_identity_change_first_payment_is_out_of_scope(store, caps_fold, signer):
    out = check_counterparty_identity_change(_action(counterparty_account_ref="acct-ref-A"), store, action_classes=["money.transfer"]).constraint
    assert out.result == "n/a"
    assert out.evidence == not_applicable_evidence("counterparty_identity_change", in_scope=False)


def test_counterparty_identity_change_passes_on_same_account_and_fails_on_a_new_one(store, caps_fold, signer):
    engine = _engine(store, caps_fold, signer, IDENTITY)
    # Three different payments to one counterparty: a distinct
    # equivalence_key each, so dedupe does not treat them as one action.
    first = engine.check(
        _action(counterparty_account_ref="acct-ref-A", action_id="pay/i1", equivalence_key="march"), dry_run=True
    ).capsule
    same = engine.check(
        _action(counterparty_account_ref="acct-ref-A", action_id="pay/i2", equivalence_key="april"), dry_run=True
    ).capsule
    changed = engine.check(
        _action(counterparty_account_ref="acct-ref-B", action_id="pay/i3", equivalence_key="may"), dry_run=True
    ).capsule

    assert first["asg_payload"]["counterparty_account_ref"] == "acct-ref-A"
    assert first["disposition"]["decision"] == "accept"
    assert same["disposition"]["decision"] == "accept"
    assert _record_for(same, "counterparty_identity_change")["result"] == "pass"
    changed_record = _record_for(changed, "counterparty_identity_change")
    assert changed_record["result"] == "fail"
    assert changed_record["evidence_digest"] == json_digest(
        {
            "target": "utility/acct-1",
            "counterparty_account_ref": "acct-ref-B",
            "prior_capsule_id": same["capsule_id"],
            "prior_counterparty_account_ref": "acct-ref-A",
        }
    )


def test_counterparty_identity_change_ignores_a_prior_record_that_was_not_accepted(store, caps_fold, signer):
    engine = _engine(store, caps_fold, signer, IDENTITY, RAIL)
    rejected = engine.check(
        _action(counterparty_account_ref="acct-ref-A", rail="p2p", action_id="pay/r1"), dry_run=True
    ).capsule
    assert rejected["disposition"]["decision"] == "reject"
    later = engine.check(
        _action(counterparty_account_ref="acct-ref-B", rail="card", action_id="pay/r2", equivalence_key="r2"),
        dry_run=True,
    ).capsule
    assert _record_for(later, "counterparty_identity_change")["result"] == "n/a"


def test_counterparty_identity_change_outside_its_action_classes_is_out_of_scope(store):
    action = _action(action_class="info.query", counterparty_account_ref="acct-ref-A")
    out = check_counterparty_identity_change(action, store, action_classes=IDENTITY.config["action_classes"]).constraint
    assert out.result == "n/a"
    assert out.evidence == not_applicable_evidence("counterparty_identity_change", in_scope=False)


@pytest.mark.parametrize(
    "overrides,missing",
    [({"target": None, "counterparty_account_ref": "acct-ref-A"}, "target"), ({}, "counterparty_account_ref")],
)
def test_counterparty_identity_change_names_the_missing_field(store, overrides, missing):
    out = check_counterparty_identity_change(_action(**overrides), store, action_classes=["money.transfer"]).constraint
    assert out.result == "n/a"
    assert out.evidence == not_applicable_evidence("counterparty_identity_change", in_scope=True, missing_field=missing)


# -- recurring_charge ----------------------------------------------------------


def _recurring(action: Action):
    return check_recurring_charge(
        action, one_time_values=RECURRING.config["one_time_values"], action_classes=RECURRING.config["action_classes"]
    ).constraint


def test_recurring_charge_fails_on_a_repeating_charge_and_passes_one_time():
    repeating = _recurring(_action(recurrence="monthly"))
    assert repeating.result == "fail"
    assert repeating.evidence == {"recurrence": "monthly", "one_time_values": ["one_time"]}
    assert _recurring(_action(recurrence="one_time")).result == "pass"


def test_recurring_charge_without_a_recurrence_names_the_missing_field():
    out = _recurring(_action())
    assert out.result == "n/a"
    assert out.evidence == not_applicable_evidence("recurring_charge", in_scope=True, missing_field="recurrence")


def test_recurring_charge_outside_its_action_classes_is_out_of_scope():
    out = _recurring(_action(action_class="info.query", recurrence="monthly"))
    assert out.result == "n/a"
    assert out.evidence == not_applicable_evidence("recurring_charge", in_scope=False)


def test_recurrence_is_recorded_on_the_capsule(store, caps_fold, signer):
    engine = _engine(store, caps_fold, signer, RECURRING)
    capsule = engine.check(_action(recurrence="monthly", action_id="pay/rc1"), dry_run=True).capsule
    assert capsule["asg_payload"]["recurrence"] == "monthly"
    assert _record_for(capsule, "recurring_charge")["result"] == "fail"


# -- GuardEngine wiring ----------------------------------------------------------


def test_engine_with_no_configured_wickets_seals_the_same_capsule_as_before(tmp_path, caps_fold, signer):
    from capsule_ledger.ledger import LedgerStore

    a, b = LedgerStore(tmp_path / "a"), LedgerStore(tmp_path / "b")
    try:
        plain = GuardEngine(ledger=a, caps_fold=caps_fold, signer_provider=lambda: signer)
        explicit = GuardEngine(ledger=b, caps_fold=caps_fold, signer_provider=lambda: signer, wickets=())
        assert plain.check(_action(action_id="pay/w1")).capsule == explicit.check(_action(action_id="pay/w1")).capsule
        capsule = plain.check(_action(action_id="pay/w2", amount_minor=5)).capsule
        assert [c["id"] for c in capsule["constraints"]] == ["dedupe", "caps", "verify_before_dispatch"]
    finally:
        a.close()
        b.close()


def test_engine_appends_configured_checks_in_order_and_a_failure_denies(store, caps_fold, signer):
    engine = _engine(store, caps_fold, signer, RAIL, IDENTITY, CREDENTIAL)
    decision = engine.check(_action(rail="crypto", action_id="pay/w3"), dry_run=True)
    ids = [c["id"] for c in decision.capsule["constraints"]]
    assert ids == [
        "dedupe",
        "caps",
        "verify_before_dispatch",
        "destination_rail",
        "counterparty_identity_change",
        "credential_pattern",
    ]
    assert _record_for(decision.capsule, "destination_rail")["result"] == "fail"
    assert decision.capsule["disposition"]["decision"] == "reject"


def test_engine_refuses_a_wicket_it_cannot_run_per_decision(store, caps_fold, signer):
    dedupe = load_definition_file(CATALOG / "dedupe.yaml")
    with pytest.raises(ValueError, match="cannot run per decision"):
        _engine(store, caps_fold, signer, dedupe)


# -- counterparty_account_ref is opaque ------------------------------------------


@pytest.mark.parametrize(
    "raw",
    ["4111 1111 1111 1111", "4111-1111-1111-1111", "12345678", "021000021", "DE89 3704 0044 0532 0130 00", "gb82west12345698765432"],
)
def test_a_raw_account_card_or_iban_number_is_refused(raw):
    with pytest.raises(ValueError, match="opaque reference"):
        _action(counterparty_account_ref=raw)


@pytest.mark.parametrize(
    "opaque",
    ["acct-ref-water-1", "tok_8f3a2c", "9b74c9897bac770ffc029102a200c5de2f4a1c3e5d8e7f6a0b1c2d3e4f5a6b7c"],
)
def test_an_opaque_reference_is_accepted(opaque):
    assert _action(counterparty_account_ref=opaque).counterparty_account_ref == opaque
