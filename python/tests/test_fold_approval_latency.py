# SPDX-License-Identifier: Apache-2.0
"""GRC folds batch item 4: the approval-latency indicator fold (EU-14-4b).
The label is a fixed constant and the result type carries no verdict/ok
field at all -- these tests assert BOTH the statistics respond to data and
the label/type shape can never become a grade, even under adversarial
(rubber-stamp) data."""
from __future__ import annotations

import dataclasses

from capsule_engine.folds.approval_latency import (
    INDICATOR_LABEL,
    ApprovalLatencyResult,
    evaluate_approval_latency,
)


def _record(requested_at, approved_at):
    return {"request": {"sealed_at": requested_at}, "approval": {"sealed_at": approved_at}}


def test_computes_latency_distribution_from_paired_timestamps():
    records = [
        _record("2026-09-01T00:00:00Z", "2026-09-01T00:05:00Z"),  # 300s
        _record("2026-09-01T00:00:00Z", "2026-09-01T00:01:00Z"),  # 60s
        _record("2026-09-01T00:00:00Z", "2026-09-01T00:03:00Z"),  # 180s
    ]
    result = evaluate_approval_latency(records, request_field="request.sealed_at", approval_field="approval.sealed_at")
    assert result.count == 3
    assert result.min_seconds == 60
    assert result.max_seconds == 300
    assert result.median_seconds == 180
    assert result.label == INDICATOR_LABEL


def test_record_missing_either_timestamp_is_skipped_not_an_error():
    records = [_record("2026-09-01T00:00:00Z", "2026-09-01T00:05:00Z"), {"request": {"sealed_at": "2026-09-01T00:00:00Z"}}]
    result = evaluate_approval_latency(records, request_field="request.sealed_at", approval_field="approval.sealed_at")
    assert result.count == 1
    assert result.skipped_count == 1


def test_no_matching_records_still_renders_the_fixed_label():
    result = evaluate_approval_latency([], request_field="request.sealed_at", approval_field="approval.sealed_at")
    assert result.count == 0
    assert result.label == INDICATOR_LABEL


def test_result_type_structurally_cannot_carry_a_verdict_or_grade():
    field_names = {f.name for f in dataclasses.fields(ApprovalLatencyResult)}
    assert "verdict" not in field_names
    assert "ok" not in field_names
    assert "pass" not in field_names


def test_mutant_rubber_stamp_latencies_shift_the_distribution_but_never_the_label():
    """The report's mutant for this fold: swap realistic review latencies for
    rubber-stamp near-zero ones. The distribution MUST reflect the change
    (proving it isn't hardcoded) while the label -- the thing that keeps this
    an indicator rather than a grade -- MUST stay byte-identical."""
    base = [
        _record("2026-09-01T00:00:00Z", "2026-09-01T00:05:00Z"),
        _record("2026-09-01T00:00:00Z", "2026-09-01T00:07:00Z"),
    ]
    mutant = [
        _record("2026-09-01T00:00:00Z", "2026-09-01T00:00:00Z"),  # rubber-stamped, 0s
        _record("2026-09-01T00:00:00Z", "2026-09-01T00:00:00Z"),
    ]

    base_result = evaluate_approval_latency(base, request_field="request.sealed_at", approval_field="approval.sealed_at")
    mutant_result = evaluate_approval_latency(mutant, request_field="request.sealed_at", approval_field="approval.sealed_at")

    assert base_result.min_seconds == 300
    assert mutant_result.min_seconds == 0
    assert mutant_result.max_seconds == 0
    assert base_result.label == mutant_result.label == INDICATOR_LABEL
