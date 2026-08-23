# SPDX-License-Identifier: Apache-2.0
"""Build and seal a guard decision as an Agent Action Capsule.

A decision capsule never asserts that the underlying action executed --
that is the downstream dispatcher's own capsule to emit, not this one's.
Per the -02 disposition spec (§ Disposition and the verdict reason-class):
``verdict_class`` is "legitimately absent for a clean executed verdict", so
``allow`` leaves it absent rather than claiming ``executed`` for something
this capsule did not itself do. ``deny`` uses the registry-seeded ``blocked``
token. ``escalate`` uses ``hitl_dispatched``: the guard is the one routing the action to a human who has not yet
acted, which is what -02 §verdictclass defines ``hitl_dispatched`` as
("routed to a human operator; awaiting resolution") -- ``deferred`` is a
*human*-elected postponement, a different, later state. ``disposition.decision``
also takes ``hitl_dispatched`` under the same decision; it sits outside the
seeded ``accept``/``reject``/``needs_input``/``deferred`` set, but an
unregistered ``decision`` value is informational to a verifier, never a
rejection, per -02's conformance rules (see STATUS.md's Needs decision
section for the still-open ``supersedes`` vs. requested-but-unregistered
``resolves`` relation question -- unrelated to D1, not resolved here).

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

from agent_action_capsule import (
    AssuranceBlock,
    Capsule,
    Chain,
    ConstraintRecord,
    Disposition,
    compute_capsule_id,
    json_digest,
)

from .action import Action
from .signing import Signer

__all__ = [
    "ALLOW",
    "DENY",
    "ESCALATE",
    "ConstraintOutcome",
    "build_decision_capsule",
]

ALLOW = "allow"
DENY = "deny"
ESCALATE = "escalate"

# Disposition mapping (see module docstring). `escalate` -> `hitl_dispatched`
# for both `decision` and `verdict_class`, per D1 (2026-08-05).
_DISPOSITION_BY_OUTCOME = {
    ALLOW: {"decision": "accept", "verdict_class": None},
    DENY: {"decision": "reject", "verdict_class": "blocked"},
    ESCALATE: {"decision": "hitl_dispatched", "verdict_class": "hitl_dispatched"},
}


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
        format_version="2",
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


def build_event_capsule(
    *,
    operator: str,
    developer: str,
    signer: Signer,
    event: str,
    detail: dict,
    timestamp: str | None = None,
    action_id: str | None = None,
    chain_parent: str | None = None,
    chain_relation: str | None = None,
) -> dict:
    """Build a passive administrative record: a degradation/recovery event
    (gap window, rebuild range, operator alert), a policy-manifest
    activation (``capsule_ledger.policy``), or similar -- never a gate decision.
    ``action_type: "fyi"`` per the reference library's own convention
    ("passive observation; the emit tier records what happened but does not
    gate or decide"). Requires a live ``signer`` for the same reason a
    decision capsule does -- an unsigned record is not a record.

    ``chain_parent``/``chain_relation`` are optional, same shape as
    ``build_decision_capsule``'s -- e.g. a manifest activation cites its
    predecessor activation (or a genesis sentinel) with
    ``chain_relation="epoch_opens"`` (``cli/blame_cmd.py``'s / ``cli/
    diff_cmd.py``'s existing epoch-boundary chain vocabulary).
    """
    from .action import Action  # local import: avoids a module cycle at import time

    action = Action(
        verb=event,
        operator=operator,
        developer=developer,
        action_type="fyi",
        action_id=action_id,
        timestamp=timestamp,
    )
    chain = Chain(parent_capsule_id=chain_parent, relation=chain_relation) if chain_parent else None
    capsule_obj = Capsule(
        spec_version="draft-mih-scitt-agent-action-capsule-02",
        format_version="2",
        action_id=action.resolved_action_id(),
        action_type="fyi",
        operator=operator,
        developer=developer,
        timestamp=action.resolved_timestamp(),
        assurance=AssuranceBlock(
            attestation_mode="self_attested",
            effect_mode="not_applicable",
            ledger_mode="chained" if chain is not None else "standalone",
        ),
        chain=chain,
    )
    body = capsule_obj.to_dict()
    body["asg_payload"] = {"event": event, "detail": detail}

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
