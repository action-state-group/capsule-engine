# SPDX-License-Identifier: Apache-2.0
"""Fold engine vectors (spec §6, the shipping gate): per-reducer KATs,
determinism mutants, and MUST-FAIL cases with named reasons.

Each case is a directory under ``fold_vectors/``:

- ``kat/<name>/``: definition.yaml, records.jsonl, expected.json
  (``{key_value: expected_result}``).
- ``determinism/<name>/``: definition.yaml, base.jsonl, mutant.jsonl,
  expected.json; the mutant (permuted or injected unknown fields) MUST produce
  the same result as the base stream.
- ``must_fail/<name>/``: definition.yaml, reason.txt, and optionally
  records.jsonl and key.txt; loading or evaluating MUST raise the named reason.

The files moved here unchanged from the archived capsule-ledger's
``capsule_ledger/vectors/fixtures`` (#170); this fold runtime is their only
consumer."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from capsule_engine.folds.engine import evaluate_one
from capsule_engine.folds.errors import FoldDefinitionError, FoldDeterminismError
from capsule_engine.folds.loader import load_definition_file

VECTORS = Path(__file__).resolve().parent / "fold_vectors"


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _cases(kind: str) -> list[Path]:
    cases = sorted(p for p in (VECTORS / kind).iterdir() if p.is_dir())
    # An empty or missing directory would make every parametrized test vanish silently.
    assert cases, f"no {kind} fold vectors under {VECTORS}"
    return cases


def kat_cases() -> list[Path]:
    return _cases("kat")


def determinism_cases() -> list[Path]:
    return _cases("determinism")


def must_fail_cases() -> list[Path]:
    return _cases("must_fail")


@pytest.mark.parametrize("case", kat_cases(), ids=lambda p: p.name)
def test_kat(case):
    definition = load_definition_file(case / "definition.yaml")
    records = read_jsonl(case / "records.jsonl")
    expected = json.loads((case / "expected.json").read_text())
    assert expected, f"{case.name}: expected.json must declare at least one key"

    for key_value, expected_result in expected.items():
        trace = evaluate_one(definition, records, key_value=key_value)
        assert trace.result == expected_result, f"{case.name}[{key_value}]"
        # Byte-exact through the envelope too, not just the raw accumulator.
        assert trace.to_envelope()["result"] == expected_result


@pytest.mark.parametrize("case", determinism_cases(), ids=lambda p: p.name)
def test_determinism_mutant(case):
    definition = load_definition_file(case / "definition.yaml")
    base_records = read_jsonl(case / "base.jsonl")
    mutant_records = read_jsonl(case / "mutant.jsonl")
    expected = json.loads((case / "expected.json").read_text())
    assert expected, f"{case.name}: expected.json must declare at least one key"

    for key_value, expected_result in expected.items():
        base_trace = evaluate_one(definition, base_records, key_value=key_value)
        mutant_trace = evaluate_one(definition, mutant_records, key_value=key_value)
        assert base_trace.result == expected_result, f"{case.name}[{key_value}] base"
        assert mutant_trace.result == expected_result, f"{case.name}[{key_value}] mutant"
        assert base_trace.result == mutant_trace.result, f"{case.name}[{key_value}] base != mutant"


@pytest.mark.parametrize("case", must_fail_cases(), ids=lambda p: p.name)
def test_must_fail(case):
    expected_reason = (case / "reason.txt").read_text().strip()
    records_path = case / "records.jsonl"
    key_path = case / "key.txt"
    key_value = key_path.read_text().strip() if key_path.exists() else None

    raised: Exception | None = None
    try:
        definition = load_definition_file(case / "definition.yaml")
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
