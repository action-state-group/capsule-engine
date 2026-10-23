# SPDX-License-Identifier: Apache-2.0
"""Cross-session outcome taxonomy (design §10): three named reducer/
presentation shapes over the existing fold engine -- NOT a second engine.
Every shape below calls ``evaluate_one`` with the SAME ``FoldDefinition``,
once per ledger-ordered slice of records, and arranges the resulting
``EvaluationTrace`` objects into the shape its subject calls for. Blurring
the three (and the fourth, per-session compliance, built elsewhere) was the
mistake design §10 calls out to avoid.

  type 2 counterparty_change -- subject: the counterparty (human). Per-
    counterparty trajectory across THEIR OWN sessions, fed by
    ``scan(counterparty=...)``. Gated: no trend below ``min_n`` engagements,
    and every trajectory carries a correlation-not-cause caveat (design §10:
    "it never claims the agent caused it").
  type 3 agent_trajectory -- subject: the agent, one population. A single
    trend line over fixed-width period buckets (``folds/duration.py``
    strings, e.g. "7d") spanning the declared range.
  type 4 cohort_comparison -- subject: two populations. The SAME fold
    computed once per partition -- two results, never blended into one
    number (design §10: "two coverage lines, never blended").

The one discipline every shape here observes: the *records* passed in change
between shapes, never the fold definition's own digest (design §10.2, "range
is a flag on the run") -- so a fold's identity stays exactly what
``folds/definition.py`` already computes it to be.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from .definition import FoldDefinition
from .duration import parse_duration_seconds
from .engine import EvaluationTrace, evaluate_one
from .paths import ABSENT, get_path

CORRELATION_NOT_CAUSE_CAVEAT = (
    "shows the counterparty's outcome across sessions where the agent was present; "
    "it does not claim the agent caused the change (design §10, same discipline as "
    "the refused agent.caused_resolution outcome)"
)


def _parse_timestamp(ts: str) -> datetime:
    text = ts[:-1] + "+00:00" if ts.endswith("Z") else ts
    return datetime.fromisoformat(text)


def _bucket_by_path(records: list[dict], path: str) -> dict[Any, list[dict]]:
    """Partition records into FIRST-APPEARANCE-ordered buckets keyed by a
    dotted path. Never re-sorted -- ledger order in, ledger order out (spec
    §3 rule 3), for every shape built on this module."""
    buckets: dict[Any, list[dict]] = {}
    for record in records:
        key = get_path(record, path)
        if key is ABSENT:
            continue
        buckets.setdefault(key, []).append(record)
    return buckets


# ---------------------------------------------------------------------------
# type 2: counterparty-change
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SessionPoint:
    """One session's fold result within a counterparty's trajectory."""

    session_id: Any
    trace: EvaluationTrace


@dataclass(frozen=True)
class CounterpartyTrajectory:
    """Type 2 (design §10 row 2): a single counterparty's own sessions,
    reduced in ledger order. ``gated`` is True when ``engagement_count <
    min_n`` -- in that case ``points`` is empty and no trend is stated,
    matching the min-N discipline (design §10: "no trend stated below N
    engagements")."""

    counterparty: Any
    engagement_count: int
    min_n: int
    gated: bool
    points: tuple[SessionPoint, ...]
    caveat: str = CORRELATION_NOT_CAUSE_CAVEAT


def counterparty_change(
    definition: FoldDefinition,
    records: list[dict],
    *,
    counterparty: Any,
    session_path: str,
    min_n: int,
    as_of: str | None = None,
) -> CounterpartyTrajectory:
    """Reduce ``definition`` once per session within ``records``, which the
    caller has already scoped to one counterparty (design §10:
    "scan(counterparty=...) gives type 2's per-counterparty slice"). Sessions
    are ordered by first appearance in ``records`` (ledger order)."""
    sessions = _bucket_by_path(records, session_path)
    engagement_count = len(sessions)
    if engagement_count < min_n:
        return CounterpartyTrajectory(
            counterparty=counterparty,
            engagement_count=engagement_count,
            min_n=min_n,
            gated=True,
            points=(),
        )

    points = tuple(
        SessionPoint(session_id=session_id, trace=evaluate_one(definition, session_records, as_of=as_of))
        for session_id, session_records in sessions.items()
    )
    return CounterpartyTrajectory(
        counterparty=counterparty,
        engagement_count=engagement_count,
        min_n=min_n,
        gated=False,
        points=points,
    )


# ---------------------------------------------------------------------------
# type 3: agent-trajectory
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TrendPoint:
    """One period bucket's fold result within a single-population trend."""

    period_start: str
    period_end: str
    trace: EvaluationTrace


@dataclass(frozen=True)
class AgentTrajectory:
    """Type 3 (design §10 row 3): ONE population (the agent), several
    successive period buckets -- never partitioned by counterparty or
    cohort. A trend line, not a trajectory-per-subject."""

    points: tuple[TrendPoint, ...]


def agent_trajectory(
    definition: FoldDefinition,
    records: list[dict],
    *,
    range_start: str,
    range_end: str,
    period: str,
    timestamp_path: str = "timestamp",
    as_of: str | None = None,
) -> AgentTrajectory:
    """Reduce ``definition`` once per fixed-width ``period`` bucket (a
    ``folds/duration.py`` string, e.g. "7d") spanning [range_start,
    range_end) -- the SAME fold digest every bucket (design §10.2: "range is
    a flag on the run"), only the records slice changes. Buckets come from
    the declared range, never a wall clock (spec §3 rule 1)."""
    start = _parse_timestamp(range_start)
    end = _parse_timestamp(range_end)
    step = timedelta(seconds=parse_duration_seconds(period))

    points: list[TrendPoint] = []
    cursor = start
    while cursor < end:
        bucket_end = min(cursor + step, end)
        bucket_records = []
        for record in records:
            ts = get_path(record, timestamp_path)
            if ts is ABSENT or not isinstance(ts, str):
                continue
            if cursor <= _parse_timestamp(ts) < bucket_end:
                bucket_records.append(record)
        points.append(
            TrendPoint(
                period_start=cursor.isoformat(),
                period_end=bucket_end.isoformat(),
                trace=evaluate_one(definition, bucket_records, as_of=as_of),
            )
        )
        cursor = bucket_end

    return AgentTrajectory(points=tuple(points))


# ---------------------------------------------------------------------------
# type 4: cohort-comparison
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CohortComparison:
    """Type 4 (design §10 row 4): the SAME fold computed once per partition.
    Deliberately TWO separate results -- there is no combined/blended field
    here, and there must never be one (design §10: "two coverage lines,
    never blended")."""

    label_a: str
    label_b: str
    trace_a: EvaluationTrace
    trace_b: EvaluationTrace


def cohort_comparison(
    definition: FoldDefinition,
    *,
    label_a: str,
    records_a: list[dict],
    label_b: str,
    records_b: list[dict],
    as_of: str | None = None,
) -> CohortComparison:
    """Evaluate ``definition`` once per partition, unblended. The caller
    supplies each cohort's own record slice (e.g. two ``scan()`` calls, or a
    version/model partition upstream) -- this function's only job is to keep
    the two results next to each other, never summed or averaged together."""
    return CohortComparison(
        label_a=label_a,
        label_b=label_b,
        trace_a=evaluate_one(definition, records_a, as_of=as_of),
        trace_b=evaluate_one(definition, records_b, as_of=as_of),
    )
