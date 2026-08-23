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
    assert outcome.constraint.evidence["admitted_action_space_size"] == PLAN.admitted_action_space_size() == 4


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


# -- re-derivability: labeled ON the record, and actually exercised ---------
#
# Design doc's own claim (§1): "hand a stranger the plan and the capsule and
# they re-derive the verdict." This test plays the stranger: it never reuses
# the original ``PLAN``/``Action`` Python objects, only what a holder of the
# disclosed plan (via the Disclosure Envelope) and the sealed action record
# (verb, target, cited-capsule-id -- exactly ``Action.from_capsule``'s own
# field set) would have. Contrast ``caps``, whose evidence is a function of
# ledger state read at decision time and so cannot be replayed this way from
# the record alone -- that asymmetry is why this check may enforce ahead of
# ``[ldg-guardengine-caps-race]`` and why it is called out explicitly rather
# than left implicit.


def test_evidence_carries_the_replay_class_label():
    outcome = check_plan_containment(_action("read_user_directory"), PLAN)
    assert outcome.constraint.evidence["replay_class"] == "sealed_pure"


def test_evidence_is_re_derivable_from_the_disclosed_record_alone():
    from agent_action_capsule.canonical import json_digest

    original_action = _action("enable_mfa", cited_mandate_capsule_id="d" * 64)
    original = check_plan_containment(original_action, PLAN)
    original_digest = json_digest(original.constraint.evidence)

    # A stranger's reconstruction: the plan re-parsed from its own
    # canonical_dict() (what the Disclosure Envelope would hand over, not
    # the live PLAN object), and the action rebuilt from only the fields a
    # sealed action record carries (mirrors ``Action.from_capsule``).
    disclosed_plan = parse_plan_definition(PLAN.canonical_dict())
    stranger_action = Action(
        verb=original_action.verb,
        operator=original_action.operator,
        developer=original_action.developer,
        target=original_action.target,
        cited_mandate_capsule_id=original_action.cited_mandate_capsule_id,
    )

    replay = check_plan_containment(stranger_action, disclosed_plan)
    replay_digest = json_digest(replay.constraint.evidence)

    assert replay.constraint.evidence == original.constraint.evidence
    assert replay_digest == original_digest
