# SPDX-License-Identifier: Apache-2.0
"""C2/C3: ``GuardEngine`` wired with a bound ``plan`` -- containment governs
real allow/deny decisions and the decision capsule carries the constraint
(``[ldg-plan-containment]``)."""
from __future__ import annotations

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
