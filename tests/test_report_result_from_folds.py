# SPDX-License-Identifier: Apache-2.0
"""``result_from_folds`` adapters, exercised over REAL signed
``capsule_emit`` chain data (same real-not-mocked discipline
``test_fold_retention_continuity.py`` uses), plus the committed OO fixture
these adapters produce ([batch4-result-emission-from-engine] DONE line).

The committed fixture lives at
``tests/fixtures/evidence-result/oo-claims-result.json`` -- regenerate via
``python scripts/generate_evidence_result_oo_fixture.py``. This file's own
tests only READ that committed copy (never regenerate at test time -- real
signing is nondeterministic across runs, and a committed fixture is meant to
be a stable, reviewable artifact); the live-adapter tests below independently
prove the SAME code path (real fold -> real checkpoint grade/digest ->
Claim) produces schema-valid output on fresh data every run, so a drift
between the emitter and the committed snapshot cannot hide behind a
fixture nobody re-derives.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import jsonschema
import pytest
from capsule_emit import seal, witness
from capsule_emit.chain_segment import ChainSegment, chain_segment
from capsule_emit.ledger import read_ledger_entries

from capsule_engine.folds.record_type_coverage import evaluate_record_type_coverage
from capsule_engine.folds.retention_continuity import evaluate_retention_continuity
from capsule_engine.report.result import build_result, validate_against_schema, verify_result
from capsule_engine.report.result_from_folds import (
    claim_from_retention_continuity,
    is_excluded_not_applicable,
)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "evidence-result" / "oo-claims-result.json"
CONTRACT_REF = "ec:oo-retention-continuity:2026-09-22@1"


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
    """Same real-signed-chain shape as
    ``test_fold_retention_continuity.three_checkpoint_segment``."""
    ledger_path = tmp_path / "ledger.jsonl"
    checkpoints = []
    for batch in range(3):
        for i in range(2):
            seal(None, action=f"batch{batch}-{i}", operator="OO", anchor=False, ledger=ledger_path)
        cp = witness.push(str(ledger_path))
        assert cp is not None
        checkpoints.append(cp)
    entries = list(read_ledger_entries(ledger_path))
    return chain_segment(entries, from_size=0, to_size=checkpoints[-1].mmr_size), checkpoints


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- claim_from_retention_continuity: real data, every verdict path -------


def test_met_fold_verdict_projects_to_satisfied_met_recomputed_claim(three_checkpoint_segment):
    segment, _ = three_checkpoint_segment
    result = evaluate_retention_continuity(segment, as_of=_now(), window_days=0)
    assert result.verdict == "met"

    claim = claim_from_retention_continuity(
        result, segment, claim_id="c1", contract_ref=CONTRACT_REF, requirement_ref="req-retention-immediate"
    )
    assert claim.tier == "recomputed"
    assert claim.sufficiency == "SATISFIED"
    assert claim.verdict == "met"
    assert claim.grade == "self-attested"  # stub witnesses never grade WITNESSED
    assert claim.evidence[0].digest == segment.checkpoint.digest()
    assert claim.presentation.kind == "disclosure"


def test_not_met_fold_verdict_projects_to_satisfied_not_met_claim(three_checkpoint_segment):
    segment, checkpoints = three_checkpoint_segment
    tampered = ChainSegment(v=segment.v, links=(segment.links[0], segment.links[2]))
    result = evaluate_retention_continuity(tampered, as_of=_now(), window_days=0)
    assert result.verdict == "not_met"

    claim = claim_from_retention_continuity(
        result, tampered, claim_id="c2", contract_ref=CONTRACT_REF, requirement_ref="req-retention-continuity-unbroken"
    )
    assert claim.sufficiency == "SATISFIED"
    assert claim.verdict == "not_met"


def test_insufficient_evidence_fold_verdict_projects_to_insufficient_not_evaluable_claim(three_checkpoint_segment):
    segment, _ = three_checkpoint_segment
    result = evaluate_retention_continuity(segment, as_of=_now(), window_days=183)
    assert result.verdict == "insufficient_evidence"

    claim = claim_from_retention_continuity(
        result, segment, claim_id="c3", contract_ref=CONTRACT_REF, requirement_ref="req-retention-6mo"
    )
    assert claim.sufficiency == "INSUFFICIENT"
    assert claim.verdict == "not_evaluable"


def test_mutant_dropping_the_middle_checkpoint_flips_the_projected_claim_from_met_to_not_met(three_checkpoint_segment):
    """The report's own mutant, one level up: the SAME tamper
    ``test_fold_retention_continuity``'s mutant test uses must flip the
    PROJECTED claim's verdict too, not just the fold's own result -- proves
    the adapter doesn't silently drop or reinterpret the fold's verdict."""
    segment, _ = three_checkpoint_segment
    base = evaluate_retention_continuity(segment, as_of=_now(), window_days=0)
    base_claim = claim_from_retention_continuity(
        base, segment, claim_id="c", contract_ref=CONTRACT_REF, requirement_ref="req"
    )
    assert base_claim.verdict == "met"

    tampered = ChainSegment(v=segment.v, links=(segment.links[0], segment.links[2]))
    mutant = evaluate_retention_continuity(tampered, as_of=_now(), window_days=0)
    mutant_claim = claim_from_retention_continuity(
        mutant, tampered, claim_id="c", contract_ref=CONTRACT_REF, requirement_ref="req"
    )
    assert mutant_claim.verdict == "not_met"


def test_is_excluded_not_applicable_true_for_empty_registered_kinds():
    result = evaluate_record_type_coverage([], registered_kinds=frozenset())
    assert is_excluded_not_applicable(result)


def test_mutant_is_excluded_not_applicable_false_once_kinds_are_registered():
    result = evaluate_record_type_coverage([], registered_kinds=frozenset({"action.executed"}))
    assert not is_excluded_not_applicable(result)


# --- live end-to-end build, fresh data every run ---------------------------


def test_live_build_produces_a_schema_valid_three_bucket_result(three_checkpoint_segment):
    segment, _ = three_checkpoint_segment
    as_of = _now()

    met_claim = claim_from_retention_continuity(
        evaluate_retention_continuity(segment, as_of=as_of, window_days=0),
        segment,
        claim_id="claim-1",
        contract_ref=CONTRACT_REF,
        requirement_ref="req-retention-immediate",
    )
    tampered = ChainSegment(v=segment.v, links=(segment.links[0], segment.links[2]))
    not_met_claim = claim_from_retention_continuity(
        evaluate_retention_continuity(tampered, as_of=as_of, window_days=0),
        tampered,
        claim_id="claim-2",
        contract_ref=CONTRACT_REF,
        requirement_ref="req-retention-continuity-unbroken",
    )
    not_evaluable_claim = claim_from_retention_continuity(
        evaluate_retention_continuity(segment, as_of=as_of, window_days=183),
        segment,
        claim_id="claim-3",
        contract_ref=CONTRACT_REF,
        requirement_ref="req-retention-6mo",
    )
    excluded = evaluate_record_type_coverage([], registered_kinds=frozenset())
    assert is_excluded_not_applicable(excluded)

    result = build_result(
        [met_claim, not_met_claim, not_evaluable_claim],
        generated_at=as_of,
        excluded_not_applicable=1,
    )
    doc = result.to_dict()

    assert doc["aggregate"]["coverage"] == {
        "evaluated_population": 3,
        "excluded_not_applicable": 1,
        "unknown_count": 0,
    }
    assert doc["aggregate"]["buckets"] == {
        "met": ["claim-1"],
        "not_met": ["claim-2"],
        "not_evaluable": ["claim-3"],
    }
    validate_against_schema(doc)
    verify_result(doc)


# --- the committed OO fixture ----------------------------------------------


def test_committed_oo_fixture_exists_and_is_schema_valid():
    doc = json.loads(FIXTURE_PATH.read_text())
    validate_against_schema(doc)
    verify_result(doc)


def test_committed_oo_fixture_coverage_matches_hand_count():
    """The hand count this task's DONE line requires: 3 requirements
    evaluated (one per retention-continuity call), 1 excluded as
    NOT_APPLICABLE (the empty-registered_kinds record-type-coverage call),
    0 UNKNOWN, one claim in each of the three verdict buckets."""
    doc = json.loads(FIXTURE_PATH.read_text())
    assert doc["aggregate"]["coverage"] == {
        "evaluated_population": 3,
        "excluded_not_applicable": 1,
        "unknown_count": 0,
    }
    assert len(doc["aggregate"]["buckets"]["met"]) == 1
    assert len(doc["aggregate"]["buckets"]["not_met"]) == 1
    assert len(doc["aggregate"]["buckets"]["not_evaluable"]) == 1
    assert doc["view"]["producer_name"] == "OO"


def test_mutant_committed_oo_fixture_rejects_a_stripped_aggregate():
    """Proves the schema-validity test above is actually looking at the
    fixture's real content, not vacuously passing."""
    doc = json.loads(FIXTURE_PATH.read_text())
    del doc["aggregate"]["coverage"]
    with pytest.raises(jsonschema.exceptions.ValidationError):
        validate_against_schema(doc)
