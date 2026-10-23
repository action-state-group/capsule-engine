#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Regenerate the two UNKNOWN-sufficiency Evidence Result fixtures under
``tests/fixtures/evidence-result/``.

Before these, no Result fixture in this repo or in ``capsule-viewer``
carried a claim whose sufficiency is UNKNOWN. A second implementation that
never emits UNKNOWN, or folds it into another sufficiency, reproduced every
fixture byte for byte. These two close that hole:

- ``unknown-claim-result.json`` -- one SATISFIED/met claim and one
  UNKNOWN/not_evaluable claim. The smallest Result in which UNKNOWN appears.
- ``unknown-count-aggregate-result.json`` -- all four sufficiencies, two of
  them UNKNOWN and not adjacent, so ``aggregate.coverage.unknown_count`` is 2
  and has to agree with the claims array. Remapping UNKNOWN to GAP keeps every
  bucket the same (both are not_evaluable), so only the sufficiency strings and
  ``unknown_count`` reveal it.

Synthetic only: plain claims with digests derived from fixed seeds, no
signing and no clock, so the output is a pure function of this file.
``tests/test_report_result_unknown_fixtures.py`` re-runs it and compares
bytes. ``capsule-viewer`` carries the same bytes under ``tests/testdata/``.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from capsule_engine.report.result import (
    AnalysisCarrier,
    Claim,
    DigestRef,
    DisclosureCarrier,
    EvidenceResult,
    ProofRef,
    StoryCarrier,
    View,
    build_result,
)

ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "evidence-result"
UNKNOWN_CLAIM_PATH = FIXTURE_DIR / "unknown-claim-result.json"
UNKNOWN_COUNT_PATH = FIXTURE_DIR / "unknown-count-aggregate-result.json"
GENERATED_AT = "2026-10-04T00:00:00Z"
CONTRACT_REF = "ec:example-org-unknown-sufficiency:2026-10-04@1"
UNKNOWN_SUMMARY = "The requirement was not evaluated and the cause was not determined."


def _hex(seed: str) -> str:
    return hashlib.sha256(seed.encode()).hexdigest()


def _satisfied(claim_id: str, requirement_ref: str, verdict: str) -> Claim:
    evidence = (DigestRef(_hex(f"{claim_id}/evidence")),)
    return Claim(
        id=claim_id,
        contract_ref=CONTRACT_REF,
        requirement_ref=requirement_ref,
        tier="recomputed",
        grade="witnessed",
        sufficiency="SATISFIED",
        verdict=verdict,
        evidence=evidence,
        proofs=(ProofRef(kind="receipt", digest=_hex(f"{claim_id}/receipt")),),
        presentation=DisclosureCarrier(status="SATISFIED", evidence=evidence),
    )


def _gap(claim_id: str, requirement_ref: str) -> Claim:
    return Claim(
        id=claim_id,
        contract_ref=CONTRACT_REF,
        requirement_ref=requirement_ref,
        tier="recomputed",
        grade="self-attested",
        sufficiency="GAP",
        verdict="not_evaluable",
        evidence=(),
        proofs=(),
        presentation=AnalysisCarrier(status="NOT_FOUND", summary="The required source has no record in the range."),
    )


def _insufficient(claim_id: str, requirement_ref: str) -> Claim:
    evidence = (DigestRef(_hex(f"{claim_id}/evidence")),)
    return Claim(
        id=claim_id,
        contract_ref=CONTRACT_REF,
        requirement_ref=requirement_ref,
        tier="recomputed",
        grade="self-attested",
        sufficiency="INSUFFICIENT",
        verdict="not_evaluable",
        evidence=evidence,
        proofs=(),
        presentation=DisclosureCarrier(status="INSUFFICIENT", evidence=evidence),
    )


def _unknown(claim_id: str, requirement_ref: str, *, story: bool = False) -> Claim:
    presentation = (
        StoryCarrier(status="UNKNOWN", narrative=UNKNOWN_SUMMARY)
        if story
        else AnalysisCarrier(status="UNKNOWN", summary=UNKNOWN_SUMMARY)
    )
    return Claim(
        id=claim_id,
        contract_ref=CONTRACT_REF,
        requirement_ref=requirement_ref,
        tier="recomputed",
        grade="self-attested",
        sufficiency="UNKNOWN",
        verdict="not_evaluable",
        evidence=(),
        proofs=(),
        presentation=presentation,
    )


def build_unknown_claim_fixture() -> EvidenceResult:
    claims = [
        _satisfied("claim-1", "req-1", "met"),
        _unknown("claim-2", "req-2"),
    ]
    view = View(producer_name="EXAMPLE-ORG", title="EXAMPLE-ORG UNKNOWN claim -- Result v0")
    return build_result(claims, generated_at=GENERATED_AT, view=view)


def build_unknown_count_fixture() -> EvidenceResult:
    claims = [
        _satisfied("claim-1", "req-1", "met"),
        _unknown("claim-2", "req-2"),
        _gap("claim-3", "req-3"),
        _satisfied("claim-4", "req-4", "not_met"),
        _unknown("claim-5", "req-5", story=True),
        _insufficient("claim-6", "req-6"),
    ]
    view = View(producer_name="EXAMPLE-ORG", title="EXAMPLE-ORG UNKNOWN count -- Result v0")
    return build_result(claims, generated_at=GENERATED_AT, view=view)


FIXTURES = {
    UNKNOWN_CLAIM_PATH: build_unknown_claim_fixture,
    UNKNOWN_COUNT_PATH: build_unknown_count_fixture,
}


def render(result: EvidenceResult) -> str:
    return json.dumps(result.to_dict(), indent=2, sort_keys=True) + "\n"


def main() -> None:
    for path, build in FIXTURES.items():
        path.write_text(render(build()))
        print(f"wrote {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
