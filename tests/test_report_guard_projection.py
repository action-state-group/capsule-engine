# SPDX-License-Identifier: Apache-2.0
"""The guard-constraint projection in ``report/result_from_folds.py``
(``project_guard_constraint``, ``claim_from_guard_constraint``), exercised on
the everyday pack's sealed fixture records. Every assertion names the field
on the named record or aggregate."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from capsule_engine.packs.schema import NORMALIZED_ACTION_FIELDS
from capsule_engine.report.result import build_result, validate_against_schema, verify_result
from capsule_engine.report.result_from_folds import claim_from_guard_constraint, project_guard_constraint

LEDGER = Path(__file__).parent.parent / "capsule_engine" / "packs" / "catalog" / "everyday" / "fixtures" / "mini_ledger.jsonl"
FIELDS = frozenset(NORMALIZED_ACTION_FIELDS)

# The caps constraint record exactly as the engine sealed it before every n/a
# carried a facts object: a real verify_before_dispatch n/a record from the
# payments-safety fixture at capsule-engine 3e6e5014533c07517fea6779bf50d996ca5c57c1
# (capsule 1870189b2e23c0c86ea95f2b945ffff33ec430da295ff7348eefee60efff7ad9).
# Synthetic as an input to this engine -- it cannot produce one any more.
PRE_FIX_N_A = {
    "id": "verify_before_dispatch",
    "result": "n/a",
    "severity": "blocking",
    "check_type": "policy",
    "method": "agent_action_capsule.verify",
}
PRE_FIX_CAPSULE = {
    "capsule_id": "1870189b2e23c0c86ea95f2b945ffff33ec430da295ff7348eefee60efff7ad9",
    "assurance": {"attestation_mode": "self_attested"},
}


# Reads raw capsule or fixture JSON/YAML: the test's decoding boundary.
def _capsules() -> dict[str, dict]:
    out = {}
    for line in LEDGER.read_text().splitlines():
        capsule = json.loads(line)
        if "constraints" in capsule:
            out[capsule["action_id"].split("fixture-")[1]] = capsule
    return out


# Reads raw capsule or fixture JSON/YAML: the test's decoding boundary.
def _caps(capsule: dict) -> dict:
    (record,) = [c for c in capsule["constraints"] if c["id"] == "caps"]
    return record


# Reads raw capsule or fixture JSON/YAML: the test's decoding boundary.
def _claim(capsule: dict, record: dict, n: int = 0):
    return claim_from_guard_constraint(
        capsule,
        record,
        claim_id=f"{record['id']}-{n}",
        contract_ref="asg/everyday@0.2.0",
        requirement_ref=record["id"],
        candidate_fields=FIELDS,
    )


def test_settled_records_project_to_satisfied():
    capsules = _capsules()
    passed = project_guard_constraint(_caps(capsules["caps-boundary"]), candidate_fields=FIELDS)
    failed = project_guard_constraint(_caps(capsules["caps-over-limit"]), candidate_fields=FIELDS)
    assert (passed.evidence_status, passed.sufficiency, passed.verdict) == ("SATISFIED", "SATISFIED", "met")
    assert (failed.evidence_status, failed.sufficiency, failed.verdict) == ("SATISFIED", "SATISFIED", "not_met")


def test_in_scope_missing_amount_projects_to_not_found_gap_not_evaluable():
    projected = project_guard_constraint(_caps(_capsules()["caps-amount-missing"]), candidate_fields=FIELDS)
    assert projected.evidence_status == "NOT_FOUND"
    assert projected.sufficiency == "GAP"
    assert projected.verdict == "not_evaluable"
    assert projected.excluded is False
    assert projected.recording_defect is False


def test_out_of_scope_projects_to_an_exclusion_and_never_a_claim():
    capsule = _capsules()["out-of-scope"]
    projected = project_guard_constraint(_caps(capsule), candidate_fields=FIELDS)
    assert projected.evidence_status == "NOT_APPLICABLE"
    assert projected.excluded is True
    assert _claim(capsule, _caps(capsule)) is None


def test_a_causeless_n_a_projects_to_unknown_as_a_recording_defect():
    projected = project_guard_constraint(PRE_FIX_N_A, candidate_fields=FIELDS)
    assert projected.evidence_status == "UNKNOWN"
    assert projected.sufficiency == "UNKNOWN"
    assert projected.verdict == "not_evaluable"
    assert projected.recording_defect is True


def test_an_n_a_whose_digest_matches_no_facts_object_is_a_recording_defect():
    record = dict(PRE_FIX_N_A, evidence_digest="ab" * 32)
    assert project_guard_constraint(record, candidate_fields=FIELDS).recording_defect is True


def test_aggregate_puts_the_gap_claim_in_not_evaluable_with_unknown_count_zero():
    capsules = _capsules()
    gap_capsule, excluded_capsule = capsules["caps-amount-missing"], capsules["out-of-scope"]
    claims = [_claim(gap_capsule, _caps(gap_capsule))]
    assert _claim(excluded_capsule, _caps(excluded_capsule)) is None
    result = build_result(claims, generated_at="2026-08-10T11:00:00Z", excluded_not_applicable=1).to_dict()
    validate_against_schema(result)
    verify_result(result)

    coverage = result["aggregate"]["coverage"]
    buckets = result["aggregate"]["buckets"]
    assert coverage["evaluated_population"] == 1
    assert coverage["excluded_not_applicable"] == 1
    assert coverage["unknown_count"] == 0
    assert buckets["not_evaluable"] == ["caps-0"]
    assert result["claims"][0]["sufficiency"] == "GAP"
    assert result["claims"][0]["presentation"]["status"] == "NOT_FOUND"


def test_aggregate_counts_a_recording_defect_in_unknown_count():
    claims = [_claim(PRE_FIX_CAPSULE, PRE_FIX_N_A)]
    result = build_result(claims, generated_at="2026-08-10T11:00:00Z").to_dict()
    validate_against_schema(result)
    verify_result(result)
    assert result["aggregate"]["coverage"]["unknown_count"] == 1
    assert result["aggregate"]["buckets"]["not_evaluable"] == ["verify_before_dispatch-0"]


def test_every_record_in_the_everyday_fixture_projects():
    for capsule in _capsules().values():
        for record in capsule["constraints"]:
            projected = project_guard_constraint(record, candidate_fields=FIELDS)
            assert projected.recording_defect is False, (capsule["action_id"], record["id"])


def test_an_attestation_mode_with_no_defined_grade_is_refused():
    capsule = dict(_capsules()["caps-boundary"], assurance={"attestation_mode": "witnessed_somehow"})
    with pytest.raises(ValueError, match="no Result grade"):
        _claim(capsule, _caps(capsule))
