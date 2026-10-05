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
)
from capsule_engine.guards.wickets import load_definition_file

CATALOG = Path(__file__).parent.parent / "capsule_engine" / "guards" / "wickets" / "catalog_defs"
RAIL = load_definition_file(CATALOG / "destination_rail.yaml")
IDENTITY = load_definition_file(CATALOG / "counterparty_identity_change.yaml")
CREDENTIAL = load_definition_file(CATALOG / "credential_pattern.yaml")


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


def test_credential_pattern_without_content_is_out_of_scope():
    out = check_credential_pattern(_action(), patterns=CREDENTIAL.config["patterns"]).constraint
    assert out.result == "n/a"
    assert out.evidence == not_applicable_evidence("credential_pattern", in_scope=False)


# -- counterparty_identity_change ------------------------------------------------


def _engine(store, caps_fold, signer, *wickets):
    return GuardEngine(ledger=store, caps_fold=caps_fold, signer_provider=lambda: signer, wickets=wickets)


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
