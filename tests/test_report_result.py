# SPDX-License-Identifier: Apache-2.0
"""Result v0 model: construction guards, aggregate computation, schema
validation, and the two normative cross-checks."""
from __future__ import annotations

import jsonschema
import pytest

from capsule_engine.report.errors import ResultError
from capsule_engine.report.result import (
    AnalysisCarrier,
    Claim,
    DigestRef,
    DisclosureCarrier,
    ProofRef,
    StoryCarrier,
    build_result,
    validate_against_schema,
    verify_result,
)

_DIGEST_A = "a" * 64
_DIGEST_B = "b" * 64
_PROOF_A = "c" * 64


def _digest_ref(d: str = _DIGEST_A) -> DigestRef:
    return DigestRef(digest=d)


def _proof_ref(d: str = _PROOF_A) -> ProofRef:
    return ProofRef(kind="inclusion_proof", digest=d)


def _claim(
    *,
    claim_id: str = "claim-1",
    sufficiency: str = "SATISFIED",
    verdict: str = "met",
    presentation=None,
) -> Claim:
    presentation = presentation or DisclosureCarrier(status="SATISFIED", evidence=(_digest_ref(),))
    return Claim(
        id=claim_id,
        contract_ref="ec:example-org-test:2026-09-22@1",
        requirement_ref=f"req-{claim_id}",
        tier="recomputed",
        grade="self-attested",
        sufficiency=sufficiency,
        verdict=verdict,
        evidence=(_digest_ref(),),
        proofs=(_proof_ref(),),
        presentation=presentation,
    )


# --- tier: both closed values construct; "judged" has no adapter in this
# repo (no capsule-judge integration -- result_from_folds.py's docstring),
# but the Claim model itself must accept a caller who already has one. -----


def test_judged_tier_claim_constructs():
    claim = Claim(
        id="c",
        contract_ref="ec:x@1",
        requirement_ref="r",
        tier="judged",
        grade="witnessed",
        sufficiency="SATISFIED",
        verdict="met",
        evidence=(_digest_ref(),),
        proofs=(_proof_ref(),),
        presentation=DisclosureCarrier(status="SATISFIED", evidence=(_digest_ref(),)),
    )
    assert claim.tier == "judged"


# --- DigestRef/ProofRef: hex-digest discipline -----------------------------


def test_digest_ref_accepts_64_lowercase_hex():
    assert _digest_ref().digest == _DIGEST_A


@pytest.mark.parametrize("bad", ["A" * 64, "g" * 64, "a" * 63, "a" * 65, ""])
def test_mutant_digest_ref_rejects_non_hex64(bad):
    with pytest.raises(ResultError):
        DigestRef(digest=bad)


# --- Claim: sufficiency/verdict pairing rule (spec section 1) --------------


def test_satisfied_sufficiency_allows_met_or_not_met():
    assert _claim(sufficiency="SATISFIED", verdict="met").verdict == "met"
    assert _claim(sufficiency="SATISFIED", verdict="not_met").verdict == "not_met"


@pytest.mark.parametrize("sufficiency", ["GAP", "INSUFFICIENT", "UNKNOWN"])
def test_non_satisfied_sufficiency_requires_not_evaluable(sufficiency):
    claim = _claim(
        sufficiency=sufficiency,
        verdict="not_evaluable",
        presentation=DisclosureCarrier(status="INSUFFICIENT", evidence=(_digest_ref(),)),
    )
    assert claim.verdict == "not_evaluable"


def test_mutant_satisfied_sufficiency_with_not_evaluable_is_rejected():
    """The rule's first half: SATISFIED sufficiency asserting not_evaluable
    is malformed (spec section 1) -- proves the guard fires, not just that
    the happy path passes."""
    with pytest.raises(ResultError) as exc:
        _claim(sufficiency="SATISFIED", verdict="not_evaluable")
    assert exc.value.reason == "sufficiency_verdict_mismatch"


@pytest.mark.parametrize("sufficiency", ["GAP", "INSUFFICIENT", "UNKNOWN"])
def test_mutant_non_satisfied_sufficiency_with_met_is_rejected(sufficiency):
    """The rule's second half: GAP/INSUFFICIENT/UNKNOWN sufficiency
    asserting a real verdict is malformed."""
    with pytest.raises(ResultError) as exc:
        _claim(
            sufficiency=sufficiency,
            verdict="met",
            presentation=DisclosureCarrier(status="INSUFFICIENT", evidence=(_digest_ref(),)),
        )
    assert exc.value.reason == "sufficiency_verdict_mismatch"


def test_mutant_unknown_tier_is_rejected():
    with pytest.raises(ResultError) as exc:
        Claim(
            id="c",
            contract_ref="ec:x@1",
            requirement_ref="r",
            tier="invented-tier",
            grade="self-attested",
            sufficiency="SATISFIED",
            verdict="met",
            evidence=(_digest_ref(),),
            proofs=(_proof_ref(),),
            presentation=DisclosureCarrier(status="SATISFIED", evidence=(_digest_ref(),)),
        )
    assert exc.value.reason == "invalid_result_tier"


def test_mutant_unknown_grade_is_rejected():
    with pytest.raises(ResultError) as exc:
        Claim(
            id="c",
            contract_ref="ec:x@1",
            requirement_ref="r",
            tier="recomputed",
            grade="trust-me",
            sufficiency="SATISFIED",
            verdict="met",
            evidence=(_digest_ref(),),
            proofs=(_proof_ref(),),
            presentation=DisclosureCarrier(status="SATISFIED", evidence=(_digest_ref(),)),
        )
    assert exc.value.reason == "invalid_result_grade"


# --- Disclosure gate (spec section 2) --------------------------------------


def test_disclosure_carrier_legal_for_satisfied_status():
    DisclosureCarrier(status="SATISFIED", evidence=(_digest_ref(),))


@pytest.mark.parametrize("status", ["WITHHELD", "NOT_COMMITTED"])
def test_mutant_disclosure_carrier_illegal_for_withheld_or_not_committed(status):
    """spec section 2's gate: 'disclosure is not a legal choice,
    structurally' for WITHHELD/NOT_COMMITTED -- this is the exact rule a
    renderer could otherwise leak withheld evidence through."""
    with pytest.raises(ResultError) as exc:
        DisclosureCarrier(status=status, evidence=(_digest_ref(),))
    assert exc.value.reason == "invalid_disclosed_status"


@pytest.mark.parametrize("status", ["WITHHELD", "NOT_COMMITTED"])
def test_analysis_and_story_carriers_are_legal_for_withheld_or_not_committed(status):
    AnalysisCarrier(status=status, summary="characterization, never a quote")
    StoryCarrier(status=status, narrative="a request was made and no artifact resulted")


# --- build_result: aggregate computed from claims (spec section 3) --------


def test_build_result_computes_coverage_and_buckets_from_claims():
    claims = [
        _claim(claim_id="claim-1", sufficiency="SATISFIED", verdict="met"),
        _claim(claim_id="claim-2", sufficiency="SATISFIED", verdict="not_met"),
        _claim(
            claim_id="claim-3",
            sufficiency="UNKNOWN",
            verdict="not_evaluable",
            presentation=DisclosureCarrier(status="UNKNOWN", evidence=(_digest_ref(),)),
        ),
    ]
    result = build_result(claims, generated_at="2026-09-22T00:00:00Z", excluded_not_applicable=1)
    doc = result.to_dict()
    assert doc["aggregate"]["coverage"] == {
        "evaluated_population": 3,
        "excluded_not_applicable": 1,
        "unknown_count": 1,
    }
    assert doc["aggregate"]["buckets"] == {
        "met": ["claim-1"],
        "not_met": ["claim-2"],
        "not_evaluable": ["claim-3"],
    }


def test_mutant_build_result_rejects_duplicate_claim_ids():
    """Proves the id-uniqueness guard fires inside build_result itself, not
    only in verify_result's post-hoc check."""
    claims = [_claim(claim_id="dup"), _claim(claim_id="dup", verdict="not_met")]
    with pytest.raises(ResultError) as exc:
        build_result(claims, generated_at="2026-09-22T00:00:00Z")
    assert exc.value.reason == "duplicate_claim_id"


def test_build_result_never_accepts_a_caller_supplied_aggregate():
    """There is no aggregate= parameter on build_result at all -- the only
    way to influence the aggregate is through the claims themselves (plus
    the one count that cannot be derived from claims, excluded_not_applicable).
    A regression that added such a parameter would be exactly the
    'templated, not computed' bug section 3 forbids."""
    import inspect

    assert "aggregate" not in inspect.signature(build_result).parameters


# --- Schema validation (item 4) --------------------------------------------


def test_emitted_result_validates_against_the_public_schema():
    claims = [_claim(claim_id="claim-1")]
    result = build_result(claims, generated_at="2026-09-22T00:00:00Z")
    validate_against_schema(result.to_dict())  # raises on failure
    verify_result(result.to_dict())


def test_mutant_schema_validation_rejects_a_result_missing_aggregate():
    """Proves validate_against_schema actually looks at the document, by
    removing the one field spec/schema make mandatory above the fold."""
    claims = [_claim(claim_id="claim-1")]
    doc = build_result(claims, generated_at="2026-09-22T00:00:00Z").to_dict()
    del doc["aggregate"]
    with pytest.raises(jsonschema.exceptions.ValidationError):
        validate_against_schema(doc)


def test_mutant_schema_validation_rejects_aggregate_without_coverage():
    claims = [_claim(claim_id="claim-1")]
    doc = build_result(claims, generated_at="2026-09-22T00:00:00Z").to_dict()
    del doc["aggregate"]["coverage"]
    with pytest.raises(jsonschema.exceptions.ValidationError):
        validate_against_schema(doc)


# --- verify_result: normative cross-checks not expressible in JSON Schema -


def test_mutant_verify_result_catches_a_bucket_naming_a_nonexistent_claim():
    claims = [_claim(claim_id="claim-1")]
    doc = build_result(claims, generated_at="2026-09-22T00:00:00Z").to_dict()
    doc["aggregate"]["buckets"]["met"].append("claim-does-not-exist")
    with pytest.raises(ResultError) as exc:
        verify_result(doc)
    assert exc.value.reason == "bucket_claim_mismatch"


def test_mutant_verify_result_catches_a_claim_in_the_wrong_bucket():
    claims = [_claim(claim_id="claim-1", verdict="met")]
    doc = build_result(claims, generated_at="2026-09-22T00:00:00Z").to_dict()
    doc["aggregate"]["buckets"]["met"] = []
    doc["aggregate"]["buckets"]["not_met"] = ["claim-1"]
    with pytest.raises(ResultError) as exc:
        verify_result(doc)
    assert exc.value.reason == "bucket_claim_mismatch"


def test_mutant_verify_result_catches_a_claim_in_no_bucket():
    claims = [_claim(claim_id="claim-1", verdict="met")]
    doc = build_result(claims, generated_at="2026-09-22T00:00:00Z").to_dict()
    doc["aggregate"]["buckets"]["met"] = []
    with pytest.raises(ResultError) as exc:
        verify_result(doc)
    assert exc.value.reason == "bucket_claim_mismatch"
