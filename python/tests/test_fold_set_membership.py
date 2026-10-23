# SPDX-License-Identifier: Apache-2.0
"""GRC folds batch item 5: the set-membership fold (EU-26-2, approver in
roster)."""
from __future__ import annotations

from capsule_engine.folds.set_membership import evaluate_set_membership

ROSTER = frozenset({"alice", "bob"})


def _approvals(approver_ids):
    return [{"approver_id": a} for a in approver_ids]


def test_approver_in_roster_is_met():
    result = evaluate_set_membership(_approvals(["alice", "bob"]), field="approver_id", roster=ROSTER)
    assert result.met_count == 2
    assert result.not_met_count == 0


def test_approver_not_in_roster_is_not_met():
    result = evaluate_set_membership(_approvals(["alice", "eve"]), field="approver_id", roster=ROSTER)
    assert result.met_count == 1
    assert result.not_met_count == 1
    assert result.records[1].value == "eve"
    assert result.records[1].verdict == "not_met"


def test_missing_field_is_skipped_not_an_error():
    result = evaluate_set_membership([{"no_approver_here": True}], field="approver_id", roster=ROSTER)
    assert result.skipped_count == 1
    assert result.considered_count == 1
    assert result.records == ()


def test_mutant_approver_swapped_out_of_roster_flips_met_to_not_met():
    base = _approvals(["alice", "bob"])
    mutant = _approvals(["alice", "mallory"])  # bob replaced with a non-roster approver

    base_result = evaluate_set_membership(base, field="approver_id", roster=ROSTER)
    mutant_result = evaluate_set_membership(mutant, field="approver_id", roster=ROSTER)

    assert base_result.not_met_count == 0
    assert mutant_result.not_met_count == 1
    assert mutant_result.records[1].verdict == "not_met"
