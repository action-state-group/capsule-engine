# SPDX-License-Identifier: Apache-2.0
"""C2: ``plan_containment`` check -- pass/fail/n-a semantics, evidence shape,
and the lock-independence property the design doc cites as the reason this
check may ship into enforce mode ahead of ``[ldg-guardengine-caps-race]``."""
from __future__ import annotations

import inspect

from capsule_engine.guards.action import Action
from capsule_engine.guards.checks.plan_containment import check_plan_containment
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


def _action(verb: str, **kwargs) -> Action:
    return Action(verb=verb, operator="acme-corp", developer="security-assistant@v1", target="employee-4471", **kwargs)


# -- lock-independence: the property under test, not a comment -------------


def test_check_signature_takes_no_ledger_argument():
    """The whole reason this check may ship into enforce mode ahead of the
    caps-race fix: it is structurally incapable of reading ledger state,
    because it is never handed anything to read it from. A mutant that
    reintroduces a ledger dependency (even an optional one) changes this
    signature and fails this assertion."""
    sig = inspect.signature(check_plan_containment)
    assert set(sig.parameters) == {"action", "plan"}


def test_result_is_a_pure_function_of_action_and_plan_alone():
    """Calling the check repeatedly, with no ledger/store in scope anywhere
    in this test, must always produce the same result -- there is no read-
    decide-append window to race because there is no read at all."""
    action = _action("enable_mfa", cited_mandate_capsule_id="a" * 64)
    results = [check_plan_containment(action, PLAN).constraint.result for _ in range(5)]
    assert results == ["pass"] * 5


# -- n/a: no plan bound ------------------------------------------------------


def test_no_plan_bound_is_not_applicable():
    outcome = check_plan_containment(_action("enable_mfa"), None)
    assert outcome.constraint.result == "n/a"
    assert outcome.constraint.id == "plan_containment"


# -- pass: in the allowed set, no precondition ------------------------------


def test_verb_in_allowed_set_with_no_precondition_passes():
    outcome = check_plan_containment(_action("read_user_directory"), PLAN)
    assert outcome.constraint.result == "pass"
    assert outcome.constraint.evidence["step_index"] == 0
    assert outcome.constraint.evidence["outcome_id"] == PLAN.outcome_id
    assert outcome.constraint.evidence["plan_digest"] == PLAN.definition_digest()
    assert outcome.constraint.evidence["allowed_set_digest"] == PLAN.allowed_set_digest()


# -- pass: precondition satisfied by a cited capsule id ---------------------


def test_precondition_satisfied_by_citation_passes():
    outcome = check_plan_containment(_action("enable_mfa", cited_mandate_capsule_id="b" * 64), PLAN)
    assert outcome.constraint.result == "pass"
    assert outcome.constraint.evidence["step_index"] == 2
    assert outcome.constraint.evidence["cited_capsule_id"] == "b" * 64
    assert outcome.constraint.evidence["satisfied_precondition"] == "agreement_judgment"


# -- fail: departure -- verb outside the allowed set ------------------------


def test_verb_outside_allowed_set_fails_as_a_departure():
    outcome = check_plan_containment(_action("export_user_list"), PLAN)
    assert outcome.constraint.result == "fail"
    assert outcome.constraint.evidence["attempted_verb"] == "export_user_list"
    assert outcome.constraint.evidence["step_index"] is None
    assert outcome.constraint.evidence["reason_kind"] == "not_in_allowed_set"
    # Vocabulary discipline (lines to hold): never "drift", never "guarantee".
    assert "drift" not in outcome.constraint.reason
    assert "guarantee" not in outcome.constraint.reason


# -- fail: precondition required but not cited -------------------------------


def test_precondition_required_but_uncited_fails():
    outcome = check_plan_containment(_action("enable_mfa"), PLAN)
    assert outcome.constraint.result == "fail"
    assert outcome.constraint.evidence["reason_kind"] == "precondition_uncited"
    assert outcome.constraint.evidence["requires_citation_for"] == "agreement_judgment"


# -- fail: binding mismatch ---------------------------------------------------


def test_binding_mismatch_fails():
    action = Action(
        verb="read_user_directory", operator="acme-corp", developer="security-assistant@v1", target="employee-9999"
    )
    outcome = check_plan_containment(action, PLAN)
    assert outcome.constraint.result == "fail"
    assert outcome.constraint.evidence["reason_kind"] == "binding_mismatch"
