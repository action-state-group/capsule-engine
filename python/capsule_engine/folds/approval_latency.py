# SPDX-License-Identifier: Apache-2.0
"""Approval-latency distribution fold — GRC folds batch item 4 (EU-14-4b):
"overseers were made aware of automation bias; approvals were not rubber
stamps." Review time alone cannot prove attention was paid — a long latency
can still be an unattended idle tab, and a short one can be a genuinely fast,
attentive reviewer. This fold therefore emits an INDICATOR, structurally
incapable of carrying a pass/fail grade: :class:`ApprovalLatencyResult` has
no ``verdict``/``ok`` field at all, and ``label`` is a fixed constant, never
derived from the data. A caller that wants a judged row on "clear and
meaningful" review still needs the separate JUDGED path (batch spec: "RULE,
never 'proves attention'").
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .paths import get_path

__all__ = ["ApprovalLatencyResult", "evaluate_approval_latency", "INDICATOR_LABEL"]

INDICATOR_LABEL = "indicator, does not prove attention"


def _parse_timestamp(ts: str) -> datetime:
    text = ts[:-1] + "+00:00" if ts.endswith("Z") else ts
    return datetime.fromisoformat(text)


@dataclass(frozen=True)
class ApprovalLatencyResult:
    label: str
    latencies_seconds: tuple[int, ...]
    count: int
    min_seconds: int | None
    max_seconds: int | None
    median_seconds: int | None
    skipped_count: int


def evaluate_approval_latency(
    records: list[dict],
    *,
    request_field: str,
    approval_field: str,
) -> ApprovalLatencyResult:
    """One latency per record with BOTH ``request_field`` and
    ``approval_field`` present (ISO-8601 timestamp strings); a record missing
    either is skipped (counted), never an error -- the same skip-with-count
    discipline the fold engine uses for a declared field with no default.
    """
    latencies: list[int] = []
    skipped = 0
    for record in records:
        requested_at = get_path(record, request_field, None)
        approved_at = get_path(record, approval_field, None)
        if not isinstance(requested_at, str) or not isinstance(approved_at, str):
            skipped += 1
            continue
        delta = _parse_timestamp(approved_at) - _parse_timestamp(requested_at)
        latencies.append(int(delta.total_seconds()))

    if not latencies:
        return ApprovalLatencyResult(
            label=INDICATOR_LABEL,
            latencies_seconds=(),
            count=0,
            min_seconds=None,
            max_seconds=None,
            median_seconds=None,
            skipped_count=skipped,
        )

    ordered = sorted(latencies)
    mid = len(ordered) // 2
    median = ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) // 2

    return ApprovalLatencyResult(
        label=INDICATOR_LABEL,
        latencies_seconds=tuple(latencies),
        count=len(latencies),
        min_seconds=ordered[0],
        max_seconds=ordered[-1],
        median_seconds=median,
        skipped_count=skipped,
    )
