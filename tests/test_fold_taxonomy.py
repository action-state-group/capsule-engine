# SPDX-License-Identifier: Apache-2.0
"""Cross-session outcome taxonomy (design §10): counterparty-change,
agent-trajectory, cohort-comparison -- three reducer/presentation shapes over
the existing fold engine, not a second engine."""

from __future__ import annotations

from capsule_engine.folds.definition import parse_definition
from capsule_engine.folds.taxonomy import (
    CORRELATION_NOT_CAUSE_CAVEAT,
    agent_trajectory,
    cohort_comparison,
    counterparty_change,
)

_HELD_COUNT_DEF = parse_definition(
    {
        "fold_id": "test.held_count/1.0.0",
        "reads": [{"path": "verdict", "erasure_class": "commitment-ok"}],
        "filter": [{"field": "verdict", "op": "eq", "value": "held"}],
        "reduce": {"reducer": "count"},
        "emit": "count",
    }
)


# ---------------------------------------------------------------------------
# type 2: counterparty_change
# ---------------------------------------------------------------------------


def test_counterparty_change_below_min_n_is_gated_with_no_points():
    records = [
        {"session": "s1", "verdict": "held"},
        {"session": "s2", "verdict": "held"},
    ]
    result = counterparty_change(
        _HELD_COUNT_DEF, records, counterparty="acme", session_path="session", min_n=3
    )
    assert result.gated is True
    assert result.points == ()
    assert result.engagement_count == 2
    assert result.min_n == 3


def test_counterparty_change_at_or_above_min_n_yields_one_point_per_session_in_order():
    records = [
        {"session": "s1", "verdict": "held"},
        {"session": "s1", "verdict": "failed"},
        {"session": "s2", "verdict": "held"},
        {"session": "s3", "verdict": "held"},
    ]
    result = counterparty_change(
        _HELD_COUNT_DEF, records, counterparty="acme", session_path="session", min_n=3
    )
    assert result.gated is False
    assert [p.session_id for p in result.points] == ["s1", "s2", "s3"]
    assert [p.trace.result for p in result.points] == [1, 1, 1]


def test_counterparty_change_carries_correlation_not_cause_caveat():
    records = [
        {"session": "s1", "verdict": "held"},
        {"session": "s2", "verdict": "held"},
    ]
    result = counterparty_change(
        _HELD_COUNT_DEF, records, counterparty="acme", session_path="session", min_n=1
    )
    assert result.caveat == CORRELATION_NOT_CAUSE_CAVEAT


def test_counterparty_change_ignores_records_missing_the_session_path():
    records = [
        {"session": "s1", "verdict": "held"},
        {"verdict": "held"},  # no session field -- excluded from bucketing, not an error
    ]
    result = counterparty_change(
        _HELD_COUNT_DEF, records, counterparty="acme", session_path="session", min_n=1
    )
    assert result.engagement_count == 1
    assert result.points[0].trace.result == 1


# ---------------------------------------------------------------------------
# type 3: agent_trajectory
# ---------------------------------------------------------------------------


def test_agent_trajectory_buckets_into_fixed_width_periods_over_the_range():
    records = [
        {"verdict": "held", "timestamp": "2026-01-01T00:00:00Z"},  # bucket 0
        {"verdict": "held", "timestamp": "2026-01-03T00:00:00Z"},  # bucket 0
        {"verdict": "held", "timestamp": "2026-01-08T00:00:00Z"},  # bucket 1
    ]
    trajectory = agent_trajectory(
        _HELD_COUNT_DEF,
        records,
        range_start="2026-01-01T00:00:00Z",
        range_end="2026-01-15T00:00:00Z",
        period="7d",
    )
    assert len(trajectory.points) == 2
    assert trajectory.points[0].trace.result == 2
    assert trajectory.points[1].trace.result == 1


def test_agent_trajectory_empty_bucket_has_a_defined_zero_result_not_an_error():
    records = [{"verdict": "held", "timestamp": "2026-01-01T00:00:00Z"}]
    trajectory = agent_trajectory(
        _HELD_COUNT_DEF,
        records,
        range_start="2026-01-01T00:00:00Z",
        range_end="2026-01-15T00:00:00Z",
        period="7d",
    )
    assert trajectory.points[0].trace.result == 1
    assert trajectory.points[1].trace.result == 0


def test_agent_trajectory_last_bucket_is_clipped_to_range_end():
    records = []
    trajectory = agent_trajectory(
        _HELD_COUNT_DEF,
        records,
        range_start="2026-01-01T00:00:00Z",
        range_end="2026-01-10T00:00:00Z",
        period="7d",
    )
    assert len(trajectory.points) == 2
    assert trajectory.points[1].period_end == "2026-01-10T00:00:00+00:00"


# ---------------------------------------------------------------------------
# type 4: cohort_comparison
# ---------------------------------------------------------------------------


def test_cohort_comparison_evaluates_each_partition_independently_never_blended():
    records_a = [{"verdict": "held"}, {"verdict": "held"}, {"verdict": "failed"}]
    records_b = [{"verdict": "held"}]
    result = cohort_comparison(
        _HELD_COUNT_DEF,
        label_a="v2",
        records_a=records_a,
        label_b="v1",
        records_b=records_b,
    )
    assert result.label_a == "v2"
    assert result.trace_a.result == 2
    assert result.label_b == "v1"
    assert result.trace_b.result == 1
    # no combined/summed field exists on the result at all
    assert not hasattr(result, "total")
    assert not hasattr(result, "combined")
