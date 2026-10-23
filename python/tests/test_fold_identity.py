# SPDX-License-Identifier: Apache-2.0
"""Design §10.1: fold identity is ``(definition_digest, range, input_set_digest)``
-- run the same fold over the same range against the same ledger contents
twice and get the same triple back, which is what makes a re-run provably
idempotent rather than merely assumed so. The fold also cites, by digest,
exactly which records it reduced (§10.1's "the fold capsule points at its
inputs, so '23 of 73' expands to the exact 73 sessions and 23 verdicts")."""
from __future__ import annotations

from capsule_engine.folds.definition import parse_definition
from capsule_engine.folds.engine import evaluate_all, evaluate_one

DEFINITION = {
    "fold_id": "test.identity/1.0.0",
    "reads": [{"path": "developer", "erasure_class": "commitment-ok"}],
    "key": "developer",
    "reduce": {"reducer": "count"},
    "emit": "count",
}


def test_same_fold_same_range_same_records_is_idempotent():
    definition = parse_definition(DEFINITION)
    records = [
        {"capsule_id": "cap-1", "developer": "agent-a"},
        {"capsule_id": "cap-2", "developer": "agent-b"},
        {"capsule_id": "cap-3", "developer": "agent-a"},
    ]
    first = evaluate_one(definition, records, key_value="agent-a")
    second = evaluate_one(definition, records, key_value="agent-a")
    assert first.fold_identity() == second.fold_identity()
    assert first.input_set_digest == second.input_set_digest


def test_identity_is_definition_digest_range_input_set_digest_triple():
    definition = parse_definition(DEFINITION)
    records = [{"capsule_id": "cap-1", "developer": "agent-a"}]
    trace = evaluate_one(definition, records, key_value="agent-a")
    assert trace.fold_identity() == (definition.definition_digest(), trace.range_, trace.input_set_digest)


def test_different_input_set_yields_a_different_identity():
    definition = parse_definition(DEFINITION)
    records_a = [{"capsule_id": "cap-1", "developer": "agent-a"}]
    records_b = [{"capsule_id": "cap-2", "developer": "agent-a"}]
    trace_a = evaluate_one(definition, records_a, key_value="agent-a")
    trace_b = evaluate_one(definition, records_b, key_value="agent-a")
    assert trace_a.fold_identity() != trace_b.fold_identity()
    assert trace_a.input_set_digest != trace_b.input_set_digest


def test_a_different_definition_yields_a_different_identity_even_over_the_same_records():
    records = [{"capsule_id": "cap-1", "developer": "agent-a"}]
    definition_a = parse_definition(DEFINITION)
    definition_b = parse_definition({**DEFINITION, "fold_id": "test.identity.other/1.0.0"})
    trace_a = evaluate_one(definition_a, records, key_value="agent-a")
    trace_b = evaluate_one(definition_b, records, key_value="agent-a")
    assert trace_a.input_set_digest == trace_b.input_set_digest  # same inputs...
    assert trace_a.fold_identity() != trace_b.fold_identity()  # ...but different fold identity


def test_input_set_digest_covers_every_considered_record_not_just_matched():
    definition = parse_definition(DEFINITION)
    # "other" has no `developer` field -- it is considered (in the range) and
    # skipped (no reads resolved), but it must still be part of the input set:
    # the range's contents, not just the ones that fed the reducer.
    records = [{"capsule_id": "cap-1", "developer": "agent-a"}, {"capsule_id": "cap-2", "other": True}]
    trace = evaluate_one(definition, records, key_value="agent-a")
    assert trace.input_capsule_ids == ("cap-1", "cap-2")
    assert trace.matched_capsule_ids == ("cap-1",)
    assert trace.considered_count == 2
    assert trace.skipped_count == 1
    assert trace.matched_count == 1


def test_citations_cite_inputs_by_digest():
    definition = parse_definition(DEFINITION)
    # matched_count (and so matched_capsule_ids) is evaluation-wide, not
    # per-group -- matches the existing considered/skipped/matched-count
    # convention (both "agent-a" and "agent-b" pass the (no) filter here).
    records = [{"capsule_id": "cap-1", "developer": "agent-a"}, {"capsule_id": "cap-2", "developer": "agent-b"}]
    trace = evaluate_one(definition, records, key_value="agent-a")
    citations = trace.citations()
    assert citations["input_capsule_ids"] == ["cap-1", "cap-2"]
    assert citations["cited_capsule_ids"] == ["cap-1", "cap-2"]
    assert citations["input_set_digest"] == trace.input_set_digest


def test_records_without_a_capsule_id_still_get_a_stable_content_digest_identity():
    definition = parse_definition(DEFINITION)
    records = [{"developer": "agent-a"}]  # no capsule_id -- e.g. a synthetic fixture
    first = evaluate_one(definition, records, key_value="agent-a")
    second = evaluate_one(definition, records, key_value="agent-a")
    assert first.input_capsule_ids == second.input_capsule_ids
    assert first.input_capsule_ids != ("",)


def test_evaluate_all_gives_every_group_the_same_fold_identity_for_one_evaluation():
    # Identity is per-evaluation (the range + its full input set), not
    # per-group -- two groups folded out of the same range share it.
    definition = parse_definition(DEFINITION)
    records = [
        {"capsule_id": "cap-1", "developer": "agent-a"},
        {"capsule_id": "cap-2", "developer": "agent-b"},
    ]
    traces = evaluate_all(definition, records)
    identities = {trace.fold_identity() for trace in traces.values()}
    assert len(identities) == 1


def test_a_float_in_an_unread_field_does_not_break_identity():
    # Spec §3 rule 2 ("no floats") is enforced by reducers.py at the point a
    # value enters arithmetic -- it must not leak into "can this record even
    # get an identity", or a record with a float anywhere would make every
    # fold over it blow up regardless of whether that field is read.
    definition = parse_definition(DEFINITION)
    records = [{"developer": "agent-a", "unrelated_score": 42.5}]
    first = evaluate_one(definition, records, key_value="agent-a")
    second = evaluate_one(definition, records, key_value="agent-a")
    assert first.input_capsule_ids == second.input_capsule_ids
    assert first.input_set_digest == second.input_set_digest


def test_no_match_branch_still_reports_a_full_input_set_digest():
    definition = parse_definition(DEFINITION)
    records = [{"capsule_id": "cap-1", "developer": "agent-a"}]
    trace = evaluate_one(definition, records, key_value="agent-never-appeared")
    assert trace.matched_capsule_ids == ()
    assert trace.input_capsule_ids == ("cap-1",)
    assert trace.input_set_digest  # non-empty: still a real digest over the range's contents
