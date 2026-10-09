# SPDX-License-Identifier: Apache-2.0
"""Build and seal a guard decision as an Agent Action Capsule.

Passive ``fyi`` events (degradation/recovery, policy-manifest activation,
conversation turns, ...) are a separate, guard-vocabulary-free concern --
see ``events/capsule.py``'s ``build_event_capsule`` (W3, 2026-09-01 split).

A decision capsule never asserts that the underlying action executed --
that is the downstream dispatcher's own capsule to emit, not this one's.
Per the -02 disposition spec (§ Disposition and the verdict reason-class):
``verdict_class`` is "legitimately absent for a clean executed verdict", so
``allow`` leaves it absent rather than claiming ``executed`` for something
this capsule did not itself do. ``deny`` uses the registry-seeded ``blocked``
token. ``escalate`` sets ``verdict_class`` to ``hitl_dispatched``: the guard
is the one routing the action to a human who has not yet acted, which is
what -02 §verdictclass defines ``hitl_dispatched`` as ("routed to a human
operator; awaiting resolution") -- ``deferred`` is a *human*-elected
postponement, a different, later state. ``disposition.decision`` is
``needs_input``, a seeded decision value, so the pair matches the donated
conformance vector ``vectors/capsule/pos-hitl-dispatched`` exactly.

Superseded 2026-10-04: the 2026-08-05 decision (D1) that also wrote
``hitl_dispatched`` into ``disposition.decision``: that put one token on
both axes, and ``hitl_dispatched`` is not a seeded decision value. Records
sealed under D1 are never rewritten; ``outcome_from_disposition`` reads
their legacy pairing as ``escalate``.

Money amounts have no field in the core -02 schema. ``asg_payload`` is a
single namespaced, non-spec payload extension (never a repurposed spec-
defined field, per the workspace's extension-field rule) carrying the one
numeric field the fold engine needs (``amount_minor``, integer minor units
-- floats are a fold determinism MUST-FAIL). It is committed into
``capsule_id`` like every other payload field, so it can't be tampered with
post-seal without invalidating the digest.

``asg_payload.manifest_digest`` (added for the policy-manifest task) is the
active policy manifest's own ``manifest_digest()`` (``capsule_ledger.policy``) at
decision time -- "which policy governed this decision" is checkable directly
off the capsule, never a separate, possibly-stale lookup. Omitted (not just
null) when no manifest is configured, same as every other optional
``asg_payload`` field here.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TypedDict

from agent_action_capsule import (
    DEFAULT_FORMAT_VERSION,
    AssuranceBlock,
    Capsule,
    Chain,
    ConstraintRecord,
    Disposition,
    compute_capsule_id,
    json_digest,
)
from agent_action_capsule.canonical import CANONICALIZATION_JCS

from .action import Action
from .signing import Signer

__all__ = [
    "ALLOW",
    "DENY",
    "ESCALATE",
    "ConstraintOutcome",
    "NotApplicableEvidence",
    "build_decision_capsule",
    "not_applicable_evidence",
    "outcome_from_disposition",
]

ALLOW = "allow"
DENY = "deny"
ESCALATE = "escalate"

# Disposition mapping (see module docstring). `escalate` -> decision
# `needs_input` with verdict_class `hitl_dispatched`, the donated vector's pair.
_DISPOSITION_BY_OUTCOME = {
    ALLOW: {"decision": "accept", "verdict_class": None},
    DENY: {"decision": "reject", "verdict_class": "blocked"},
    ESCALATE: {"decision": "needs_input", "verdict_class": "hitl_dispatched"},
}

# Reader side: every decision value this engine has ever sealed. The legacy
# `hitl_dispatched` decision was written before the change above and still
# reads as an escalation.
_OUTCOME_BY_DECISION = {"accept": ALLOW, "reject": DENY, "needs_input": ESCALATE}
_LEGACY_ESCALATE_DECISION = "hitl_dispatched"


# `disposition` is the raw JSON object of a sealed capsule, possibly one this
# engine did not write; its keys are the capsule spec's and are read here.
def outcome_from_disposition(disposition: dict) -> str:
    """allow | deny | escalate for a sealed decision capsule's disposition,
    including records sealed with the legacy escalate pairing (decision and
    verdict_class both ``hitl_dispatched``)."""
    decision = disposition.get("decision")
    if decision in _OUTCOME_BY_DECISION:
        return _OUTCOME_BY_DECISION[decision]
    if decision == _LEGACY_ESCALATE_DECISION and disposition.get("verdict_class") == _LEGACY_ESCALATE_DECISION:
        return ESCALATE
    raise ValueError(f"no guard outcome for disposition.decision {decision!r}")


@dataclass(frozen=True)
class ConstraintOutcome:
    """One check's result, on its way to becoming a ``ConstraintRecord``.

    ``evidence`` is a structured, private reason object (constraint id,
    threshold, observed value -- never free prose); it is digested into
    ``evidence_digest`` and never stored raw on the capsule. ``reason`` is a
    human-readable string returned to the caller for logging/CLI display;
    it never reaches the capsule at all.
    """

    id: str
    result: str  # "pass" | "fail" | "n/a"
    reason: str | None = None
    evidence: dict | None = None
    severity: str | None = "blocking"
    blocking: bool | None = None
    check_type: str | None = "policy"
    method: str | None = None

    def __post_init__(self) -> None:
        # An n/a with no evidence seals with no evidence_digest, so every n/a
        # cause would produce the same constraint record and a reader could
        # not tell "this rule did not apply" from "this rule applied and
        # could not be evaluated". Refuse to build one.
        if self.result == "n/a" and self.evidence is None:
            raise ValueError(
                f"constraint {self.id!r}: result 'n/a' requires an evidence object "
                "(see not_applicable_evidence)"
            )


class NotApplicableEvidence(TypedDict):
    constraint_id: str
    in_scope: bool
    missing_field: str | None


def not_applicable_evidence(
    constraint_id: str, *, in_scope: bool, missing_field: str | None = None
) -> NotApplicableEvidence:
    """The evidence object every ``n/a`` constraint carries: facts only.

    ``in_scope`` says whether the rule applied to this action at all (e.g. a
    cap is configured for its action class). ``missing_field`` names the
    normalized action field that was absent when an in-scope rule could not
    be evaluated, and is ``None`` otherwise. The object is small, canonical
    and holds no private data, so anyone can recompute the candidate digests
    and tell the cases apart from ``evidence_digest`` alone.
    """
    if not in_scope and missing_field is not None:
        raise ValueError("missing_field is only meaningful for an in-scope n/a")
    return NotApplicableEvidence(constraint_id=constraint_id, in_scope=in_scope, missing_field=missing_field)


def _to_constraint_record(outcome: ConstraintOutcome) -> ConstraintRecord:
    evidence_digest = json_digest(outcome.evidence) if outcome.evidence is not None else None
    return ConstraintRecord(
        id=outcome.id,
        result=outcome.result,
        severity=outcome.severity,
        blocking=outcome.blocking,
        check_type=outcome.check_type,
        method=outcome.method,
        evidence_digest=evidence_digest,
    )


def _payload_extension(action: Action, checkpoint: dict, manifest_digest: str | None) -> dict:
    ext: dict = {"checkpoint": checkpoint}
    if action.amount_minor is not None:
        ext["amount_minor"] = action.amount_minor
    if action.currency is not None:
        ext["currency"] = action.currency
    if action.target is not None:
        ext["target"] = action.target
    if action.action_class is not None:
        ext["action_class"] = action.action_class
    if action.rail is not None:
        ext["rail"] = action.rail
    if action.counterparty_account_ref is not None:
        ext["counterparty_account_ref"] = action.counterparty_account_ref
    if action.recurrence is not None:
        ext["recurrence"] = action.recurrence
    # Optional scalars sealed under their own name when set, so a record
    # without them keeps its prior bytes.
    scalars = (
        ("recipient_role", action.recipient_role),
        ("refundable", action.refundable),
        ("material_fields_changed", action.material_fields_changed),
        ("material_fields_basis", action.material_fields_basis),
        ("offer_fields_changed", action.offer_fields_changed),
        ("offer_fields_basis", action.offer_fields_basis),
        ("channel", action.channel),
        ("first_contact_channel", action.first_contact_channel),
        ("upfront_amount_minor", action.upfront_amount_minor),
        ("task_authority_ref", action.task_authority_ref),
        ("representation_class", action.representation_class),
        ("authorized_by", action.authorized_by),
        ("proposal_at", action.proposal_at),
        ("item_ref", action.item_ref),
        ("returned_minor", action.returned_minor),
        ("reverses_ref", action.reverses_ref),
    )
    for name, value in scalars:
        if value is not None:
            ext[name] = value
    if action.taxonomy_version is not None:
        # Written only when set, so existing records keep their bytes; read
        # back with ``classes.record_taxonomy_version``.
        ext["taxonomy_version"] = action.taxonomy_version
    if manifest_digest is not None:
        ext["manifest_digest"] = manifest_digest
    return ext


def build_decision_capsule(
    *,
    action: Action,
    outcome: str,
    constraints: Sequence[ConstraintOutcome],
    signer: Signer,
    checkpoint: dict,
    reason: dict | None = None,
    chain_parent: str | None = None,
    chain_relation: str | None = None,
    manifest_digest: str | None = None,
) -> dict:
    """Build, sign, and seal a decision capsule. Requires a live ``signer``
    -- callers MUST NOT call this when the signing key is unavailable
    (gating doc §1: "an unsigned record is not a record"); that fail-closed
    gate lives in the engine, one layer up.
    """
    if outcome not in _DISPOSITION_BY_OUTCOME:
        raise ValueError(f"unknown outcome {outcome!r}")

    spec = _DISPOSITION_BY_OUTCOME[outcome]
    reason_digest = json_digest(reason) if reason is not None else None
    disposition = Disposition(
        decision=spec["decision"],
        approver="policy",
        human_disposed=False,
        verdict_class=spec["verdict_class"],
        reason_digest=reason_digest,
    )

    chain = Chain(parent_capsule_id=chain_parent, relation=chain_relation) if chain_parent else None

    capsule_obj = Capsule(
        spec_version="draft-mih-scitt-agent-action-capsule-02",
        format_version=DEFAULT_FORMAT_VERSION,
        canonicalization_id=CANONICALIZATION_JCS,
        action_id=action.resolved_action_id(),
        action_type=action.action_type,
        operator=action.operator,
        developer=action.developer,
        timestamp=action.resolved_timestamp(),
        assurance=AssuranceBlock(
            attestation_mode="self_attested",
            effect_mode="not_applicable",  # the guard never itself dispatches an effect
            ledger_mode="chained" if chain is not None else "standalone",
        ),
        disposition=disposition,
        chain=chain,
        constraints=tuple(_to_constraint_record(c) for c in constraints),
    )

    body = capsule_obj.to_dict()
    body["asg_payload"] = _payload_extension(action, checkpoint, manifest_digest)

    # Sign the pre-signature canonical body, then commit the signature into
    # the body too: capsule_id ends up covering the signature value as well
    # as every other field, so a tampered signature is caught the same way
    # a tampered amount would be -- by digest_mismatch on recompute, not by
    # a separate signature-verification step this v0 doesn't have.
    presig_digest = json_digest(body)
    body["asg_signature"] = {
        "key_id": signer.key_id,
        "alg": signer.algorithm,
        "sig": signer.sign(presig_digest),
    }

    capsule_id = compute_capsule_id(body)
    sealed = {"spec_version": body["spec_version"], "format_version": body["format_version"], "capsule_id": capsule_id}
    for k, v in body.items():
        if k not in sealed:
            sealed[k] = v
    return sealed
