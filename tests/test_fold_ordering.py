# SPDX-License-Identifier: Apache-2.0
"""GRC folds batch item 1: the ordering fold (EU-50-1/50-3/26-7/27). Every
negative case flips exactly one thing and confirms the mutant is caught."""
from __future__ import annotations

from capsule_engine.folds.ordering import evaluate_ordering


def _disclosure(session, idx):
    return {"session_id": session, "kind": "interaction.disclosure", "seq": idx}


def _turn(session, idx):
    return {"session_id": session, "kind": "agent.turn", "seq": idx}


def _is_disclosure(record):
    return record.get("kind") == "interaction.disclosure"


def _is_first_turn(record):
    return record.get("kind") == "agent.turn"


def test_disclosure_before_first_turn_is_met():
    records = [_disclosure("s1", 0), _turn("s1", 1)]
    result = evaluate_ordering(records, session_key="session_id", before=_is_disclosure, after=_is_first_turn)
    assert result.total_sessions == 1
    assert result.sessions[0].verdict == "met"
    assert result.met_count == 1


def test_same_record_satisfying_both_is_met_not_not_met():
    records = [{"session_id": "s1", "kind": "interaction.disclosure_and_turn"}]

    def both(record):
        return True

    result = evaluate_ordering(records, session_key="session_id", before=both, after=both)
    assert result.sessions[0].verdict == "met"


def test_gated_event_with_no_prior_disclosure_is_not_met():
    records = [_turn("s1", 0)]
    result = evaluate_ordering(records, session_key="session_id", before=_is_disclosure, after=_is_first_turn)
    assert result.sessions[0].verdict == "not_met"
    assert "no prior disclosure" in result.sessions[0].detail


def test_gated_event_not_yet_reached_is_insufficient_evidence_not_a_grade():
    records = [_disclosure("s1", 0)]
    result = evaluate_ordering(records, session_key="session_id", before=_is_disclosure, after=_is_first_turn)
    assert result.sessions[0].verdict == "insufficient_evidence"


def test_missing_session_key_is_skipped_not_an_error():
    records = [{"kind": "interaction.disclosure"}, _turn("s1", 1)]
    result = evaluate_ordering(records, session_key="session_id", before=_is_disclosure, after=_is_first_turn)
    assert result.skipped_count == 1
    assert result.considered_count == 2


def test_multiple_sessions_are_independent():
    records = [
        _disclosure("s1", 0),
        _turn("s1", 1),
        _turn("s2", 2),  # s2: gated event with no disclosure at all
    ]
    result = evaluate_ordering(records, session_key="session_id", before=_is_disclosure, after=_is_first_turn)
    by_session = {s.session: s.verdict for s in result.sessions}
    assert by_session == {"s1": "met", "s2": "not_met"}


def test_mutant_swap_order_flips_met_to_not_met():
    """The report's own mutant: swap the disclosure and the gated event ->
    not_met. Base ordering (disclosure then turn) is met; swapping the two
    records' ledger positions must flip the verdict."""
    base = [_disclosure("s1", 0), _turn("s1", 1)]
    mutant = [_turn("s1", 0), _disclosure("s1", 1)]  # swapped order

    base_result = evaluate_ordering(base, session_key="session_id", before=_is_disclosure, after=_is_first_turn)
    mutant_result = evaluate_ordering(mutant, session_key="session_id", before=_is_disclosure, after=_is_first_turn)

    assert base_result.sessions[0].verdict == "met"
    assert mutant_result.sessions[0].verdict == "not_met"
