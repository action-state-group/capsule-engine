# SPDX-License-Identifier: Apache-2.0
"""GRC folds batch item 3: the record-type coverage fold (EU-12-2)."""
from __future__ import annotations

from capsule_engine.folds.record_type_coverage import evaluate_record_type_coverage

REGISTERED = frozenset({"action.executed", "hold.reserve", "incident.flag"})


def _records(kinds):
    return [{"kind": k} for k in kinds]


def test_all_registered_kinds_present_is_met():
    result = evaluate_record_type_coverage(
        _records(["action.executed", "hold.reserve", "incident.flag", "unregistered.kind"]),
        registered_kinds=REGISTERED,
    )
    assert result.verdict == "met"
    assert result.present == REGISTERED
    assert result.missing == frozenset()


def test_empty_registered_kinds_is_a_population_exclusion_not_a_verdict():
    """Nothing declared means the requirement is excluded from the evaluated
    population: no verdict at all, and in particular never a vacuous met."""
    result = evaluate_record_type_coverage(_records(["anything"]), registered_kinds=frozenset())
    assert result.applicable is False
    assert result.verdict is None


def test_declared_kinds_are_applicable_and_carry_a_verdict():
    result = evaluate_record_type_coverage([], registered_kinds=REGISTERED)
    assert result.applicable is True
    assert result.verdict == "not_met"


def test_no_records_with_registered_kinds_is_not_met():
    result = evaluate_record_type_coverage([], registered_kinds=REGISTERED)
    assert result.verdict == "not_met"
    assert result.missing == REGISTERED


def test_records_missing_kind_field_are_not_counted_as_present():
    result = evaluate_record_type_coverage([{"no_kind_here": True}], registered_kinds=REGISTERED)
    assert result.verdict == "not_met"
    assert result.present == frozenset()


def test_mutant_removing_all_records_of_one_kind_flips_met_to_not_met():
    base = _records(["action.executed", "hold.reserve", "incident.flag"])
    mutant = [r for r in base if r["kind"] != "incident.flag"]  # remove one registered kind entirely

    base_result = evaluate_record_type_coverage(base, registered_kinds=REGISTERED)
    mutant_result = evaluate_record_type_coverage(mutant, registered_kinds=REGISTERED)

    assert base_result.verdict == "met"
    assert mutant_result.verdict == "not_met"
    assert mutant_result.missing == frozenset({"incident.flag"})
