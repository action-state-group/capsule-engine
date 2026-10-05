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
witness/countersign state, never asserted".

``tier`` is always ``"recomputed"`` in this module: every fold in
``capsule_engine/folds/`` is a deterministic recomputation over sealed
records (this repo's own report/build.py docstring: "nothing in this module
invents a number"), never a semantic judgment. A ``"judged"`` claim's source
is a ``capsule-judge`` verdict record -- a different subsystem this repo
does not import -- so no adapter in this module ever emits
``tier="judged"``; ``result.py``'s ``Claim`` supports it for a caller that
has one.
"""
from __future__ import annotations

from dataclasses import dataclass

from agent_action_capsule import json_digest
from capsule_emit.chain_segment import ChainSegment

from ..folds.record_type_coverage import RecordTypeCoverageResult
from ..folds.retention_continuity import RetentionContinuityResult
from .result import Claim, DigestRef, DisclosureCarrier, ProofRef

__all__ = [
    "GuardProjection",
    "claim_from_guard_constraint",
    "claim_from_retention_continuity",
    "is_excluded_not_applicable",
    "project_guard_constraint",
]

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


# A guard constraint record's result (pass | fail | n/a) projected onto the
# registered (EvidenceStatus, Sufficiency, Verdict) triple, beside the fold
# map above. pass/fail are settled; an n/a is read through the facts object
# guards/capsule.py's not_applicable_evidence() digests into evidence_digest.
_GUARD_SETTLED = {
    "pass": ("SATISFIED", "SATISFIED", "met"),
    "fail": ("SATISFIED", "SATISFIED", "not_met"),
}


_GRADE_BY_ATTESTATION_MODE = {"self_attested": "self-attested"}


@dataclass(frozen=True)
class GuardProjection:
    """One guard constraint record, projected. ``excluded`` means the rule did
    not apply: the record contributes to ``excluded_not_applicable`` and never
    becomes a claim. ``recording_defect`` marks an n/a whose cause cannot be
    read off the record (population b); it is counted on its own and kept out
    of every rate."""

    evidence_status: str
    sufficiency: str | None
    verdict: str | None
    excluded: bool = False
    recording_defect: bool = False


def project_guard_constraint(record: dict, *, candidate_fields: frozenset[str]) -> GuardProjection:
    """Project one sealed guard constraint record (``capsule["constraints"][i]``).

    ``candidate_fields`` are the normalized action fields an in-scope n/a may
    name as missing; the record's ``evidence_digest`` is matched against the
    facts object for each, so nothing beyond the sealed record is read.

    | record                                  | status         | sufficiency | verdict       |
    |-----------------------------------------|----------------|-------------|---------------|
    | pass / fail                             | SATISFIED      | SATISFIED   | met / not_met |
    | n/a, in_scope false                     | NOT_APPLICABLE | (excluded, never a claim)   |
    | n/a, in_scope true, missing_field named | NOT_FOUND      | GAP         | not_evaluable |
    | n/a with no readable cause              | UNKNOWN        | UNKNOWN     | not_evaluable |
    """
    result = record["result"]
    if result in _GUARD_SETTLED:
        status, sufficiency, verdict = _GUARD_SETTLED[result]
        return GuardProjection(evidence_status=status, sufficiency=sufficiency, verdict=verdict)
    if result != "n/a":
        raise ValueError(f"unrecognized guard constraint result {result!r}")

    constraint_id = record["id"]
    digest = record.get("evidence_digest")
    if digest == json_digest({"constraint_id": constraint_id, "in_scope": False, "missing_field": None}):
        return GuardProjection(evidence_status="NOT_APPLICABLE", sufficiency=None, verdict=None, excluded=True)
    for field_name in sorted(candidate_fields):
        if digest == json_digest({"constraint_id": constraint_id, "in_scope": True, "missing_field": field_name}):
            # LOCAL CHOICE, declared here rather than derived: NOT_FOUND -> GAP.
            # The donated spec does not define NOT_FOUND's sufficiency.
            # agent-action-capsule spec/evidence-result-v0.md section 2
            # ("Disclosure policy", at origin/main 3dfc70b) collapses
            # WITHHELD and NOT_COMMITTED into GAP and names NOT_FOUND only as
            # the contrast case; it leaves NOT_FOUND's projection open. We
            # chose GAP: the claim is inside the evaluated population, in the
            # not_evaluable bucket, and NOT in unknown_count. Choosing UNKNOWN
            # instead would put this case in the published unknown_count,
            # which section 3 ("What the sponsor sees first") and section 7
            # ("Aggregate") define as the count of claims resolved UNKNOWN.
            return GuardProjection(evidence_status="NOT_FOUND", sufficiency="GAP", verdict="not_evaluable")
    # No digest, or one that matches no facts object: the record does not say
    # why the rule did not settle. This engine cannot emit one
    # (ConstraintOutcome refuses an n/a without evidence), so this is a
    # foreign or pre-fix record -- a recording defect, not a decline.
    return GuardProjection(
        evidence_status="UNKNOWN", sufficiency="UNKNOWN", verdict="not_evaluable", recording_defect=True
    )


def claim_from_guard_constraint(
    capsule: dict,
    record: dict,
    *,
    claim_id: str,
    contract_ref: str,
    requirement_ref: str,
    candidate_fields: frozenset[str],
) -> Claim | None:
    """A Result v0 claim for one constraint record of a sealed guard decision
    capsule, or ``None`` when the rule did not apply (the caller counts it in
    ``build_result(excluded_not_applicable=...)``). Tier ``recomputed``: the
    check is a deterministic predicate over the decision's recorded inputs.
    Grade is read off the capsule's own ``assurance.attestation_mode``; only
    ``self_attested`` maps today, and anything else is refused rather than
    guessed. Evidence is the constraint's own ``evidence_digest``
    when it has one; the proof is the decision capsule's ``capsule_id``."""
    projected = project_guard_constraint(record, candidate_fields=candidate_fields)
    if projected.excluded:
        return None
    attestation_mode = (capsule.get("assurance") or {}).get("attestation_mode")
    if attestation_mode not in _GRADE_BY_ATTESTATION_MODE:
        raise ValueError(f"no Result grade is defined for attestation_mode {attestation_mode!r}")
    evidence = (DigestRef(digest=record["evidence_digest"]),) if record.get("evidence_digest") else ()
    return Claim(
        id=claim_id,
        contract_ref=contract_ref,
        requirement_ref=requirement_ref,
        tier="recomputed",
        grade=_GRADE_BY_ATTESTATION_MODE[attestation_mode],
        sufficiency=projected.sufficiency,
        verdict=projected.verdict,
        evidence=evidence,
        proofs=(ProofRef(kind="inclusion_proof", digest=capsule["capsule_id"]),),
        presentation=DisclosureCarrier(status=projected.evidence_status, evidence=evidence),
    )
