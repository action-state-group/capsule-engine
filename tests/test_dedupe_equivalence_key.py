# SPDX-License-Identifier: Apache-2.0
"""``Action.equivalence_key`` keys only the action side of dedupe.

A caller's key replaces the default formula for the action being checked
(``equivalence_key_for_action``), but it is not sealed on the decision
capsule, so the earlier record is always keyed by the default formula
(``equivalence_key_for_capsule``). A caller's key therefore never matches
an earlier record that carried the same key: it can make two acts distinct,
never the same. An exact retry under the same key passes dedupe.

That is a latent defect for a caller using the key as an idempotency key, as
the packs' bootstrap notes invite. Fixing it means sealing the key (or a
digest of it) and reading it back on the capsule side, a new locally matched
field on every decision that sets one, which needs the share boundary
reviewed first (AGENTS.md). Until then these tests pin what the engine does,
and the strict ``xfail`` states what it should do, so the fix flips it.
"""
from __future__ import annotations

import pytest
from capsule_ledger.ledger import LedgerStore

from capsule_engine.guards import Action, GuardEngine, LocalSigner
from capsule_engine.guards.checks.dedupe import equivalence_key_for_action, equivalence_key_for_capsule

SIGNER = LocalSigner(key_id="dedupe-equivalence-key", secret=b"dedupe-equivalence-key-fixed-key")


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


def test_the_caller_key_is_not_sealed_so_the_record_is_keyed_by_the_default_formula(engine):
    action = _payment(1, equivalence_key="order-123")
    capsule = engine.check(action).capsule
    assert "equivalence_key" not in capsule["asg_payload"]
    assert equivalence_key_for_action(action) == "order-123"
    assert equivalence_key_for_capsule(capsule) == equivalence_key_for_action(_payment(1))


def test_without_a_caller_key_the_same_act_repeated_fails_dedupe(engine):
    assert _dedupe(engine.check(_payment(1))) == "pass"
    assert _dedupe(engine.check(_payment(2))) == "fail"


def test_a_caller_key_keeps_two_acts_distinct(engine):
    """What the everyday bootstrap note relies on: two genuinely different
    payments to one payee, told apart by their keys."""
    assert _dedupe(engine.check(_payment(1, equivalence_key="bill-2026-09"))) == "pass"
    assert _dedupe(engine.check(_payment(2, equivalence_key="bill-2026-10"))) == "pass"


def test_today_an_exact_retry_under_the_same_caller_key_passes_dedupe(engine):
    assert _dedupe(engine.check(_payment(1, equivalence_key="order-123"))) == "pass"
    assert _dedupe(engine.check(_payment(2, equivalence_key="order-123"))) == "pass"


@pytest.mark.xfail(strict=True, reason="the caller key is not sealed, so the capsule side cannot match it")
def test_an_exact_retry_under_the_same_caller_key_fails_dedupe(engine):
    assert _dedupe(engine.check(_payment(1, equivalence_key="order-123"))) == "pass"
    assert _dedupe(engine.check(_payment(2, equivalence_key="order-123"))) == "fail"
