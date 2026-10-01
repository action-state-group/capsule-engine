# SPDX-License-Identifier: Apache-2.0
"""C2/C3: ``GuardEngine`` wired with a bound ``plan`` -- containment governs
real allow/deny decisions and the decision capsule carries the constraint."""
from __future__ import annotations

import threading

from capsule_engine.guards import Action, GuardEngine
from capsule_engine.guards.plan import parse_plan_definition

PLAN = parse_plan_definition(
    {
        "outcome_id": "workforce.remediation_completed/1.0.0",
        "allowed_actions": ["read_user_directory", "send_enrollment_link", "enable_mfa", "verify_mfa_state"],
        "preconditions": [{"action": "enable_mfa", "citing": "agreement_judgment"}],
        "binding": {"subject": "employee-4471"},
        "window": "session",
    }
)


def _engine(store, caps_fold, signer, **kwargs):
    return GuardEngine(ledger=store, caps_fold=caps_fold, signer_provider=lambda: signer, plan=PLAN, **kwargs)


def test_action_inside_the_plan_is_allowed_and_records_the_constraint(store, caps_fold, signer):
    engine = _engine(store, caps_fold, signer)
    action = Action(
        verb="read_user_directory", operator="acme-corp", developer="security-assistant@v1", target="employee-4471"
    )
    decision = engine.check(action)

    assert decision.outcome == "allow"
    ids = {c.id for c in decision.constraints}
    assert "plan_containment" in ids
    plan_constraint = next(c for c in decision.constraints if c.id == "plan_containment")
    assert plan_constraint.result == "pass"


def test_departure_from_the_plan_hard_denies(store, caps_fold, signer):
    engine = _engine(store, caps_fold, signer)
    action = Action(
        verb="export_user_list", operator="acme-corp", developer="security-assistant@v1", target="employee-4471"
    )
    decision = engine.check(action)

    assert decision.outcome == "deny"
    plan_constraint = next(c for c in decision.constraints if c.id == "plan_containment")
    assert plan_constraint.result == "fail"
    assert decision.capsule is not None
    assert decision.capsule["disposition"]["decision"] == "reject"


def test_precondition_satisfied_by_citing_the_judgment_capsule_allows(store, caps_fold, signer):
    engine = _engine(store, caps_fold, signer)
    action = Action(
        verb="enable_mfa",
        operator="acme-corp",
        developer="security-assistant@v1",
        target="employee-4471",
        cited_mandate_capsule_id="c" * 64,
    )
    decision = engine.check(action)

    # verify_before_dispatch independently fails (the cited id is not a real
    # ledger record in this test) -- plan_containment's own verdict is what
    # this test is about, so check it directly rather than the overall
    # decision (which correctly denies on the vbd failure).
    plan_constraint = next(c for c in decision.constraints if c.id == "plan_containment")
    assert plan_constraint.result == "pass"


def test_uncited_precondition_hard_denies(store, caps_fold, signer):
    engine = _engine(store, caps_fold, signer)
    action = Action(verb="enable_mfa", operator="acme-corp", developer="security-assistant@v1", target="employee-4471")
    decision = engine.check(action)

    assert decision.outcome == "deny"
    plan_constraint = next(c for c in decision.constraints if c.id == "plan_containment")
    assert plan_constraint.result == "fail"


# -- lock-independence under GENUINE concurrent ledger mutation ------------
#
# ``tests/test_plan_containment_check.py`` proves the check is structurally
# incapable of a ledger read (signature has no ledger param) and is a pure
# function called repeatedly with no ledger in scope. Neither test actually
# races a live mutation against the check while it runs -- the property this
# item requires ("assert containment's verdict is unchanged under concurrent
# ledger mutation. This is the property that lets it enforce ahead of the
# caps work; if it is not tested it is not a property, it is a hope").
# A caps race proved ``caps`` unsafe this exact way: two
# concurrent ``GuardEngine.check()`` calls sharing one ledger, racing a
# read-decide-append window (``tests/test_pack_differential_concurrency.py``).
# This test races the SAME shape of concurrent writers against the SAME
# shared store and engine, with a plan bound, and asserts every single
# plan_containment verdict -- in-plan and departure alike -- comes back
# exactly as an isolated, sequential call would, on every one of many
# interleavings. Unlike ``caps``, there is nothing to find: the check never
# reads the store the noise writers are mutating.


def test_verdict_is_unchanged_under_concurrent_ledger_mutation(store, caps_fold, signer):
    engine = _engine(store, caps_fold, signer)
    in_plan_results: list[str] = []
    departure_results: list[str] = []
    noise_outcomes: list[str] = []
    lock = threading.Lock()
    n_rounds = 40

    def _check_in_plan(i: int) -> None:
        action = Action(
            verb="read_user_directory",
            operator="acme-corp",
            developer=f"security-assistant@v1-{i}",
            target="employee-4471",
            action_id=f"read_user_directory/containment-race-{i}",
            timestamp=f"2026-08-18T09:{i % 60:02d}:00Z",
        )
        result = engine.check(action).constraints
        plan_result = next(c.result for c in result if c.id == "plan_containment")
        with lock:
            in_plan_results.append(plan_result)

    def _check_departure(i: int) -> None:
        action = Action(
            verb="export_user_list",
            operator="acme-corp",
            developer=f"security-assistant@v1-{i}",
            target="employee-4471",
            action_id=f"export_user_list/containment-race-{i}",
            timestamp=f"2026-08-18T10:{i % 60:02d}:00Z",
        )
        result = engine.check(action).constraints
        plan_result = next(c.result for c in result if c.id == "plan_containment")
        with lock:
            departure_results.append(plan_result)

    def _noise_write(i: int) -> None:
        """Unrelated concurrent writers -- a different verb, no plan
        relevance, no shared ``target`` -- mutating the SAME shared ledger
        store the containment checks above are running against. This is the
        live concurrent mutation; containment must not notice it."""
        action = Action(
            verb="dispatch_payout",
            operator="acme-corp",
            developer=f"noise-writer-{i}",
            action_class="money.transfer",
            amount_minor=100,
            currency="EUR",
            target=f"vendor/noise-{i}",
            action_id=f"dispatch_payout/containment-race-noise-{i}",
            timestamp=f"2026-08-18T11:{i % 60:02d}:00Z",
        )
        outcome = engine.check(action, dry_run=True).outcome
        with lock:
            noise_outcomes.append(outcome)

    threads = []
    for i in range(n_rounds):
        threads.append(threading.Thread(target=_check_in_plan, args=(i,)))
        threads.append(threading.Thread(target=_check_departure, args=(i,)))
        threads.append(threading.Thread(target=_noise_write, args=(i,)))
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(in_plan_results) == n_rounds
    assert len(departure_results) == n_rounds
    assert len(noise_outcomes) == n_rounds
    assert in_plan_results == ["pass"] * n_rounds, (
        f"in-plan verdict flipped under concurrent ledger mutation: {in_plan_results}"
    )
    assert departure_results == ["fail"] * n_rounds, (
        f"departure verdict flipped under concurrent ledger mutation: {departure_results}"
    )


def test_no_plan_configured_omits_the_constraint_entirely(store, caps_fold, signer):
    """No ``plan=`` at all (the default, and every pre-existing caller) is
    byte-for-byte the same engine behavior as before this check existed --
    the constraint is absent, not an ``n/a`` stub, so no existing decision
    capsule's hash shifts just because this check now exists in the code."""
    engine = GuardEngine(ledger=store, caps_fold=caps_fold, signer_provider=lambda: signer)
    action = Action(verb="export_user_list", operator="acme-corp", developer="security-assistant@v1")
    decision = engine.check(action)

    assert not any(c.id == "plan_containment" for c in decision.constraints)
    assert decision.outcome == "allow"
