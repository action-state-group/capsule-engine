# SPDX-License-Identifier: Apache-2.0
"""Fold engine vectors (spec §6, the shipping gate): per-reducer KATs,
determinism mutants, and MUST-FAIL cases with named reasons."""
from __future__ import annotations

import json

import pytest

from capsule_ledger.folds.engine import evaluate_one
from capsule_ledger.folds.errors import FoldDefinitionError, FoldDeterminismError
from capsule_ledger.folds.loader import load_definition_file
from capsule_ledger.vectors import determinism_cases, kat_cases, must_fail_cases
from capsule_ledger.vectors.runner import read_jsonl


@pytest.mark.parametrize("case", kat_cases(), ids=lambda c: c.name)
def test_kat(case):
    definition = load_definition_file(case.directory / "definition.yaml")
    records = read_jsonl(case.directory / "records.jsonl")
    expected = json.loads((case.directory / "expected.json").read_text())
    assert expected, f"{case.name}: expected.json must declare at least one key"

    for key_value, expected_result in expected.items():
        trace = evaluate_one(definition, records, key_value=key_value)
        assert trace.result == expected_result, f"{case.name}[{key_value}]"
        # Byte-exact through the envelope too, not just the raw accumulator.
        assert trace.to_envelope()["result"] == expected_result


@pytest.mark.parametrize("case", determinism_cases(), ids=lambda c: c.name)
def test_determinism_mutant(case):
    definition = load_definition_file(case.directory / "definition.yaml")
    base_records = read_jsonl(case.directory / "base.jsonl")
    mutant_records = read_jsonl(case.directory / "mutant.jsonl")
    expected = json.loads((case.directory / "expected.json").read_text())
    assert expected, f"{case.name}: expected.json must declare at least one key"

    for key_value, expected_result in expected.items():
        base_trace = evaluate_one(definition, base_records, key_value=key_value)
        mutant_trace = evaluate_one(definition, mutant_records, key_value=key_value)
        assert base_trace.result == expected_result, f"{case.name}[{key_value}] base"
        assert mutant_trace.result == expected_result, f"{case.name}[{key_value}] mutant"
        assert base_trace.result == mutant_trace.result, f"{case.name}[{key_value}] base != mutant"


@pytest.mark.parametrize("case", must_fail_cases(), ids=lambda c: c.name)
def test_must_fail(case):
    expected_reason = (case.directory / "reason.txt").read_text().strip()
    records_path = case.directory / "records.jsonl"
    key_path = case.directory / "key.txt"
    key_value = key_path.read_text().strip() if key_path.exists() else None

    raised: Exception | None = None
    try:
        definition = load_definition_file(case.directory / "definition.yaml")
        if records_path.exists():
            records = read_jsonl(records_path)
            evaluate_one(definition, records, key_value=key_value)
    except (FoldDefinitionError, FoldDeterminismError) as exc:
        raised = exc

    assert raised is not None, (
        f"{case.name} was expected to fail with reason {expected_reason!r} but did not raise "
        "(a MUST-FAIL vector that never fails is not a check)"
    )
    assert raised.reason == expected_reason, f"{case.name}: got reason {raised.reason!r}"
