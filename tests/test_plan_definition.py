# SPDX-License-Identifier: Apache-2.0
"""C1: the compiled-plan artifact -- ``PlanDefinition``'s parsing, digesting,
and validation (``capsule_engine.guards.plan``)."""
from __future__ import annotations

import pytest

from capsule_engine.guards.plan import (
    PlanDefinition,
    PlanDefinitionError,
    PlanPrecondition,
    parse_plan_definition,
)

VALID_PLAN: dict = {
    "outcome_id": "workforce.remediation_completed/1.0.0",
    "allowed_actions": ["read_user_directory", "send_enrollment_link", "enable_mfa", "verify_mfa_state"],
    "preconditions": [{"action": "enable_mfa", "citing": "agreement_judgment"}],
    "binding": {"subject": "employee-4471"},
    "window": "session",
}


def test_parses_a_valid_plan():
    plan = parse_plan_definition(VALID_PLAN)
    assert plan.outcome_id == "workforce.remediation_completed/1.0.0"
    assert plan.allowed_actions == ("read_user_directory", "send_enrollment_link", "enable_mfa", "verify_mfa_state")
    assert plan.preconditions == (PlanPrecondition(action="enable_mfa", citing="agreement_judgment"),)
    assert plan.binding == {"subject": "employee-4471"}
    assert plan.window == "session"


def test_step_index_and_precondition_lookup():
    plan = parse_plan_definition(VALID_PLAN)
    assert plan.step_index("read_user_directory") == 0
    assert plan.step_index("enable_mfa") == 2
    assert plan.step_index("export_user_list") is None
    assert plan.precondition_for("enable_mfa").citing == "agreement_judgment"
    assert plan.precondition_for("read_user_directory") is None


def test_definition_digest_is_deterministic_and_key_order_independent():
    plan_a = parse_plan_definition(VALID_PLAN)
    reordered = {
        "window": "session",
        "binding": {"subject": "employee-4471"},
        "preconditions": [{"citing": "agreement_judgment", "action": "enable_mfa"}],
        "allowed_actions": list(VALID_PLAN["allowed_actions"]),
        "outcome_id": VALID_PLAN["outcome_id"],
    }
    plan_b = parse_plan_definition(reordered)
    assert plan_a.definition_digest() == plan_b.definition_digest()


def test_definition_digest_changes_when_allowed_actions_change():
    plan_a = parse_plan_definition(VALID_PLAN)
    mutated = dict(VALID_PLAN)
    mutated["allowed_actions"] = [*VALID_PLAN["allowed_actions"], "export_user_list"]
    # export_user_list is unguarded by a precondition, so this stays valid.
    plan_b = parse_plan_definition(mutated)
    assert plan_a.definition_digest() != plan_b.definition_digest()


def test_allowed_set_digest_is_narrower_than_the_full_plan_digest():
    plan_a = parse_plan_definition(VALID_PLAN)
    mutated = dict(VALID_PLAN)
    mutated["binding"] = {"subject": "employee-9999"}
    plan_b = parse_plan_definition(mutated)
    # Same allowed-action set, different binding: allowed_set_digest matches,
    # full plan digest does not.
    assert plan_a.allowed_set_digest() == plan_b.allowed_set_digest()
    assert plan_a.definition_digest() != plan_b.definition_digest()


def test_canonical_dict_omits_window_when_absent_not_null():
    plan = PlanDefinition(outcome_id="a.b/1.0.0", allowed_actions=("do_thing",))
    assert "window" not in plan.canonical_dict()


# -- admitted-action-space (over-breadth) measure ---------------------------


def test_admitted_action_space_size_counts_allowed_actions():
    plan = parse_plan_definition(VALID_PLAN)
    assert plan.admitted_action_space_size() == 4


def test_admitted_action_space_size_grows_with_a_broader_plan():
    narrow = parse_plan_definition(VALID_PLAN)
    broad_data = dict(VALID_PLAN)
    broad_data["allowed_actions"] = [*VALID_PLAN["allowed_actions"], "export_user_list"]
    broad = parse_plan_definition(broad_data)
    assert broad.admitted_action_space_size() == narrow.admitted_action_space_size() + 1


def test_admitted_action_space_size_is_sealed_to_the_frozen_definition():
    """Same measure every call -- a pure function of the frozen dataclass's
    own ``allowed_actions`` field, not re-derived from anything mutable."""
    plan = parse_plan_definition(VALID_PLAN)
    assert plan.admitted_action_space_size() == plan.admitted_action_space_size() == len(plan.allowed_actions)


@pytest.mark.parametrize(
    "mutation,reason",
    [
        ({"outcome_id": "not-a-valid-id"}, "invalid_outcome_id"),
        ({"allowed_actions": []}, "empty_allowed_actions"),
        ({"allowed_actions": ["Not_Lowercase"]}, "invalid_action_verb"),
        ({"allowed_actions": ["enable_mfa", "enable_mfa"]}, "duplicate_allowed_action"),
        ({"preconditions": [{"action": "export_user_list", "citing": "x"}]}, "precondition_action_not_allowed"),
        ({"preconditions": [{"action": "enable_mfa"}]}, "invalid_precondition"),
        ({"binding": "not-a-mapping"}, "malformed_definition"),
    ],
)
def test_rejects_invalid_plans(mutation, reason):
    data = dict(VALID_PLAN)
    data.update(mutation)
    with pytest.raises(PlanDefinitionError) as excinfo:
        parse_plan_definition(data)
    assert excinfo.value.reason == reason


def test_rejects_non_mapping_input():
    with pytest.raises(PlanDefinitionError) as excinfo:
        parse_plan_definition(["not", "a", "mapping"])
    assert excinfo.value.reason == "malformed_definition"
