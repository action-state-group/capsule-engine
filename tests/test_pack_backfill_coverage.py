# SPDX-License-Identifier: Apache-2.0
"""[batch3-backfill-builder-provenance-mode]. Every negative case flips
exactly one thing and confirms the mutant is caught, per QUEUE_PROTOCOL §7."""
from __future__ import annotations

from capsule_engine.packs.backfill_coverage import (
    STATUS_INSUFFICIENT,
    STATUS_NOT_FOUND,
    STATUS_SATISFIED,
    evaluate_requirement_coverage,
)

REQ = "req-1"


def _matches(record):
    return record.get("payload", {}).get("requirement_id") == REQ


def _contemporaneous(capsule_id, requirement_id=REQ):
    return {"capsule_id": capsule_id, "payload": {"requirement_id": requirement_id}}


def _backfilled(capsule_id, requirement_id=REQ, corroborated=False):
    record = {
        "capsule_id": capsule_id,
        "payload": {"requirement_id": requirement_id},
        "provenance_mode": {
            "mode": "backfilled",
            "source_ref": {"type": "source_record", "digest_alg": "sha256", "digest": "a" * 64},
            "source_asserted_at": "2026-01-01T00:00:00Z",
            "import_batch": "batch-1",
            "imported_at": "2026-09-22T00:00:00Z",
        },
    }
    if corroborated:
        record["references"] = [
            {
                "citation_purpose": "corroborates_source_time",
                "type": "witness_receipt",
                "digest_alg": "sha256",
                "digest": "b" * 64,
            }
        ]
    return record


def _duplicates(capsule_id, parent_id, requirement_id=REQ):
    record = _backfilled(capsule_id, requirement_id=requirement_id)
    record["chain"] = {"relation": "duplicates", "parent_capsule_id": parent_id}
    return record


def test_no_matching_record_is_not_found():
    result = evaluate_requirement_coverage([], matches=_matches)
    assert result.status == STATUS_NOT_FOUND
    assert result.matched_capsule_ids == ()


def test_contemporaneous_evidence_satisfies_with_no_assurance_floor():
    records = [_contemporaneous("c1")]
    result = evaluate_requirement_coverage(records, matches=_matches)
    assert result.status == STATUS_SATISFIED
    assert result.contemporaneous_count == 1
    assert result.backfilled_count == 0


def test_backfilled_evidence_satisfies_with_no_assurance_floor():
    records = [_backfilled("b1")]
    result = evaluate_requirement_coverage(records, matches=_matches)
    assert result.status == STATUS_SATISFIED
    assert result.backfilled_count == 1


def test_committed_requirement_is_insufficient_with_only_backfilled_evidence():
    records = [_backfilled("b1")]
    result = evaluate_requirement_coverage(
        records, matches=_matches, minimum_assurance=frozenset({"committed"})
    )
    assert result.status == STATUS_INSUFFICIENT
    assert "backfilled" in result.detail
    assert "self_attested" in result.detail


def test_committed_requirement_is_satisfied_by_contemporaneous_evidence():
    records = [_backfilled("b1"), _contemporaneous("c1")]
    result = evaluate_requirement_coverage(
        records, matches=_matches, minimum_assurance=frozenset({"committed"})
    )
    assert result.status == STATUS_SATISFIED


def test_committed_requirement_is_satisfied_by_a_corroborated_backfilled_record():
    records = [_backfilled("b1", corroborated=True)]
    result = evaluate_requirement_coverage(
        records, matches=_matches, minimum_assurance=frozenset({"committed"})
    )
    assert result.status == STATUS_SATISFIED


def test_mutant_removing_the_corroborating_reference_flips_satisfied_to_insufficient():
    """The report's own mutant: a corroborated backfilled record clears the
    Committed bar; strip its references[] entry and the same record must
    fail it."""
    base = [_backfilled("b1", corroborated=True)]
    mutant = [_backfilled("b1", corroborated=False)]

    base_result = evaluate_requirement_coverage(base, matches=_matches, minimum_assurance=frozenset({"committed"}))
    mutant_result = evaluate_requirement_coverage(mutant, matches=_matches, minimum_assurance=frozenset({"committed"}))

    assert base_result.status == STATUS_SATISFIED
    assert mutant_result.status == STATUS_INSUFFICIENT


def test_duplicate_pair_counts_once_under_the_contemporaneous_status():
    records = [_contemporaneous("c1"), _duplicates("b1", parent_id="c1")]
    result = evaluate_requirement_coverage(records, matches=_matches)
    assert result.status == STATUS_SATISFIED
    assert result.matched_count == 2  # both are candidates before collapse
    assert result.duplicates_collapsed_count == 1
    assert result.contemporaneous_count == 1
    assert result.backfilled_count == 0  # the duplicate child was collapsed, not counted
    assert result.matched_capsule_ids == ("c1",)


def test_duplicate_pair_under_committed_is_satisfied_via_the_contemporaneous_parent():
    """A backfilled duplicate of a contemporaneous record must not itself be
    evaluated against the Committed cap -- the parent governs (AAC -05
    §provenancemode, 'Duplicates')."""
    records = [_contemporaneous("c1"), _duplicates("b1", parent_id="c1")]
    result = evaluate_requirement_coverage(records, matches=_matches, minimum_assurance=frozenset({"committed"}))
    assert result.status == STATUS_SATISFIED


def test_mutant_dropping_the_duplicates_chain_double_counts():
    """The report's own mutant: with chain.relation removed, the second
    record is no longer collapsed and both are counted independently."""
    base = [_contemporaneous("c1"), _duplicates("b1", parent_id="c1")]
    mutant = [_contemporaneous("c1"), _backfilled("b1")]  # no chain block

    base_result = evaluate_requirement_coverage(base, matches=_matches)
    mutant_result = evaluate_requirement_coverage(mutant, matches=_matches)

    assert base_result.duplicates_collapsed_count == 1
    assert len(base_result.matched_capsule_ids) == 1
    assert mutant_result.duplicates_collapsed_count == 0
    assert len(mutant_result.matched_capsule_ids) == 2


def test_duplicate_parent_absent_from_stream_is_not_collapsed():
    """Cannot collapse against evidence not shown -- a duplicates child whose
    parent capsule_id is not present in ``records`` is kept as its own
    survivor rather than silently dropped."""
    records = [_duplicates("b1", parent_id="unseen-parent")]
    result = evaluate_requirement_coverage(records, matches=_matches)
    assert result.duplicates_collapsed_count == 0
    assert result.matched_capsule_ids == ("b1",)


def test_unrelated_records_are_ignored_by_the_matches_predicate():
    records = [_contemporaneous("c1", requirement_id="other-req"), _backfilled("b1")]
    result = evaluate_requirement_coverage(records, matches=_matches)
    assert result.matched_count == 1
    assert result.considered_count == 2
    assert result.matched_capsule_ids == ("b1",)
