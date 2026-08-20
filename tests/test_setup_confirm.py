# SPDX-License-Identifier: Apache-2.0
import io

import pytest

from capsule_engine.setup.confirm import (
    EVENT_REFUSAL_ACKNOWLEDGED,
    ConfirmError,
    confirm_accept,
    confirm_acknowledge_refusal,
    confirm_scope_census,
)
from capsule_engine.setup.declarations import DeclarationStore
from capsule_engine.setup.observe import ObserveRecorder
from capsule_engine.setup.propose import persist_proposals, propose_from_ledger


def _observe(store, signer, events):
    recorder = ObserveRecorder(
        ledger=store, signer=signer, operator="op", developer="dev", heartbeat_every=0, heartbeat_stream=io.StringIO()
    )
    return recorder.run(events)


def _proposed_store(store, signer, tmp_path, events=()):
    _observe(store, signer, events)
    proposal_set = propose_from_ledger(store)
    decl_store = DeclarationStore(tmp_path)
    persist_proposals(proposal_set, decl_store)
    return decl_store


def test_confirm_accept_seals_a_compilation_record_and_flips_state(store, signer, tmp_path):
    events = [
        {"kind": "dispatch", "dispatch_id": "d1", "action_class": "remediation", "tool": "remediate"},
        {"kind": "confirmation", "commitment_ref": "d1", "status": "confirmed"},
    ]
    decl_store = _proposed_store(store, signer, tmp_path, events)
    assert decl_store.load("outcome.remediation_confirmed").acceptance_state == "proposed"

    record = confirm_accept("outcome.remediation_confirmed", store=decl_store, ledger=store, signer=signer, operator="op", developer="dev")
    assert record["asg_payload"]["detail"]["d_digest"] == decl_store.load("outcome.remediation_confirmed").d_digest
    assert decl_store.load("outcome.remediation_confirmed").acceptance_state == "accepted"


def test_confirm_accept_refuses_a_refused_candidate(store, signer, tmp_path):
    decl_store = _proposed_store(store, signer, tmp_path)
    with pytest.raises(ConfirmError, match="REFUSED"):
        confirm_accept("outcome.trust_increased", store=decl_store, ledger=store, signer=signer, operator="op", developer="dev")


def test_confirm_scope_census_appends_a_census_capsule(store, signer):
    capsule = confirm_scope_census(
        document_digest="d" * 64, n=3, m=5, review_by="alice", ledger=store, signer=signer, operator="op", developer="dev"
    )
    assert store.fetch(capsule["capsule_id"]) is not None


def test_confirm_acknowledge_refusal_seals_refusal_and_ack_chained(store, signer, tmp_path):
    decl_store = _proposed_store(store, signer, tmp_path)
    refusal_capsule, ack_capsule = confirm_acknowledge_refusal(
        "outcome.trust_increased", store=decl_store, ledger=store, signer=signer, operator="op", developer="dev", acknowledged_by="alice"
    )
    assert ack_capsule["chain"]["parent_capsule_id"] == refusal_capsule["capsule_id"]
    assert ack_capsule["chain"]["relation"] == "confirms"
    assert ack_capsule["asg_payload"]["event"] == EVENT_REFUSAL_ACKNOWLEDGED
    assert decl_store.load("outcome.trust_increased").acceptance_state == "refused"


def test_confirm_acknowledge_refusal_rejects_a_non_refused_candidate(store, signer, tmp_path):
    events = [
        {"kind": "dispatch", "dispatch_id": "d1", "action_class": "remediation", "tool": "remediate"},
        {"kind": "confirmation", "commitment_ref": "d1", "status": "confirmed"},
    ]
    decl_store = _proposed_store(store, signer, tmp_path, events)
    with pytest.raises(ConfirmError, match="not REFUSED"):
        confirm_acknowledge_refusal(
            "outcome.remediation_confirmed", store=decl_store, ledger=store, signer=signer, operator="op", developer="dev", acknowledged_by="alice"
        )
