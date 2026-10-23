# SPDX-License-Identifier: Apache-2.0
"""capsule-emit #52: expiry terminally resolves; resume re-evaluates.

Acceptance criteria: hold-semantics spec §#52.
"""
from __future__ import annotations

from agent_action_capsule import Finding, VerificationResult

from capsule_engine.guards import Action
from capsule_engine.holds import HoldStatus
from capsule_engine.holds.errors import HOLD_ALREADY_TERMINAL, HOLD_STATUS_AMBIGUOUS, RECONCILE_AFTER_EXPIRY

DEVELOPER = "procurement-agent@v1"
OPERATOR = "acme-research"


def _action(amount_minor=100, action_id=None, target=None):
    return Action(
        verb="transfer_funds", operator=OPERATOR, developer=DEVELOPER, action_class="money.transfer",
        amount_minor=amount_minor, currency="EUR", action_id=action_id, target=target,
    )


# -- #52.1: expiry is TERMINAL -----------------------------------------------


def test_expire_is_terminal_further_lifecycle_calls_deny(hold_engine):
    reserve = hold_engine.evaluate_and_reserve(_action(amount_minor=1_000, target="acct-1"))
    assert reserve.outcome == "allow"
    reserve_id = reserve.capsule["capsule_id"]

    expired = hold_engine.expire(reserve_id, reason="ttl elapsed")
    assert expired.outcome == "allow"
    assert expired.hold_status == HoldStatus.EXPIRED

    # nothing may dispatch citing the original evaluation after this
    again_expire = hold_engine.expire(reserve_id)
    assert again_expire.outcome == "deny"
    assert again_expire.hold_status == HoldStatus.EXPIRED

    release_attempt = hold_engine.release(reserve_id)
    assert release_attempt.outcome == "deny"

    reconcile_attempt = hold_engine.reconcile(reserve_id, action_class="money.transfer", executed_amount_minor=1_000)
    assert reconcile_attempt.outcome == "deny"
    assert reconcile_attempt.reason_code == RECONCILE_AFTER_EXPIRY


# -- #52.2: resume = a fresh evaluate_and_reserve, same atomic path ---------


def test_resume_after_expiry_is_a_fresh_evaluate_and_reserve_no_special_path(hold_engine, store, hold_fold):
    reserve = hold_engine.evaluate_and_reserve(_action(amount_minor=1_000, target="acct-1"))
    reserve_id = reserve.capsule["capsule_id"]
    hold_engine.expire(reserve_id)

    # HoldEngine exposes no resume(reserve_id)-shaped method at all -- the
    # only way forward is calling evaluate_and_reserve() again, through the
    # SAME atomic (scope-locked, fold-evaluating) path as any other action.
    assert not hasattr(hold_engine, "resume")

    resumed = hold_engine.evaluate_and_reserve(_action(amount_minor=1_000, target="acct-1"))
    assert resumed.outcome == "allow"
    assert resumed.capsule["capsule_id"] != reserve_id
    assert resumed.capsule["action_id"].startswith("hold.reserve/")

    # the fresh reservation is a genuinely new, independent hold: the fold
    # aggregate reflects it (the expired hold's own exposure already netted
    # to zero), not a reactivation of the old one.
    from capsule_engine.folds.engine import evaluate_one

    records = [r.capsule for r in store.scan()]
    trace = evaluate_one(hold_fold, records, key_value=DEVELOPER)
    assert trace.result == 1_000


# -- #52.3 / #52.5: the four-step breach sequence + mutant ------------------


RESERVE_AMOUNT = 9_500_000  # holds.yaml's money.transfer cap is 10,000,000 minor units
OTHER_AMOUNT = 9_800_000  # consumes nearly all remaining headroom after the reserve expires


def _breach_sequence(hold_engine):
    """Steps 1-4 from the issue's own breach sequence:
    1. reserve a hold for 9,500,000 (minor units) against the 10,000,000 cap.
    2. the hold expires.
    3. other, unrelated activity consumes most of the now-freed headroom
       (time passed; the world moved on) -- large enough that a later resume
       of the original amount would no longer fit.
    4. a late approval/dispatch arrives, attempting to convert against the
       ORIGINAL (now expired) evaluation -- must deny, citing re-evaluation,
       chained to the expiry.
    Returns (reserve_id, expire_capsule, step4_decision).
    """
    reserve = hold_engine.evaluate_and_reserve(
        _action(amount_minor=RESERVE_AMOUNT, action_id="transfer_funds/1", target="acct-1")
    )
    assert reserve.outcome == "allow"
    reserve_id = reserve.capsule["capsule_id"]

    expired = hold_engine.expire(reserve_id, reason="ttl elapsed while awaiting approval")
    assert expired.outcome == "allow"

    other = hold_engine.evaluate_and_reserve(
        _action(amount_minor=OTHER_AMOUNT, action_id="transfer_funds/2", target="acct-2")
    )
    assert other.outcome == "allow"

    step4 = hold_engine.reconcile(reserve_id, action_class="money.transfer", executed_amount_minor=RESERVE_AMOUNT)
    return reserve_id, expired.capsule, step4


def test_breach_sequence_step4_denies_citing_re_evaluation_chained_to_expiry(hold_engine):
    reserve_id, expire_capsule, step4 = _breach_sequence(hold_engine)

    assert step4.outcome == "deny"
    assert step4.reason_code == RECONCILE_AFTER_EXPIRY
    # machine-readable (reason_code) AND human-readable (HoldDecision.reason,
    # the caller-facing string -- ConstraintRecord itself carries no free-text
    # reason field on the sealed capsule) both cite re-evaluation.
    assert "evaluate_and_reserve" in step4.reason
    assert step4.capsule["chain"]["parent_capsule_id"] == expire_capsule["capsule_id"]
    assert step4.capsule["chain"]["relation"] == "confirms"

    # the correct path forward: a fresh evaluate_and_reserve, which -- since
    # the remaining cap was already consumed by other activity (step 3) --
    # must itself deny/escalate, not silently succeed.
    resumed = hold_engine.evaluate_and_reserve(
        _action(amount_minor=RESERVE_AMOUNT, action_id="transfer_funds/1-resume", target="acct-1")
    )
    assert resumed.outcome in ("deny", "escalate")


def test_breach_sequence_mutant_disabling_re_evaluation_flips_resume_to_allow(hold_engine, monkeypatch):
    """Mutant test (#52.5): disable the re-evaluation step (the real fold
    check inside evaluate_and_reserve) and confirm the resume half of the
    breach-sequence test above would then wrongly ALLOW -- proving that step
    is load-bearing, not decorative. Restored automatically by pytest's
    monkeypatch teardown."""
    reserve_id, expire_capsule, step4 = _breach_sequence(hold_engine)
    assert step4.outcome == "deny"  # step 4 itself is unaffected by this mutant

    import capsule_engine.holds.engine as holds_engine_module
    from capsule_engine.guards.capsule import ConstraintOutcome
    from capsule_engine.guards.checks import CheckOutcome

    def _always_pass_caps(action, ledger, *, definition, cap_minor, since=None, as_of=None):
        return CheckOutcome(
            constraint=ConstraintOutcome(id="caps", result="pass", reason="mutant: re-evaluation disabled"),
        )

    monkeypatch.setattr(holds_engine_module, "check_caps", _always_pass_caps)

    resumed_mutant = hold_engine.evaluate_and_reserve(
        _action(amount_minor=RESERVE_AMOUNT, action_id="transfer_funds/1-resume-mutant", target="acct-1")
    )
    assert resumed_mutant.outcome == "allow", (
        "mutant did not flip the resume outcome -- the re-evaluation step is not load-bearing"
    )


# -- #52.4: ambiguous expiry status fails closed -----------------------------


def test_hold_status_ambiguous_when_reserve_record_missing(hold_engine):
    status, terminal = hold_engine.hold_status("0" * 64)
    assert status == HoldStatus.AMBIGUOUS
    assert terminal is None


class _ForcedVerifyFailure:
    """Wraps a real ledger, forcing ``verify()`` to fail for one capsule_id --
    simulates the clock-skew/corruption ambiguity case (#52.4) through the
    documented ``LedgerAPI`` extension point rather than on-disk tampering."""

    def __init__(self, inner, fail_for: str):
        self._inner = inner
        self._fail_for = fail_for

    def append(self, *a, **k):
        return self._inner.append(*a, **k)

    def scan(self, *a, **k):
        return self._inner.scan(*a, **k)

    def fetch(self, *a, **k):
        return self._inner.fetch(*a, **k)

    def find_gaps(self, *a, **k):
        return self._inner.find_gaps(*a, **k)

    def verify(self, capsule_id):
        if capsule_id == self._fail_for:
            return VerificationResult(ok=False, findings=[Finding("simulated_ambiguity", "forced failure for test")])
        return self._inner.verify(capsule_id)


def test_ambiguous_terminal_record_fails_closed_for_consequential_class(store, hold_fold, signer):
    from capsule_engine.holds import HoldEngine

    real_engine = HoldEngine(
        ledger=store, hold_fold=hold_fold, fold_digest=hold_fold.definition_digest(),
        signer_provider=lambda: signer, cap_minor={"money.transfer": 1_000_000},
    )
    reserve = real_engine.evaluate_and_reserve(_action(amount_minor=1_000, target="acct-1"))
    reserve_id = reserve.capsule["capsule_id"]
    expired = real_engine.expire(reserve_id)
    expire_id = expired.capsule["capsule_id"]

    wrapped = _ForcedVerifyFailure(store, fail_for=expire_id)
    ambiguous_engine = HoldEngine(
        ledger=wrapped, hold_fold=hold_fold, fold_digest=hold_fold.definition_digest(),
        signer_provider=lambda: signer, cap_minor={"money.transfer": 1_000_000},
    )

    status, terminal = ambiguous_engine.hold_status(reserve_id)
    assert status == HoldStatus.AMBIGUOUS
    assert terminal is not None and terminal["capsule_id"] == expire_id

    # ambiguous is never silently treated as active: every lifecycle op
    # still fails closed (deny), not "proceed as if this hold is active".
    release_attempt = ambiguous_engine.release(reserve_id)
    assert release_attempt.outcome == "deny"
    assert release_attempt.reason_code in (HOLD_ALREADY_TERMINAL, HOLD_STATUS_AMBIGUOUS)
