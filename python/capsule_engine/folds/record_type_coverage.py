# SPDX-License-Identifier: Apache-2.0
"""Record-type coverage fold — GRC folds batch item 3 (EU-12-2): which
registered event kinds the desk expects actually appear in a range. Backward
replay only (a presence check over what already happened, never a forward
wicket) — pairs with the log-schema registration itself (a DOC presence
check, not this fold's job).
"""
from __future__ import annotations

from dataclasses import dataclass

from .paths import get_path

__all__ = ["RecordTypeCoverageResult", "evaluate_record_type_coverage"]


@dataclass(frozen=True)
class RecordTypeCoverageResult:
    present: frozenset[str]
    missing: frozenset[str]
    considered_count: int
    # False when nothing was declared: the requirement is excluded from the
    # evaluated population, and ``verdict`` is None rather than any verdict.
    applicable: bool
    verdict: str | None  # "met" | "not_met"; None when not applicable


def evaluate_record_type_coverage(
    records: list[dict],
    *,
    registered_kinds: frozenset[str],
    kind_field: str = "kind",
) -> RecordTypeCoverageResult:
    """``registered_kinds`` is the desk's own declared expectation (from the
    log-schema registry entry) -- this fold never invents which kinds
    "should" appear, it only reports which of the DECLARED kinds actually do.

    An empty ``registered_kinds`` (nothing was declared to check coverage
    against) is a population exclusion: ``applicable=False`` with no verdict
    at all, never a vacuous ``met``.
    """
    if not registered_kinds:
        return RecordTypeCoverageResult(
            present=frozenset(), missing=frozenset(), considered_count=len(records), applicable=False, verdict=None
        )

    seen: set[str] = set()
    for record in records:
        kind = get_path(record, kind_field, None)
        if kind is not None:
            seen.add(kind)

    present = frozenset(seen) & registered_kinds
    missing = registered_kinds - present
    verdict = "met" if not missing else "not_met"
    return RecordTypeCoverageResult(
        present=present, missing=missing, considered_count=len(records), applicable=True, verdict=verdict
    )
