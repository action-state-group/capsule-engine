# SPDX-License-Identifier: Apache-2.0
"""Replay engine behavior: skip-with-count, defaults, unknown-field tolerance,
filter ops, windows, and key-less accumulation (spec §3)."""
from __future__ import annotations

from capsule_ledger.folds.definition import parse_definition
from capsule_ledger.folds.engine import evaluate_all, evaluate_one


def test_absent_declared_field_without_default_is_skipped_not_errored():
    definition = parse_definition(
        {
            "fold_id": "test.skip/1.0.0",
            "reads": [{"path": "developer", "erasure_class": "commitment-ok"}],
            "key": "developer",
            "reduce": {"reducer": "count"},
            "emit": "count",
        }
    )
    records = [{"developer": "agent-a"}, {"other_field": "no developer here"}, {"developer": "agent-a"}]
    trace = evaluate_one(definition, records, key_value="agent-a")
    assert trace.result == 2
    assert trace.skipped_count == 1
    assert trace.considered_count == 3


def test_absent_declared_field_uses_declared_default():
    definition = parse_definition(
        {
            "fold_id": "test.default/1.0.0",
            "reads": [
                {"path": "developer", "erasure_class": "commitment-ok"},
                {"path": "priority", "erasure_class": "commitment-ok", "default": "normal"},
            ],
            "key": "developer",
            "filter": [{"field": "priority", "op": "eq", "value": "normal"}],
            "reduce": {"reducer": "count"},
            "emit": "count",
        }
    )
    records = [{"developer": "agent-a"}, {"developer": "agent-a", "priority": "high"}]
    trace = evaluate_one(definition, records, key_value="agent-a")
    assert trace.result == 1  # only the record that defaults to "normal" matches
    assert trace.skipped_count == 0


def test_undeclared_fields_present_on_a_record_are_ignored():
    definition = parse_definition(
        {
            "fold_id": "test.unknown/1.0.0",
            "reads": [{"path": "developer", "erasure_class": "commitment-ok"}],
            "key": "developer",
            "reduce": {"reducer": "count"},
            "emit": "count",
        }
    )
    records = [{"developer": "agent-a", "future_field": {"nested": True}, "trace_id": "xyz"}]
    trace = evaluate_one(definition, records, key_value="agent-a")
    assert trace.result == 1
    assert trace.skipped_count == 0


def test_filter_ops():
    definition = parse_definition(
        {
            "fold_id": "test.filterops/1.0.0",
            "reads": [
                {"path": "developer", "erasure_class": "commitment-ok"},
                {"path": "status", "erasure_class": "commitment-ok"},
                {"path": "amount_minor", "erasure_class": "preimage"},
            ],
            "filter": [
                {"field": "status", "op": "in", "value": ["executed", "confirmed"]},
                {"field": "amount_minor", "op": "gte", "value": 100},
                {"field": "developer", "op": "prefix", "value": "agent-"},
            ],
            "key": "developer",
            "reduce": {"reducer": "count"},
            "emit": "count",
        }
    )
    records = [
        {"developer": "agent-a", "status": "executed", "amount_minor": 500},  # matches
        {"developer": "agent-a", "status": "blocked", "amount_minor": 500},  # status excluded
        {"developer": "agent-a", "status": "executed", "amount_minor": 10},  # amount excluded
        {"developer": "other-b", "status": "executed", "amount_minor": 500},  # prefix excluded
    ]
    trace = evaluate_one(definition, records, key_value="agent-a")
    assert trace.result == 1


def test_explicit_window_bounds_by_timestamp():
    definition = parse_definition(
        {
            "fold_id": "test.explicit_window/1.0.0",
            "reads": [
                {"path": "developer", "erasure_class": "commitment-ok"},
                {"path": "timestamp", "erasure_class": "commitment-ok"},
            ],
            "window": {"mode": "explicit", "start": "2026-01-01T00:00:00Z", "end": "2026-01-31T23:59:59Z"},
            "key": "developer",
            "reduce": {"reducer": "count"},
            "emit": "count",
        }
    )
    records = [
        {"developer": "agent-a", "timestamp": "2025-12-31T23:59:59Z"},  # before window
        {"developer": "agent-a", "timestamp": "2026-01-15T00:00:00Z"},  # in window
        {"developer": "agent-a", "timestamp": "2026-02-01T00:00:00Z"},  # after window
    ]
    trace = evaluate_one(definition, records, key_value="agent-a")
    assert trace.result == 1


def test_rolling_window_uses_explicit_as_of_not_wall_clock():
    definition = parse_definition(
        {
            "fold_id": "test.rolling_window/1.0.0",
            "reads": [
                {"path": "developer", "erasure_class": "commitment-ok"},
                {"path": "timestamp", "erasure_class": "commitment-ok"},
            ],
            "window": {"mode": "rolling", "duration": "7d"},
            "key": "developer",
            "reduce": {"reducer": "count"},
            "emit": "count",
        }
    )
    records = [
        {"developer": "agent-a", "timestamp": "2026-01-01T00:00:00Z"},  # 10 days before anchor
        {"developer": "agent-a", "timestamp": "2026-01-08T00:00:00Z"},  # 3 days before anchor
        {"developer": "agent-a", "timestamp": "2026-01-11T00:00:00Z"},  # the anchor itself
    ]
    trace = evaluate_one(definition, records, key_value="agent-a", as_of="2026-01-11T00:00:00Z")
    assert trace.result == 2


def test_key_less_definition_uses_a_single_global_accumulator():
    definition = parse_definition(
        {
            "fold_id": "test.global/1.0.0",
            "reads": [{"path": "developer", "erasure_class": "commitment-ok"}],
            "reduce": {"reducer": "count"},
            "emit": "count",
        }
    )
    records = [{"developer": "agent-a"}, {"developer": "agent-b"}, {"developer": "agent-a"}]
    trace = evaluate_one(definition, records)
    assert trace.result == 3


def test_evaluate_one_unseen_key_returns_defined_empty_result_not_an_error():
    definition = parse_definition(
        {
            "fold_id": "test.unseen/1.0.0",
            "reads": [{"path": "developer", "erasure_class": "commitment-ok"}],
            "key": "developer",
            "reduce": {"reducer": "count"},
            "emit": "count",
        }
    )
    trace = evaluate_one(definition, [{"developer": "agent-a"}], key_value="agent-never-appeared")
    assert trace.result == 0
    assert trace.matched_count == 0


def test_evaluate_all_returns_one_trace_per_group():
    definition = parse_definition(
        {
            "fold_id": "test.allgroups/1.0.0",
            "reads": [{"path": "developer", "erasure_class": "commitment-ok"}],
            "key": "developer",
            "reduce": {"reducer": "count"},
            "emit": "count",
        }
    )
    records = [{"developer": "agent-a"}, {"developer": "agent-b"}, {"developer": "agent-a"}]
    traces = evaluate_all(definition, records)
    assert {k: t.result for k, t in traces.items()} == {"agent-a": 2, "agent-b": 1}


def test_envelope_shape_matches_spec_section_4():
    definition = parse_definition(
        {
            "fold_id": "test.envelope/1.0.0",
            "reads": [{"path": "developer", "erasure_class": "commitment-ok"}],
            "key": "developer",
            "reduce": {"reducer": "count"},
            "emit": "count",
        }
    )
    trace = evaluate_one(definition, [{"developer": "agent-a"}], key_value="agent-a")
    envelope = trace.to_envelope()
    assert set(envelope.keys()) == {"fold", "range", "tree_size", "checkpoint", "result", "evaluated_at", "staleness"}
    assert envelope["fold"] == definition.definition_digest()
    assert envelope["range"] == [0, 0]
    assert envelope["tree_size"] == 1
    assert envelope["staleness"] == {"checkpoint_age_ms": 0}
