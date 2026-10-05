# SPDX-License-Identifier: Apache-2.0
"""An escalated decision seals disposition.decision ``needs_input`` with
disposition.verdict_class ``hitl_dispatched`` -- the pair the donated
conformance vector uses for the same case -- and a record sealed with the
legacy pairing (both fields ``hitl_dispatched``) still reads as an
escalation, untouched.

Vendored, byte-for-byte, under ``tests/fixtures/escalate-disposition/``:

- ``aac-pos-hitl-dispatched.input.json``: agent-action-capsule
  ``vectors/capsule/pos-hitl-dispatched/input.json`` at
  3dfc70b8eda76f87e788b743d75a266f2746cf98;
- ``legacy-sealed-escalate.json``: the caps-escalate decision from the
  payments-safety fixture ledger at capsule-engine
  3e6e5014533c07517fea6779bf50d996ca5c57c1, sealed under the legacy pairing.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from agent_action_capsule import compute_capsule_id

from capsule_engine.guards import Action, GuardEngine
from capsule_engine.guards.capsule import outcome_from_disposition

FIXTURES = Path(__file__).parent / "fixtures" / "escalate-disposition"
VECTOR = FIXTURES / "aac-pos-hitl-dispatched.input.json"
LEGACY = FIXTURES / "legacy-sealed-escalate.json"


def test_vendored_files_are_the_bytes_they_claim_to_be():
    assert hashlib.sha256(VECTOR.read_bytes()).hexdigest() == (
        "53fb07010af1370302992748a6242d209b2d7102ff59b6203df47fadc1556bdd"
    )
    assert hashlib.sha256(LEGACY.read_bytes()).hexdigest() == (
        "6ff584cf57238a2e210d6925aa36a2381c7dd54c8645f3205adf05f05ed6b47b"
    )


# Reads raw capsule or fixture JSON/YAML: the test's decoding boundary.
def _escalated(store, caps_fold, signer) -> dict:
    engine = GuardEngine(
        ledger=store, caps_fold=caps_fold, signer_provider=lambda: signer, caps_minor={"money.transfer": 100}
    )
    action = Action(
        verb="pay", operator="acme", developer="agent@v1", action_class="money.transfer", amount_minor=500,
        action_id="pay/escalate-1", timestamp="2026-08-10T09:00:00Z",
    )
    decision = engine.check(action, dry_run=True)
    assert decision.outcome == "escalate"
    return decision.capsule


def test_escalate_seals_the_donated_vectors_disposition_pair(store, caps_fold, signer):
    vector = json.loads(VECTOR.read_text())
    capsule = _escalated(store, caps_fold, signer)
    assert capsule["disposition"]["decision"] == vector["disposition"]["decision"] == "needs_input"
    assert capsule["disposition"]["verdict_class"] == vector["disposition"]["verdict_class"] == "hitl_dispatched"


def test_escalate_reads_back_as_escalate(store, caps_fold, signer):
    assert outcome_from_disposition(_escalated(store, caps_fold, signer)["disposition"]) == "escalate"


def test_a_record_sealed_with_the_legacy_pairing_still_reads_as_escalate():
    legacy = json.loads(LEGACY.read_text())
    assert legacy["disposition"]["decision"] == "hitl_dispatched"
    assert legacy["disposition"]["verdict_class"] == "hitl_dispatched"
    body = {k: v for k, v in legacy.items() if k != "capsule_id"}
    assert compute_capsule_id(body) == legacy["capsule_id"]  # still intact; never rewritten
    assert outcome_from_disposition(legacy["disposition"]) == "escalate"


@pytest.mark.parametrize(
    "disposition,outcome",
    [({"decision": "accept"}, "allow"), ({"decision": "reject", "verdict_class": "blocked"}, "deny")],
)
def test_allow_and_deny_read_back(disposition, outcome):
    assert outcome_from_disposition(disposition) == outcome


@pytest.mark.parametrize(
    "disposition",
    [{"decision": "hitl_dispatched"}, {"decision": "deferred"}, {"decision": "approve"}, {}],
)
def test_a_decision_with_no_guard_outcome_is_refused(disposition):
    with pytest.raises(ValueError, match="no guard outcome"):
        outcome_from_disposition(disposition)
