# SPDX-License-Identifier: Apache-2.0
"""The committed coverage fixture is exactly what the generator builds, and
it validates and verifies. Regenerate with
``python scripts/generate_evidence_result_coverage_fixture.py``."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from capsule_engine.report.result import validate_against_schema, verify_result

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "generate_evidence_result_coverage_fixture", ROOT / "scripts" / "generate_evidence_result_coverage_fixture.py"
)
gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen)


def test_committed_fixture_matches_generator():
    assert gen.FIXTURE_PATH.read_text() == gen.render(gen.build_coverage_fixture())


def test_fixture_validates_and_verifies():
    doc = json.loads(gen.FIXTURE_PATH.read_text())
    validate_against_schema(doc)
    verify_result(doc)


def test_fixture_shows_one_requirement_per_state():
    rows = json.loads(gen.FIXTURE_PATH.read_text())["coverage_report"]["requirements"]
    assert [(r["requirement_ref"], r["status"], r["sufficiency"]) for r in rows] == [
        ("req-human-role-1", "SATISFIED", "SATISFIED"),
        ("req-human-role-2", "NOT_FOUND", "GAP"),
        ("req-human-role-3", "INSUFFICIENT", "INSUFFICIENT"),
    ]
    assert rows[1]["gaps"][0]["remedy"] == {"connector": "system_of_record", "raises_to": "retrospectively_evidenced"}
    assert rows[2]["gaps"][0]["kind"] == "correlated_only"
    assert rows[2]["independence"] == {
        "required_producers": 2,
        "independent_producers": 1,
        "correlated_records": 3,
        "unattributed_records": 0,
        "producer_basis": "asserted",
        "met": False,
    }


def test_claim_sufficiency_agrees_with_coverage():
    doc = json.loads(gen.FIXTURE_PATH.read_text())
    claims = {c["id"]: c for c in doc["claims"]}
    for row in doc["coverage_report"]["requirements"]:
        for claim_id in row["claim_ids"]:
            assert claims[claim_id]["sufficiency"] == row["sufficiency"]


def test_fixture_types_catalogued_sources_only():
    rows = json.loads(gen.FIXTURE_PATH.read_text())["coverage_report"]["requirements"]
    types = {s["source"]: s.get("epistemic_type") for r in rows for s in r["sources"]}
    assert types["override-events"] == "HUMAN_REPORT"
    assert types["authority-competence-record"] is None
