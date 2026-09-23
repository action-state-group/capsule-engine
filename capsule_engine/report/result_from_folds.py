# SPDX-License-Identifier: Apache-2.0
"""Adapters: real deterministic-fold outputs -> Result v0 ``Claim``s.

Every adapter here reads ``tier``/``grade``/``evidence``/``proofs`` off REAL
objects the fold call already produced -- ``capsule_emit``'s own
``CheckpointRecord.grade()`` (the two-rung ladder position: ``self-attested``
until a real, non-stub, offline-verified witness stamp lands, then
``witnessed`` -- see that method's docstring) for ``grade``, and
``CheckpointRecord.digest()`` / ``WitnessRecord.entry_hash`` for
``evidence``/``proofs``. Nothing here asserts a grade or invents a digest;
that is the module's whole point -- "grade read from the bundle's
witness/countersign state, never asserted"
([batch4-result-emission-from-engine]).

``tier`` is always ``"recomputed"`` in this module: every fold in
``capsule_engine/folds/`` is a deterministic recomputation over sealed
records (this repo's own report/build.py docstring: "nothing in this module
invents a number"), never a semantic judgment. A ``"judged"`` claim's source
is a ``capsule-judge`` verdict record -- a different repo/subsystem this one
does not import (lane boundary: "planner/executor internals stay in
action-state-engine and are not imported here") -- so no adapter in this
module ever emits ``tier="judged"``; ``result.py``'s ``Claim`` supports it
for a caller that has one.
"""
from __future__ import annotations

from capsule_emit.chain_segment import ChainSegment

from ..folds.record_type_coverage import RecordTypeCoverageResult
from ..folds.retention_continuity import RetentionContinuityResult
from .result import Claim, DigestRef, DisclosureCarrier, ProofRef

__all__ = ["claim_from_retention_continuity", "is_excluded_not_applicable"]

# spec/evidence-result-v0.md section 1's rule, restated: sufficiency SATISFIED
# only when the fold actually reached a met/not_met judgment; INSUFFICIENT
# otherwise (this fold's own "insufficient_evidence" word, spelled onto the
# Evidence Contract v3 section 5.2 vocabulary it mirrors).
_RETENTION_VERDICT_MAP = {
    "met": ("SATISFIED", "met", "SATISFIED"),
    "not_met": ("SATISFIED", "not_met", "SATISFIED"),
    "insufficient_evidence": ("INSUFFICIENT", "not_evaluable", "INSUFFICIENT"),
}


def claim_from_retention_continuity(
    result: RetentionContinuityResult,
    segment: ChainSegment,
    *,
    claim_id: str,
    contract_ref: str,
    requirement_ref: str,
) -> Claim:
    """Project one ``evaluate_retention_continuity`` call into a Result v0
    claim. ``segment`` must be the SAME segment ``result`` was computed
    over -- this function reads the segment's own last checkpoint for grade
    and evidence/proof digests, it does not recompute the fold.
    """
    if result.verdict not in _RETENTION_VERDICT_MAP:
        raise ValueError(f"unrecognized retention-continuity verdict {result.verdict!r}")
    sufficiency, verdict, disclosure_status = _RETENTION_VERDICT_MAP[result.verdict]

    checkpoint = segment.checkpoint
    grade = checkpoint.grade().value
    evidence = (DigestRef(digest=checkpoint.digest()),)
    proofs = tuple(
        ProofRef(kind="receipt", digest=w.entry_hash) for w in checkpoint.witnesses
    ) or (ProofRef(kind="inclusion_proof", digest=checkpoint.digest()),)

    return Claim(
        id=claim_id,
        contract_ref=contract_ref,
        requirement_ref=requirement_ref,
        tier="recomputed",
        grade=grade,
        sufficiency=sufficiency,
        verdict=verdict,
        evidence=evidence,
        proofs=proofs,
        presentation=DisclosureCarrier(status=disclosure_status, evidence=evidence),
    )


def is_excluded_not_applicable(result: RecordTypeCoverageResult) -> bool:
    """spec section 3: a requirement excluded as NOT_APPLICABLE never
    becomes a claim at all -- it only contributes to
    ``aggregate.coverage.excluded_not_applicable`` (``build_result``'s
    ``excluded_not_applicable`` count). This fold's own ``verdict`` already
    carries that exclusion (an empty ``registered_kinds`` declaration, per
    ``record_type_coverage.py``'s docstring: "nothing was declared to check
    coverage against"), so this is a pure read, not a second judgment.
    """
    return result.verdict == "not_applicable"
