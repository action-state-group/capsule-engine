# SPDX-License-Identifier: Apache-2.0
"""Set-membership fold — GRC folds batch item 5 (EU-26-2): "the approver was
a member of the deployer's authorised roster." Per-record membership check
against a caller-supplied roster — the roster itself is a declared pack
parameter (like the ordering fold's ``before``/``after`` classifiers), never
mined from the records under test (the party under test must not be the one defining what counts as compliant).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .paths import get_path

__all__ = ["MembershipRecordResult", "SetMembershipResult", "evaluate_set_membership"]


@dataclass(frozen=True)
class MembershipRecordResult:
    index: int
    value: Any
    verdict: str  # "met" | "not_met"


@dataclass(frozen=True)
class SetMembershipResult:
    records: tuple[MembershipRecordResult, ...]
    considered_count: int
    skipped_count: int

    @property
    def met_count(self) -> int:
        return sum(1 for r in self.records if r.verdict == "met")

    @property
    def not_met_count(self) -> int:
        return sum(1 for r in self.records if r.verdict == "not_met")


def evaluate_set_membership(
    records: list[dict],
    *,
    field: str,
    roster: frozenset[Any],
) -> SetMembershipResult:
    """A record missing ``field`` is skipped (counted), never an error --
    the same skip-with-count discipline the fold engine uses for a declared
    read field with no default."""
    results: list[MembershipRecordResult] = []
    skipped = 0
    for idx, record in enumerate(records):
        value = get_path(record, field, None)
        if value is None:
            skipped += 1
            continue
        verdict = "met" if value in roster else "not_met"
        results.append(MembershipRecordResult(index=idx, value=value, verdict=verdict))

    return SetMembershipResult(records=tuple(results), considered_count=len(records), skipped_count=skipped)
