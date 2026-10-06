# SPDX-License-Identifier: Apache-2.0
"""The UNKNOWN-sufficiency Result fixtures: exactly what the generator
builds, schema-valid, and pinned tightly enough that an implementation which
drops UNKNOWN claims or remaps UNKNOWN to another sufficiency cannot match
them. Regenerate with
``python scripts/generate_evidence_result_unknown_fixtures.py``.

Two remaps are self-consistent and pass both ``validate_against_schema`` and
``verify_result``: UNKNOWN -> GAP with ``unknown_count`` recomputed, and
UNKNOWN claims dropped with the aggregate recomputed. Only the pinned bytes
and the pinned sufficiency sequence catch them. The mutant tests below build
each remap and show which check reds.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from collections import Counter
from pathlib import Path
from typing import TypedDict

import pytest

from capsule_engine.report.result import validate_against_schema, verify_result

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "generate_evidence_result_unknown_fixtures", ROOT / "scripts" / "generate_evidence_result_unknown_fixtures.py"
)
gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen)

RESULT_FIXTURE_DIR = ROOT / "tests" / "fixtures" / "evidence-result"


class ClaimDoc(TypedDict):
    id: str
    sufficiency: str
    verdict: str


class CoverageDoc(TypedDict):
    evaluated_population: int
    excluded_not_applicable: int
    unknown_count: int


class AggregateDoc(TypedDict):
    coverage: CoverageDoc
    buckets: dict[str, list[str]]


class ResultDoc(TypedDict):
    """The parts of a Result v0 document these checks read."""

    claims: list[ClaimDoc]
    aggregate: AggregateDoc

# Pinned per fixture: the sufficiency of each claim in order, and the sha256
# of the committed bytes. capsule-viewer's tests/testdata carries the same
# files and pins the same digests.
EXPECTED = {
    "unknown-claim-result.json": (
        ["SATISFIED", "UNKNOWN"],
        "b963016e72560f17a34f071c1d79bc5518b3accd607fec0730f6b4574d675547",
    ),
    "unknown-count-aggregate-result.json": (
        ["SATISFIED", "UNKNOWN", "GAP", "SATISFIED", "UNKNOWN", "INSUFFICIENT"],
        "b0c78c24c892802357455081224142ad7e4a5d1db82750053c48798ba04f126e",
    ),
}


def _load(name: str) -> ResultDoc:
    return json.loads((RESULT_FIXTURE_DIR / name).read_text())


def _sufficiencies(doc: ResultDoc) -> list[str]:
    return [claim["sufficiency"] for claim in doc["claims"]]


def _coverage_disagreements(doc: ResultDoc) -> list[str]:
    """Where the stated coverage disagrees with a count off the claims array.
    ``excluded_not_applicable`` is left out: excluded requirements never
    become claims, so the document cannot check that number against itself."""
    coverage = doc["aggregate"]["coverage"]
    counted = Counter(_sufficiencies(doc))
    problems = []
    if coverage["evaluated_population"] != len(doc["claims"]):
        problems.append(f"evaluated_population {coverage['evaluated_population']} != {len(doc['claims'])} claims")
    if coverage["unknown_count"] != counted["UNKNOWN"]:
        problems.append(f"unknown_count {coverage['unknown_count']} != {counted['UNKNOWN']} UNKNOWN claims")
    return problems


def _remap_unknown(doc: ResultDoc, to: str) -> ResultDoc:
    """An implementation that reports UNKNOWN as ``to`` and recomputes the
    aggregate from its own claims: self-consistent, and wrong."""
    out = json.loads(json.dumps(doc))
    for claim in out["claims"]:
        if claim["sufficiency"] == "UNKNOWN":
            claim["sufficiency"] = to
    out["aggregate"]["coverage"]["unknown_count"] = 0
    return out


def _drop_unknown(doc: ResultDoc) -> ResultDoc:
    """An implementation that never emits an UNKNOWN claim and recomputes
    the aggregate from what it did emit."""
    out = json.loads(json.dumps(doc))
    dropped = {c["id"] for c in out["claims"] if c["sufficiency"] == "UNKNOWN"}
    out["claims"] = [c for c in out["claims"] if c["id"] not in dropped]
    out["aggregate"]["coverage"]["evaluated_population"] = len(out["claims"])
    out["aggregate"]["coverage"]["unknown_count"] = 0
    buckets = out["aggregate"]["buckets"]
    for key in buckets:
        buckets[key] = [cid for cid in buckets[key] if cid not in dropped]
    return out


def unknown_claims_in(paths: list[Path]) -> int:
    count = 0
    for path in paths:
        doc = json.loads(path.read_text())
        if isinstance(doc, dict) and isinstance(doc.get("claims"), list):
            count += sum(1 for claim in doc["claims"] if claim.get("sufficiency") == "UNKNOWN")
    return count


def assert_corpus_exercises_unknown(paths: list[Path]) -> None:
    count = unknown_claims_in(paths)
    assert count >= 1, f"no UNKNOWN claim in {len(paths)} Result fixtures"


# --- the committed fixtures ------------------------------------------------


@pytest.mark.parametrize("path", list(gen.FIXTURES))
def test_committed_fixture_matches_generator(path: Path):
    assert path.read_text() == gen.render(gen.FIXTURES[path]())


@pytest.mark.parametrize("name", list(EXPECTED))
def test_committed_fixture_bytes_are_pinned(name: str):
    assert hashlib.sha256((RESULT_FIXTURE_DIR / name).read_bytes()).hexdigest() == EXPECTED[name][1]


@pytest.mark.parametrize("name", list(EXPECTED))
def test_fixture_validates_and_verifies(name: str):
    doc = _load(name)
    validate_against_schema(doc)
    verify_result(doc)


@pytest.mark.parametrize("name", list(EXPECTED))
def test_fixture_sufficiency_sequence_is_pinned(name: str):
    assert _sufficiencies(_load(name)) == EXPECTED[name][0]


def test_unknown_claim_is_not_evaluable_and_bucketed_so():
    doc = _load("unknown-claim-result.json")
    unknown = [c for c in doc["claims"] if c["sufficiency"] == "UNKNOWN"]
    assert [c["id"] for c in unknown] == ["claim-2"]
    assert unknown[0]["verdict"] == "not_evaluable"
    assert unknown[0]["presentation"]["status"] == "UNKNOWN"
    assert doc["aggregate"]["buckets"]["not_evaluable"] == ["claim-2"]


def test_unknown_count_is_nonzero_and_agrees_with_claims():
    doc = _load("unknown-count-aggregate-result.json")
    assert doc["aggregate"]["coverage"] == {
        "evaluated_population": 6,
        "excluded_not_applicable": 0,
        "unknown_count": 2,
    }
    assert _coverage_disagreements(doc) == []


# --- the mutants: each wrong implementation reds a named check -------------


@pytest.mark.parametrize("name", list(EXPECTED))
@pytest.mark.parametrize("to", ["GAP", "INSUFFICIENT"])
def test_mutant_remap_unknown_is_self_consistent_but_reds_the_pin(name: str, to: str):
    mutant = _remap_unknown(_load(name), to)
    # Passes everything a document can check about itself ...
    validate_against_schema(mutant)
    verify_result(mutant)
    assert _coverage_disagreements(mutant) == []
    # ... and is caught only by the pins.
    assert _sufficiencies(mutant) != EXPECTED[name][0]
    assert mutant != _load(name)


@pytest.mark.parametrize("name", list(EXPECTED))
def test_mutant_drop_unknown_is_self_consistent_but_reds_the_pin(name: str):
    mutant = _drop_unknown(_load(name))
    validate_against_schema(mutant)
    verify_result(mutant)
    assert _coverage_disagreements(mutant) == []
    assert "UNKNOWN" not in _sufficiencies(mutant)
    assert _sufficiencies(mutant) != EXPECTED[name][0]


def test_mutant_stale_unknown_count_reds_the_agreement_check():
    """Claims remapped but the aggregate left as stated: the count no longer
    agrees with the claims array."""
    doc = _load("unknown-count-aggregate-result.json")
    for claim in doc["claims"]:
        if claim["sufficiency"] == "UNKNOWN":
            claim["sufficiency"] = "GAP"
    assert _coverage_disagreements(doc) == ["unknown_count 2 != 0 UNKNOWN claims"]


# --- the corpus floor --------------------------------------------------------
#
# The population is the POSITIVE Result fixtures: what a conforming
# implementation must reproduce. ``neg-*`` files are vectors it must reject,
# so they are never counted. Before these fixtures, the positive corpus
# across this repo and capsule-viewer was 23 claims / 9 files
# (SATISFIED 16, GAP 4, INSUFFICIENT 3, UNKNOWN 0).


def positive_result_fixtures(directory: Path) -> list[Path]:
    return sorted(p for p in directory.glob("*.json") if not p.name.startswith("neg-"))


def test_result_fixture_corpus_exercises_unknown():
    assert_corpus_exercises_unknown(positive_result_fixtures(RESULT_FIXTURE_DIR))


def test_negative_vectors_are_outside_the_population(tmp_path: Path):
    (tmp_path / "neg-planted.json").write_text(json.dumps(_load("unknown-claim-result.json")))
    assert positive_result_fixtures(tmp_path) == []


def test_planted_control_corpus_without_unknown_reds_the_floor(tmp_path: Path):
    for path in positive_result_fixtures(RESULT_FIXTURE_DIR):
        doc = json.loads(path.read_text())
        (tmp_path / path.name).write_text(json.dumps(_remap_unknown(doc, "GAP")))
    planted = sorted(tmp_path.glob("*.json"))
    assert unknown_claims_in(planted) == 0
    with pytest.raises(AssertionError, match="no UNKNOWN claim"):
        assert_corpus_exercises_unknown(planted)
