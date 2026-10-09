# SPDX-License-Identifier: Apache-2.0
"""``Action.equivalence_key`` is an idempotency key on both sides of dedupe.

A caller's key replaces the default formula for the action being checked
(``equivalence_key_for_action``), and the decision seals its ``json_digest``
as ``asg_payload.equivalence_key_digest``, never the raw key. The capsule side
(``equivalence_key_for_capsule``) reads that digest before the formula, so an
exact retry under the same key fails dedupe, two different keys keep two acts
distinct, and a replayed record is keyed as it was live. The field is
local-only (``tests/test_local_only_payload_fields.py``).
"""
from __future__ import annotations

import json

import pytest
from agent_action_capsule import json_digest
from capsule_ledger.ledger import LedgerStore

from capsule_engine.guards import Action, GuardEngine, LocalSigner
from capsule_engine.guards.checks.dedupe import equivalence_key_for_action, equivalence_key_for_capsule

SIGNER = LocalSigner(key_id="dedupe-equivalence-key", secret=b"dedupe-equivalence-key-fixed-key")
RAW_KEY = "order-123"


def _payment(n: int, **fields) -> Action:
    return Action(
        verb="pay",
        operator="dedupe-equivalence-key",
        developer="assistant@v1",
        action_class="money.transfer",
        currency="EUR",
        target="payee/garden-centre",
        amount_minor=1_250,
        action_id=f"pay/dedupe-equivalence-key-{n}",
        timestamp=f"2026-10-09T12:{n:02d}:00Z",
        **fields,
    )


@pytest.fixture
def engine(tmp_path, caps_fold):
    store = LedgerStore(tmp_path / "ledger")
    yield GuardEngine(ledger=store, caps_fold=caps_fold, signer_provider=lambda: SIGNER)
    store.close()


def _dedupe(decision) -> str:
    (record,) = [c for c in decision.constraints if c.id == "dedupe"]
    return record.result


def test_the_decision_seals_the_digest_of_the_key_and_never_the_key(engine):
    action = _payment(1, equivalence_key=RAW_KEY)
    capsule = engine.check(action).capsule
    assert capsule["asg_payload"]["equivalence_key_digest"] == json_digest(RAW_KEY)
    assert "equivalence_key" not in capsule["asg_payload"]
    assert RAW_KEY not in json.dumps(capsule)
    assert equivalence_key_for_action(action) == equivalence_key_for_capsule(capsule) == json_digest(RAW_KEY)


def test_the_dedupe_evidence_holds_the_digest_and_never_the_key(engine):
    engine.check(_payment(1, equivalence_key=RAW_KEY))
    decision = engine.check(_payment(2, equivalence_key=RAW_KEY))
    (record,) = [c for c in decision.constraints if c.id == "dedupe"]
    assert record.evidence["equivalence_key"] == json_digest(RAW_KEY)
    assert RAW_KEY not in json.dumps(record.evidence)


def test_a_decision_without_a_key_keeps_its_prior_bytes(engine):
    capsule = engine.check(_payment(1)).capsule
    assert "equivalence_key_digest" not in capsule["asg_payload"]
    assert equivalence_key_for_capsule(capsule) == equivalence_key_for_action(_payment(1))


def test_without_a_caller_key_the_same_act_repeated_fails_dedupe(engine):
    assert _dedupe(engine.check(_payment(1))) == "pass"
    assert _dedupe(engine.check(_payment(2))) == "fail"


def test_a_caller_key_keeps_two_acts_distinct(engine):
    """What the everyday bootstrap note relies on: two genuinely different
    payments to one payee, told apart by their keys."""
    assert _dedupe(engine.check(_payment(1, equivalence_key="bill-2026-09"))) == "pass"
    assert _dedupe(engine.check(_payment(2, equivalence_key="bill-2026-10"))) == "pass"


def test_an_exact_retry_under_the_same_caller_key_is_denied_as_a_duplicate(engine):
    first = engine.check(_payment(1, equivalence_key=RAW_KEY))
    retry = engine.check(_payment(2, equivalence_key=RAW_KEY))
    assert _dedupe(first) == "pass"
    assert _dedupe(retry) == "fail"
    assert retry.outcome == "deny"


def test_a_keyed_act_and_an_unkeyed_act_never_match(engine):
    assert _dedupe(engine.check(_payment(1, equivalence_key=RAW_KEY))) == "pass"
    assert _dedupe(engine.check(_payment(2))) == "pass"


def test_a_key_equal_to_the_formula_digest_does_not_match_an_unkeyed_record(engine):
    """The key is digested before it is compared, so a caller cannot name an
    unkeyed record's formula digest and collide with it."""
    formula = equivalence_key_for_action(_payment(1))
    assert _dedupe(engine.check(_payment(1))) == "pass"
    assert _dedupe(engine.check(_payment(2, equivalence_key=formula))) == "pass"


def test_a_replayed_keyed_record_is_keyed_as_it_was_live(engine):
    capsule = engine.check(_payment(1, equivalence_key=RAW_KEY)).capsule
    replayed = Action.from_capsule(capsule)
    assert replayed.equivalence_key is None
    assert replayed.equivalence_key_digest == json_digest(RAW_KEY)
    assert equivalence_key_for_action(replayed) == equivalence_key_for_capsule(capsule)


def test_an_action_cannot_carry_both_the_key_and_a_digest():
    with pytest.raises(ValueError, match="both set"):
        _payment(1, equivalence_key=RAW_KEY, equivalence_key_digest=json_digest(RAW_KEY))
