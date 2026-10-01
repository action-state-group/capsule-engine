#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Regenerate ``tests/fixtures/evidence-result/coverage-report-result.json``
-- a synthetic Evidence Result carrying the per-requirement
``coverage_report`` section, for renderers to build against.

Synthetic only: the records are plain dicts with derived digests, no
signing and no clock, so the output is a pure function of this file and
``tests/test_report_coverage_fixture.py`` re-runs it and compares bytes.
The contract is ``examples/contracts/ai-act-human-oversight.json`` with one
change: ``req-human-role-3`` asks for independent producers, so the
fixture shows a correlated-only gap. Four sources are typed through a
source catalog; the others carry no ``epistemic_type``.

What the fixture shows, one requirement each:

- ``req-human-role-1`` -- SATISFIED: both sources present, one from a
  backfilled import, one contemporaneous. The two records share a developer
  token, so they count as one producer (no independence is asked).
- ``req-human-role-2`` -- NOT_FOUND: ``ui-explanation-capability-record``
  is missing; the remedy names the connector that would capture it.
- ``req-human-role-3`` -- INSUFFICIENT: all three sources present, but every
  record is a span from one exporter -- correlation, not corroboration -- and
  no connector is named for a second producer, so ``remedy`` is null.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from capsule_engine.report.coverage import Remedy, build_coverage_report
from capsule_engine.report.result import (
    AnalysisCarrier,
    Claim,
    DigestRef,
    DisclosureCarrier,
    ProofRef,
    View,
    build_result,
)

ROOT = Path(__file__).resolve().parent.parent
CONTRACT_PATH = ROOT / "examples" / "contracts" / "ai-act-human-oversight.json"
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "evidence-result" / "coverage-report-result.json"
GENERATED_AT = "2026-10-01T00:00:00Z"
OTEL_BLOCK_KEY = "org.agentactioncapsule.otel"

# Declared types for some sources; the rest stay untyped (no key).
SOURCE_CATALOG = {
    "role-assignment-record": "system_of_record_fact",
    "ui-explanation-capability-record": "system_of_record_fact",
    "review-events": "observed_event",
    "override-events": "human_report",
}

REMEDIES = {
    "ui-explanation-capability-record": Remedy(connector="system_of_record", raises_to="retrospectively_evidenced"),
    "review-events": Remedy(connector="mcp_proxy", raises_to="committed"),
}


def _hex(seed: str) -> str:
    return hashlib.sha256(seed.encode()).hexdigest()


def _record(seed: str, source: str, operator: str, developer: str) -> dict:
    return {"capsule_id": _hex(seed), "operator": operator, "developer": developer, "payload": {"source": source}}


def _span(seed: str, source: str) -> dict:
    record = _record(seed, source, "oo-operator", "oo-agent@v1")
    record["model_attestation"] = {
        "compute_attestation": {
            OTEL_BLOCK_KEY: {
                "trace_id": _hex("oo-trace"),
                "span_id": _hex(f"oo-span-{seed}"),
                "resource": {"service.name": "oo-agent"},
            }
        }
    }
    return record


def _backfilled(seed: str, source: str, operator: str, developer: str) -> dict:
    record = _record(seed, source, operator, developer)
    record["provenance_mode"] = {
        "mode": "backfilled",
        "source_ref": {"type": "source_record", "digest_alg": "sha256", "digest": _hex(f"src-{seed}")},
        "source_asserted_at": "2026-08-15T00:00:00Z",
        "import_batch": "oo-import-1",
        "imported_at": "2026-09-22T00:00:00Z",
    }
    return record


def contract() -> dict:
    doc = copy.deepcopy(json.loads(CONTRACT_PATH.read_text()))
    doc["requirements"][2]["evidence_requirements"]["independence"] = "independent-producers"
    return doc


def records() -> list[dict]:
    return [
        _backfilled("oo-r1", "role-assignment-record", "oo-hr", "sor@v1"),
        _record("oo-r2", "authority-competence-record", "oo-training", "sor@v1"),
        _record("oo-r3", "review-interaction-records", "oo-review", "ui@v2"),
        _span("oo-s1", "review-events"),
        _span("oo-s2", "review-events"),
        _span("oo-s3", "override-events"),
        _span("oo-s4", "exception-events"),
    ]


def _source_of(record: dict) -> str | None:
    return record.get("payload", {}).get("source")


def _claims(contract_ref: str) -> list[Claim]:
    def claim(claim_id, req, sufficiency, verdict, presentation):
        ev = (DigestRef(digest=_hex(f"claim-{claim_id}")),)
        return Claim(
            id=claim_id,
            contract_ref=contract_ref,
            requirement_ref=req,
            tier="recomputed",
            grade="self-attested",
            sufficiency=sufficiency,
            verdict=verdict,
            evidence=ev,
            proofs=(ProofRef(kind="inclusion_proof", digest=_hex(f"proof-{claim_id}")),),
            presentation=presentation(ev),
        )

    return [
        claim("claim-1", "req-human-role-1", "SATISFIED", "met", lambda ev: DisclosureCarrier(status="SATISFIED", evidence=ev)),
        claim(
            "claim-2",
            "req-human-role-2",
            "GAP",
            "not_evaluable",
            lambda ev: AnalysisCarrier(status="NOT_FOUND", summary="no UI explanation capability record in the period"),
        ),
        claim(
            "claim-3",
            "req-human-role-3",
            "INSUFFICIENT",
            "not_evaluable",
            lambda ev: AnalysisCarrier(status="INSUFFICIENT", summary="review evidence comes from one producer only"),
        ),
    ]


def build_coverage_fixture() -> dict:
    doc = contract()
    contract_ref = f"{doc['id']}@{doc['version']}"
    claims = _claims(contract_ref)
    coverage = build_coverage_report(
        doc, records(), source_of=_source_of, claims=claims, remedies=REMEDIES, source_catalog=SOURCE_CATALOG
    )
    view = View(producer_name="OO", title="Human oversight evidence coverage (synthetic)")
    return build_result(claims, generated_at=GENERATED_AT, view=view, coverage_report=coverage).to_dict()


def render(doc: dict) -> str:
    return json.dumps(doc, indent=2, sort_keys=True) + "\n"


def main() -> None:
    FIXTURE_PATH.write_text(render(build_coverage_fixture()))
    print(f"wrote {FIXTURE_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
