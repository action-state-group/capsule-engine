# SPDX-License-Identifier: Apache-2.0
"""GRC folds batch item 2: the retention-continuity fold (EU-26-6/75). Builds
a REAL signed 3-checkpoint chain via ``capsule_emit`` (the same fixture shape
``capsule-emit``'s own ``tests/test_chain_segment.py`` uses) so the wrapped
``verify_chain_segment`` call does real, not mocked, verification --
nothing on this verdict path is trusted from a
party-under-test without independent verification.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from capsule_emit import seal, witness
from capsule_emit.chain_segment import ChainSegment, chain_segment
from capsule_emit.ledger import read_ledger_entries

from capsule_engine.folds.retention_continuity import evaluate_retention_continuity


@pytest.fixture(autouse=True)
def _clean_witness_state():
    witness._counts.clear()
    witness._armed_at.clear()
    witness._states.clear()
    witness._dispatch_locks.clear()
    witness._notice_printed = False
    yield
    witness._counts.clear()
    witness._armed_at.clear()
    witness._states.clear()
    witness._dispatch_locks.clear()
    witness._notice_printed = False


@pytest.fixture
def stub_witness(monkeypatch):
    monkeypatch.setenv("CAPSULE_WITNESS", "stub")


@pytest.fixture
def three_checkpoint_segment(tmp_path, stub_witness):
    """Three real, signed checkpoints, two capsules apiece -- every link
    verifiable in range. Mirrors capsule-emit's own ``three_checkpoint_ledger``
    fixture."""
    ledger_path = tmp_path / "ledger.jsonl"
    checkpoints = []
    for batch in range(3):
        for i in range(2):
            seal(None, action=f"batch{batch}-{i}", operator="acme", anchor=False, ledger=ledger_path)
        cp = witness.push(str(ledger_path))
        assert cp is not None
        checkpoints.append(cp)
    entries = list(read_ledger_entries(ledger_path))
    return chain_segment(entries, from_size=0, to_size=checkpoints[-1].mmr_size), checkpoints


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def test_unbroken_chain_within_a_trivially_short_window_is_met(three_checkpoint_segment):
    segment, _ = three_checkpoint_segment
    result = evaluate_retention_continuity(segment, as_of=_now(), window_days=0)
    assert result.verdict == "met"
    assert result.continuity == "unbroken"
    assert result.history_depth == 3


def test_freshly_created_chain_has_not_reached_a_six_month_window_yet(three_checkpoint_segment):
    """A brand-new log cannot possibly have 183 days of history -- this MUST
    be insufficient_evidence, never a graded failure, for a log's own youth."""
    segment, _ = three_checkpoint_segment
    result = evaluate_retention_continuity(segment, as_of=_now(), window_days=183)
    assert result.verdict == "insufficient_evidence"


def test_require_witnessed_with_no_witnessing_ts_is_not_met(three_checkpoint_segment):
    segment, _ = three_checkpoint_segment
    # stub witnesses never grade WITNESSED (matches capsule-emit's own test
    # expectation of "0/3" for the stub harness).
    assert segment.links and all(link.checkpoint.witnesses for link in segment.links)
    result = evaluate_retention_continuity(segment, as_of=_now(), window_days=0, require_witnessed=True)
    assert result.verdict == "not_met"
    assert "grade 'witnessed'" in result.detail


def test_mutant_drop_middle_checkpoint_breaks_continuity(three_checkpoint_segment):
    """The report's own mutant for this fold: one missing checkpoint in the
    window -> continuity 'broken at ...' and the verdict flips to not_met."""
    segment, checkpoints = three_checkpoint_segment
    base_result = evaluate_retention_continuity(segment, as_of=_now(), window_days=0)
    assert base_result.verdict == "met"

    tampered = ChainSegment(v=segment.v, links=(segment.links[0], segment.links[2]))
    mutant_result = evaluate_retention_continuity(tampered, as_of=_now(), window_days=0)

    assert mutant_result.verdict == "not_met"
    assert "broken at" in mutant_result.continuity
    assert f"mmr_size={checkpoints[2].mmr_size}" in mutant_result.continuity
