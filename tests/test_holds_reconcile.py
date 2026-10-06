# SPDX-License-Identifier: Apache-2.0
"""capsule-emit #53: planned != executed; reconcile as a record; over-tolerance
is a limit event.

Acceptance criteria: hold-semantics spec §#53.
"""
from __future__ import annotations

from capsule_engine.folds.engine import evaluate_one
from capsule_engine.guards import Action
from capsule_engine.holds import HoldStatus
from capsule_engine.holds.errors import OVER_TOLERANCE

DEVELOPER = "procurement-agent@v1"
OPERATOR = "acme-research"
TOLERANCE = 50_000  # holds.yaml's hold_reconcile wicket: money.transfer tolerance_minor


def _action(amount_minor=100, action_id=None, target=None):
    return Action(
        verb="transfer_funds", operator=OPERATOR, developer=DEVELOPER, action_class="money.transfer",
        amount_minor=amount_minor, currency="EUR", action_id=action_id, target=target,
    )


# -- #53.1: planned/executed/delta, chained to BOTH reserve and execution --


def test_reconcile_carries_planned_executed_delta_and_chains_to_both(hold_engine, store):
    reserve = hold_engine.evaluate_and_reserve(_action(amount_minor=1_000_000, target="acct-1"))
    assert reserve.outcome == "allow"
    reserve_id = reserve.capsule["capsule_id"]

    execution_capsule_id = "e" * 64  # opaque foreign-system reference (Jody's runtime, not this ledger's job)
    decision = hold_engine.reconcile(
        reserve_id, action_class="money.transfer", executed_amount_minor=1_020_000,
        execution_capsule_id=execution_capsule_id,
    )
    assert decision.outcome == "allow"
    payload = decision.capsule["asg_payload"]
    assert payload["reserved_amount_minor"] == 1_000_000
    assert payload["executed_amount_minor"] == 1_020_000
    assert payload["delta_minor"] == 20_000
    assert payload["tolerance_minor"] == TOLERANCE
    assert payload["execution_capsule_id"] == execution_capsule_id
    assert all(isinstance(payload[k], int) for k in ("reserved_amount_minor", "executed_amount_minor", "delta_minor", "tolerance_minor"))

    # chained to the reserve capsule via `chain` (the schema's one parent slot)
    assert decision.capsule["chain"]["parent_capsule_id"] == reserve_id
    # and to the execution capsule via the payload citation
    assert decision.capsule["asg_payload"]["execution_capsule_id"] == execution_capsule_id

    result = store.verify(decision.capsule["capsule_id"])
    assert result.ok, result.findings


# -- #53.2: tolerance is policy, not code -- traces to the manifest --------


def test_tolerance_traces_to_the_manifest_digest(hold_engine, resolved_holds_manifest):
    from capsule_engine.holds import resolve_hold_policy

    policy = resolve_hold_policy(resolved_holds_manifest)
    assert policy.tolerance_minor["money.transfer"] == TOLERANCE

    reserve = hold_engine.evaluate_and_reserve(_action(amount_minor=1_000_000, target="acct-1"))
    reserve_id = reserve.capsule["capsule_id"]
    decision = hold_engine.reconcile(reserve_id, action_class="money.transfer", executed_amount_minor=1_010_000)
    assert decision.outcome == "allow"
    assert decision.capsule["asg_payload"]["manifest_digest"] == resolved_holds_manifest.manifest_digest


# -- #53.3: over-tolerance is a limit event, routes existing vocabulary ----


def test_over_tolerance_escalates_when_approver_role_configured_never_adjusts_aggregate(hold_engine, store, hold_fold):
    reserve = hold_engine.evaluate_and_reserve(_action(amount_minor=1_000_000, target="acct-1"))
    reserve_id = reserve.capsule["capsule_id"]

    # money.transfer has an approver_role configured (guards/classes.py) --
    # an over-tolerance breach on it escalates, same D2 rule as caps.
    over = hold_engine.reconcile(reserve_id, action_class="money.transfer", executed_amount_minor=1_200_000)
    assert over.outcome == "escalate"
    assert over.reason_code == OVER_TOLERANCE

    # NEVER a *successful* reconcile for the over-tolerance attempt: the
    # capsule's own disposition is the escalate outcome (needs_input),
    # never "accept" -- which is also exactly why the hold.active_exposure
    # fold's own filter (disposition.decision == accept) never picks this
    # record up, regardless of its action_id verb.
    assert over.capsule["disposition"]["decision"] != "accept"
    assert "delta_minor" not in over.capsule["asg_payload"]  # not the hold.reconcile record shape at all
    assert over.capsule["chain"]["parent_capsule_id"] == reserve_id

    # aggregate is NOT silently adjusted to the (rejected) executed amount --
    # the hold is still just the original reservation, pending resolution.
    status, _ = hold_engine.hold_status(reserve_id)
    assert status == HoldStatus.ACTIVE
    records = [r.capsule for r in store.scan()]
    trace = evaluate_one(hold_fold, records, key_value=DEVELOPER)
    assert trace.result == 1_000_000


def test_over_tolerance_denies_when_no_approver_role_configured(store, hold_fold, signer):
    """A class with no approver_role hard-denies an over-tolerance breach,
    same as an unapproved cap breach (D2) -- `data.delete` has no configured
    approver in the starter taxonomy."""
    from capsule_engine.holds import HoldEngine

    engine = HoldEngine(
        ledger=store, hold_fold=hold_fold, fold_digest=hold_fold.definition_digest(),
        signer_provider=lambda: signer, cap_minor={"data.delete": 1_000_000}, tolerance_minor={"data.delete": 1_000},
    )
    action = Action(verb="delete_records", operator=OPERATOR, developer=DEVELOPER, action_class="data.delete", amount_minor=10_000)
    reserve = engine.evaluate_and_reserve(action)
    assert reserve.outcome == "allow"

    over = engine.reconcile(reserve.capsule["capsule_id"], action_class="data.delete", executed_amount_minor=50_000)
    assert over.outcome == "deny"
    assert over.reason_code == OVER_TOLERANCE


def test_over_tolerance_mutant_neutralizing_tolerance_flips_to_full_reconcile(hold_engine):
    """Mutant test: neutralize the tolerance value (the condition the
    over-tolerance check tests against) and confirm the same over-limit
    conversion that was denied/escalated now silently reconciles instead --
    proving the tolerance check is load-bearing."""
    reserve = hold_engine.evaluate_and_reserve(_action(amount_minor=1_000_000, target="acct-1"))
    reserve_id = reserve.capsule["capsule_id"]
    over = hold_engine.reconcile(reserve_id, action_class="money.transfer", executed_amount_minor=1_200_000)
    assert over.outcome == "escalate"

    reserve2 = hold_engine.evaluate_and_reserve(_action(amount_minor=1_000_000, target="acct-2"))
    reserve2_id = reserve2.capsule["capsule_id"]
    # mutant: the tolerance for this class is effectively removed (set so
    # large nothing can ever exceed it).
    hold_engine._tolerance_minor["money.transfer"] = 10**12
    mutant = hold_engine.reconcile(reserve2_id, action_class="money.transfer", executed_amount_minor=1_200_000)
    assert mutant.outcome == "allow", "mutant did not flip the outcome -- the tolerance check is not load-bearing"
    assert (mutant.capsule.get("action_id") or "").startswith("hold.reconcile/")


# -- #53.4: fold semantics -- planned while held, executed once reconciled --


def test_fold_semantics_across_partial_over_within_and_over_beyond_tolerance(store, hold_fold, signer):
    from capsule_engine.holds import HoldEngine

    engine = HoldEngine(
        ledger=store, hold_fold=hold_fold, fold_digest=hold_fold.definition_digest(),
        signer_provider=lambda: signer, cap_minor={"money.transfer": 100_000_000},
        tolerance_minor={"money.transfer": TOLERANCE},
    )

    def aggregate() -> int:
        records = [r.capsule for r in store.scan()]
        trace = evaluate_one(hold_fold, records, key_value=DEVELOPER)
        return trace.result or 0

    # -- partial fill: executed < reserved -------------------------------
    r1 = engine.evaluate_and_reserve(_action(amount_minor=1_000_000, action_id="t/1", target="acct-1"))
    assert r1.outcome == "allow"
    assert aggregate() == 1_000_000  # planned, while held
    c1 = engine.reconcile(r1.capsule["capsule_id"], action_class="money.transfer", executed_amount_minor=600_000)
    assert c1.outcome == "allow"
    assert aggregate() == 600_000  # executed, once reconciled

    # -- over-fill within tolerance ---------------------------------------
    r2 = engine.evaluate_and_reserve(_action(amount_minor=1_000_000, action_id="t/2", target="acct-2"))
    assert r2.outcome == "allow"
    assert aggregate() == 600_000 + 1_000_000
    c2 = engine.reconcile(r2.capsule["capsule_id"], action_class="money.transfer", executed_amount_minor=1_000_000 + TOLERANCE)
    assert c2.outcome == "allow"
    assert aggregate() == 600_000 + (1_000_000 + TOLERANCE)

    # -- over-fill beyond tolerance: aggregate is untouched ---------------
    r3 = engine.evaluate_and_reserve(_action(amount_minor=1_000_000, action_id="t/3", target="acct-3"))
    assert r3.outcome == "allow"
    before = aggregate()
    assert before == 600_000 + (1_000_000 + TOLERANCE) + 1_000_000
    c3 = engine.reconcile(r3.capsule["capsule_id"], action_class="money.transfer", executed_amount_minor=1_000_000 + TOLERANCE + 1)
    assert c3.outcome in ("deny", "escalate")
    assert aggregate() == before  # unchanged: the over-tolerance attempt never adjusts it

    # -- replay reproduces the aggregate at every stage --------------------
    # each `aggregate()` call above was already an independent fresh
    # `evaluate_one` over the full ledger-to-date (not a running accumulator
    # this test maintains itself) -- re-run it once more now and confirm a
    # brand new independent evaluation still reproduces the final value
    # byte-exactly, over the complete record set including every hold
    # capsule from every stage.
    records = [r.capsule for r in store.scan()]
    final_trace = evaluate_one(hold_fold, records, key_value=DEVELOPER)
    assert final_trace.result == before == aggregate()
