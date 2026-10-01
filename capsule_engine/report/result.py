# SPDX-License-Identifier: Apache-2.0
"""Evidence Result v0 emitter -- ``report/`` -> ``result`` projection.

The wire shape and every closed vocabulary below mirror
``agent-action-capsule``'s ``spec/evidence-result-v0.md`` /
``schemas/evidence-result-v0.json`` field-for-field -- that document governs,
this module is the encoding. A drift between the two is a defect here, never
a second legitimate spelling. At the time this module was written, that
schema had landed on an ``agent-action-capsule`` branch (ruled, no longer
DRAFT) but was not yet on its main branch -- not yet importable from the ``agent-action-capsule``
dependency this repo already pins -- so ``schemas/vendor/evidence-result-v0.
json`` carries a vendored copy (see ``schemas/vendor/README.md`` for its
provenance and the drop-vendoring-once-shipped note).

**Claims never self-declare.** Exactly like ``EvidenceContract`` upstream
(``schemas/evidence-contract-v0.json``'s ``$comment``: "the evidence record
never self-declares that it satisfies a requirement"), nothing in this module
computes sufficiency or verdict from data it also emits as evidence -- both
are supplied by the caller, who is expected to have derived them from real
Evidence Contract evaluation (a fold's own verdict, or a capsule-judge
verdict record). This module's job is projection and validation: build a
schema-shaped ``Claim``/``EvidenceResult``, refuse to construct one that
violates the ruled vocabulary rules, and compute the aggregate (coverage +
buckets) FROM the claims actually built -- never accept a caller-supplied
aggregate. See ``result_from_folds.py`` for the adapters that produce claims
from this repo's own deterministic folds.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import jsonschema

from .errors import (
    BUCKET_CLAIM_MISMATCH,
    DISCLOSURE_NOT_LEGAL_FOR_STATUS,
    DUPLICATE_CLAIM_ID,
    INVALID_DISCLOSED_STATUS,
    INVALID_EVIDENCE_STATUS,
    INVALID_GRADE,
    INVALID_HEX_DIGEST,
    INVALID_SUFFICIENCY,
    INVALID_TIER,
    INVALID_VERDICT,
    SUFFICIENCY_VERDICT_MISMATCH,
    ResultError,
)

if TYPE_CHECKING:
    from .coverage import CoverageReport

__all__ = [
    "RESULT_VERSION",
    "TIER_VALUES",
    "GRADE_VALUES",
    "SUFFICIENCY_VALUES",
    "VERDICT_VALUES",
    "EVIDENCE_STATUS_VALUES",
    "DISCLOSED_STATUS_VALUES",
    "DigestRef",
    "ProofRef",
    "DisclosureCarrier",
    "AnalysisCarrier",
    "StoryCarrier",
    "Presentation",
    "Claim",
    "Coverage",
    "Buckets",
    "Aggregate",
    "View",
    "EvidenceResult",
    "build_result",
    "SCHEMA_PATH",
    "load_schema",
    "validate_against_schema",
    "verify_result",
]

RESULT_VERSION = "evidence-result-v0"

# Closed vocabularies -- spec/evidence-result-v0.md sections 1, 2, 4, 6.
TIER_VALUES = frozenset({"recomputed", "judged"})
GRADE_VALUES = frozenset({"self-attested", "witnessed", "countersigned"})
SUFFICIENCY_VALUES = frozenset({"SATISFIED", "GAP", "INSUFFICIENT", "UNKNOWN"})
VERDICT_VALUES = frozenset({"met", "not_met", "not_evaluable"})
EVIDENCE_STATUS_VALUES = frozenset(
    {"SATISFIED", "INSUFFICIENT", "NOT_FOUND", "NOT_COMMITTED", "WITHHELD", "CONTRADICTED", "NOT_APPLICABLE", "UNKNOWN"}
)
# EvidenceStatus minus the two the disclosure policy reserves for analysis/story (spec section 2).
DISCLOSED_STATUS_VALUES = EVIDENCE_STATUS_VALUES - {"WITHHELD", "NOT_COMMITTED"}

_HEX_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schemas" / "vendor" / "evidence-result-v0.json"


def load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text())


def _check_hex_digest(digest: str) -> None:
    if not _HEX_DIGEST_RE.match(digest):
        raise ResultError(INVALID_HEX_DIGEST, f"{digest!r} is not 64 lowercase hex characters")


@dataclass(frozen=True)
class DigestRef:
    """Evidence-by-digest-only ref (spec section 5) -- never inline bytes."""

    digest: str
    digest_alg: str = "SHA-256"

    def __post_init__(self) -> None:
        if self.digest_alg != "SHA-256":
            raise ResultError(INVALID_HEX_DIGEST, f"digest_alg must be 'SHA-256', got {self.digest_alg!r}")
        _check_hex_digest(self.digest)

    def to_dict(self) -> dict:
        return {"digest_alg": self.digest_alg, "digest": self.digest}


@dataclass(frozen=True)
class ProofRef:
    """An inclusion-proof/receipt ref, by digest only (spec section 5)."""

    kind: str  # "inclusion_proof" | "receipt"
    digest: str
    digest_alg: str = "SHA-256"

    def __post_init__(self) -> None:
        if self.kind not in ("inclusion_proof", "receipt"):
            raise ResultError(INVALID_HEX_DIGEST, f"proof kind must be inclusion_proof|receipt, got {self.kind!r}")
        if self.digest_alg != "SHA-256":
            raise ResultError(INVALID_HEX_DIGEST, f"digest_alg must be 'SHA-256', got {self.digest_alg!r}")
        _check_hex_digest(self.digest)

    def to_dict(self) -> dict:
        return {"kind": self.kind, "digest_alg": self.digest_alg, "digest": self.digest}


@dataclass(frozen=True)
class DisclosureCarrier:
    """spec section 2/6 -- the claim's evidence, by digest, plus the status
    that licensed showing it. Legal ONLY when ``status`` is not WITHHELD or
    NOT_COMMITTED (the disclosure gate) -- enforced in ``__post_init__``, not
    left to a caller to remember."""

    status: str
    evidence: tuple[DigestRef, ...] = ()
    kind: str = field(default="disclosure", init=False)

    def __post_init__(self) -> None:
        if self.status not in DISCLOSED_STATUS_VALUES:
            raise ResultError(
                INVALID_DISCLOSED_STATUS,
                f"disclosure carrier status must be one of {sorted(DISCLOSED_STATUS_VALUES)}, got {self.status!r}",
            )

    def to_dict(self) -> dict:
        return {"kind": "disclosure", "status": self.status, "evidence": [e.to_dict() for e in self.evidence]}


@dataclass(frozen=True)
class AnalysisCarrier:
    """spec section 2/6 -- a derived characterization, never a quote of the
    underlying payload. Accepts any of the eight EvidenceStatus values."""

    status: str
    summary: str
    kind: str = field(default="analysis", init=False)

    def __post_init__(self) -> None:
        if self.status not in EVIDENCE_STATUS_VALUES:
            raise ResultError(INVALID_EVIDENCE_STATUS, f"status must be one of {sorted(EVIDENCE_STATUS_VALUES)}, got {self.status!r}")
        if not self.summary:
            raise ResultError(INVALID_EVIDENCE_STATUS, "analysis carrier summary must be non-empty")

    def to_dict(self) -> dict:
        return {"kind": "analysis", "status": self.status, "summary": self.summary}


@dataclass(frozen=True)
class StoryCarrier:
    """spec section 2/6 -- narrative-only, the weakest carrier."""

    status: str
    narrative: str
    kind: str = field(default="story", init=False)

    def __post_init__(self) -> None:
        if self.status not in EVIDENCE_STATUS_VALUES:
            raise ResultError(INVALID_EVIDENCE_STATUS, f"status must be one of {sorted(EVIDENCE_STATUS_VALUES)}, got {self.status!r}")
        if not self.narrative:
            raise ResultError(INVALID_EVIDENCE_STATUS, "story carrier narrative must be non-empty")

    def to_dict(self) -> dict:
        return {"kind": "story", "status": self.status, "narrative": self.narrative}


Presentation = DisclosureCarrier | AnalysisCarrier | StoryCarrier


@dataclass(frozen=True)
class Claim:
    """spec section 4 -- one requirement of one contract version, evaluated
    once. ``sufficiency``/``verdict`` are never derived here (see module
    docstring) -- only checked for internal consistency (spec section 1's
    rule) at construction time, so a malformed claim never gets built in the
    first place: "an untiered claim is a bug here, not a rendering problem."

    ``contract_ref`` is the compact ``<contract_id>@<version>`` shape
    (evidence-plan-ir-v0.md section 2's convention, mirrored by
    evidence-result-v0.md section 1) -- not the two separate
    ``contract_id``/``contract_version`` fields this task's own inbox text
    names; the frozen schema uses ``contract_ref`` (the sibling-flagged
    rename already applied before the schema froze), so this module follows
    the shipped schema, not the stale inbox wording.
    """

    id: str
    contract_ref: str
    requirement_ref: str
    tier: str
    grade: str
    sufficiency: str
    verdict: str
    evidence: tuple[DigestRef, ...]
    proofs: tuple[ProofRef, ...]
    presentation: Presentation

    def __post_init__(self) -> None:
        if not self.id:
            raise ResultError(INVALID_TIER, "claim id must be non-empty")
        if "@" not in self.contract_ref or self.contract_ref.startswith("@") or self.contract_ref.endswith("@"):
            raise ResultError(INVALID_TIER, f"contract_ref must be <contract_id>@<version>, got {self.contract_ref!r}")
        if not self.requirement_ref:
            raise ResultError(INVALID_TIER, "requirement_ref must be non-empty")
        if self.tier not in TIER_VALUES:
            raise ResultError(INVALID_TIER, f"tier must be one of {sorted(TIER_VALUES)}, got {self.tier!r}")
        if self.grade not in GRADE_VALUES:
            raise ResultError(INVALID_GRADE, f"grade must be one of {sorted(GRADE_VALUES)}, got {self.grade!r}")
        if self.sufficiency not in SUFFICIENCY_VALUES:
            raise ResultError(INVALID_SUFFICIENCY, f"sufficiency must be one of {sorted(SUFFICIENCY_VALUES)}, got {self.sufficiency!r}")
        if self.verdict not in VERDICT_VALUES:
            raise ResultError(INVALID_VERDICT, f"verdict must be one of {sorted(VERDICT_VALUES)}, got {self.verdict!r}")
        # spec section 1's rule, restated normatively there from contract v3 section 8:
        # verdict is met/not_met ONLY when sufficiency == SATISFIED; otherwise not_evaluable.
        if self.sufficiency == "SATISFIED":
            if self.verdict not in ("met", "not_met"):
                raise ResultError(
                    SUFFICIENCY_VERDICT_MISMATCH,
                    f"sufficiency SATISFIED requires verdict met|not_met, got {self.verdict!r}",
                )
        elif self.verdict != "not_evaluable":
            raise ResultError(
                SUFFICIENCY_VERDICT_MISMATCH,
                f"sufficiency {self.sufficiency!r} (not SATISFIED) requires verdict not_evaluable, got {self.verdict!r}",
            )
        # spec section 2's gate: disclosure is not a legal carrier when the
        # carrier's own status is WITHHELD/NOT_COMMITTED -- DisclosureCarrier
        # itself already refuses that status, so this is a second, cheap
        # belt-and-suspenders check against a carrier built via replace()/
        # object.__new__ bypassing __post_init__.
        if isinstance(self.presentation, DisclosureCarrier) and self.presentation.status not in DISCLOSED_STATUS_VALUES:
            raise ResultError(
                DISCLOSURE_NOT_LEGAL_FOR_STATUS,
                f"disclosure carrier illegal for status {self.presentation.status!r}",
            )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "contract_ref": self.contract_ref,
            "requirement_ref": self.requirement_ref,
            "tier": self.tier,
            "grade": self.grade,
            "sufficiency": self.sufficiency,
            "verdict": self.verdict,
            "evidence": [e.to_dict() for e in self.evidence],
            "proofs": [p.to_dict() for p in self.proofs],
            "presentation": self.presentation.to_dict(),
        }


@dataclass(frozen=True)
class Coverage:
    """spec section 3/7 -- mandatory, computed. Never constructed with
    values the caller invented; see ``build_result``."""

    evaluated_population: int
    excluded_not_applicable: int
    unknown_count: int

    def __post_init__(self) -> None:
        for name, value in (
            ("evaluated_population", self.evaluated_population),
            ("excluded_not_applicable", self.excluded_not_applicable),
            ("unknown_count", self.unknown_count),
        ):
            if value < 0:
                raise ResultError(INVALID_SUFFICIENCY, f"coverage.{name} must be >= 0, got {value}")

    def to_dict(self) -> dict:
        return {
            "evaluated_population": self.evaluated_population,
            "excluded_not_applicable": self.excluded_not_applicable,
            "unknown_count": self.unknown_count,
        }


@dataclass(frozen=True)
class Buckets:
    """spec section 3/7 -- always three keys, each an array. Never
    constructed by a caller; see ``build_result``."""

    met: tuple[str, ...]
    not_met: tuple[str, ...]
    not_evaluable: tuple[str, ...]

    def to_dict(self) -> dict:
        return {"met": list(self.met), "not_met": list(self.not_met), "not_evaluable": list(self.not_evaluable)}


@dataclass(frozen=True)
class Aggregate:
    coverage: Coverage
    buckets: Buckets

    def to_dict(self) -> dict:
        return {"coverage": self.coverage.to_dict(), "buckets": self.buckets.to_dict()}


@dataclass(frozen=True)
class View:
    """spec section 8 -- presentation hints only, never data."""

    producer_name: str | None = None
    logo_data_url: str | None = None
    title: str | None = None
    spec_version: str = field(default="presentation/v1", init=False)

    def to_dict(self) -> dict:
        out: dict[str, Any] = {"spec_version": "presentation/v1"}
        if self.producer_name is not None:
            out["producer_name"] = self.producer_name
        if self.logo_data_url is not None:
            out["logo_data_url"] = self.logo_data_url
        if self.title is not None:
            out["title"] = self.title
        return out


@dataclass(frozen=True)
class EvidenceResult:
    generated_at: str
    claims: tuple[Claim, ...]
    aggregate: Aggregate
    view: View | None = None
    coverage_report: CoverageReport | None = None
    result_version: str = field(default=RESULT_VERSION, init=False)

    def to_dict(self) -> dict:
        out: dict[str, Any] = {
            "result_version": RESULT_VERSION,
            "generated_at": self.generated_at,
            "claims": [c.to_dict() for c in self.claims],
            "aggregate": self.aggregate.to_dict(),
        }
        if self.view is not None:
            out["view"] = self.view.to_dict()
        if self.coverage_report is not None:
            out["coverage_report"] = self.coverage_report.to_dict()
        return out


def build_result(
    claims: list[Claim],
    *,
    generated_at: str,
    excluded_not_applicable: int = 0,
    view: View | None = None,
    coverage_report: CoverageReport | None = None,
) -> EvidenceResult:
    """The projection's aggregate step (spec section 3): coverage and
    buckets are COMPUTED from ``claims``, never templated or accepted
    pre-summed from a caller.

    ``excluded_not_applicable`` is the one count that cannot be derived from
    ``claims`` -- a requirement excluded as NOT_APPLICABLE never becomes a
    claim at all (spec section 3), so the caller (typically a
    ``result_from_folds`` adapter, which sees the excluded requirements
    directly) supplies the count of what it declined to emit as claims.
    Every other coverage/bucket field is counted off ``claims`` itself.

    ``coverage_report`` is the optional per-requirement section built by
    ``coverage.build_coverage_report`` over the same claims; it is carried
    as built, and ``verify_result`` re-checks it against the claims.
    """
    if excluded_not_applicable < 0:
        raise ResultError(INVALID_SUFFICIENCY, f"excluded_not_applicable must be >= 0, got {excluded_not_applicable}")

    met: list[str] = []
    not_met: list[str] = []
    not_evaluable: list[str] = []
    unknown_count = 0
    seen_ids: set[str] = set()
    for claim in claims:
        if claim.id in seen_ids:
            raise ResultError(DUPLICATE_CLAIM_ID, f"claim id {claim.id!r} appears more than once")
        seen_ids.add(claim.id)
        if claim.verdict == "met":
            met.append(claim.id)
        elif claim.verdict == "not_met":
            not_met.append(claim.id)
        else:
            not_evaluable.append(claim.id)
        if claim.sufficiency == "UNKNOWN":
            unknown_count += 1

    coverage = Coverage(
        evaluated_population=len(claims),
        excluded_not_applicable=excluded_not_applicable,
        unknown_count=unknown_count,
    )
    buckets = Buckets(met=tuple(met), not_met=tuple(not_met), not_evaluable=tuple(not_evaluable))
    return EvidenceResult(
        generated_at=generated_at,
        claims=tuple(claims),
        aggregate=Aggregate(coverage=coverage, buckets=buckets),
        view=view,
        coverage_report=coverage_report,
    )


def validate_against_schema(doc: dict[str, Any]) -> None:
    """Validate a Result document against the vendored public JSON Schema
    (item 4: "Validate every emitted Result against the public schema in
    CI"). Raises ``jsonschema.exceptions.ValidationError`` on the first
    violation."""
    schema = load_schema()
    jsonschema.Draft202012Validator(schema).validate(doc)


def verify_result(doc: dict[str, Any]) -> None:
    """The cross-element checks spec/evidence-result-v0.md section 4 names
    as "normative, not schema-enforced in v0": claim ``id`` uniqueness, and
    every ``aggregate.buckets`` entry naming a claim that actually exists
    with the matching verdict -- plus, when the document carries a
    ``coverage_report``, ``coverage.verify_coverage_report``'s checks
    against the same claims. Raises ``ResultError`` on the first
    violation. Callers wanting full conformance run this AND
    ``validate_against_schema`` -- neither alone is the whole check."""
    claims_by_id: dict[str, dict] = {}
    for claim in doc.get("claims", []):
        claim_id = claim.get("id")
        if claim_id in claims_by_id:
            raise ResultError(DUPLICATE_CLAIM_ID, f"claim id {claim_id!r} appears more than once")
        claims_by_id[claim_id] = claim

    buckets = doc.get("aggregate", {}).get("buckets", {})
    for verdict, ids in (("met", buckets.get("met", [])), ("not_met", buckets.get("not_met", [])), ("not_evaluable", buckets.get("not_evaluable", []))):
        for claim_id in ids:
            claim = claims_by_id.get(claim_id)
            if claim is None:
                raise ResultError(BUCKET_CLAIM_MISMATCH, f"bucket {verdict!r} names claim id {claim_id!r}, which has no claim")
            if claim.get("verdict") != verdict:
                raise ResultError(
                    BUCKET_CLAIM_MISMATCH,
                    f"claim {claim_id!r} is in bucket {verdict!r} but its own verdict is {claim.get('verdict')!r}",
                )
    bucketed_ids = {cid for ids in buckets.values() for cid in ids}
    for claim_id, claim in claims_by_id.items():
        if claim_id not in bucketed_ids:
            raise ResultError(BUCKET_CLAIM_MISMATCH, f"claim {claim_id!r} (verdict {claim.get('verdict')!r}) is in no bucket")

    if "coverage_report" in doc:
        from .coverage import verify_coverage_report

        verify_coverage_report(doc["coverage_report"], claims_by_id)
