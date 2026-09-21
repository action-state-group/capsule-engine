# SPDX-License-Identifier: Apache-2.0
"""Build and seal a passive ``fyi`` event as an Agent Action Capsule.

Deliberately separate from ``guards/capsule.py``'s ``build_decision_capsule``
(W3, 2026-09-01): this module carries NO guard/decision vocabulary --
no ``ALLOW``/``DENY``/``ESCALATE``, no ``ConstraintOutcome``, no
``build_decision_capsule`` import -- so that if a neutral caller ever
materializes (e.g. capsule-emit), lifting ``build_event_capsule`` there is a
file move, not surgery. ``action_id``/``timestamp`` resolution below is a
deliberately-duplicated two-line copy of ``guards/action.py``'s
``Action.resolved_action_id``/``resolved_timestamp`` -- not an import of
``Action`` itself, since ``Action`` is the guard's own decision input
contract (``action_class``, ``amount_minor``, ``cited_mandate_capsule_id``,
etc.) and pulling it in here would re-couple this module to guard vocabulary
through the back door.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from agent_action_capsule import (
    DEFAULT_FORMAT_VERSION,
    AssuranceBlock,
    Capsule,
    Chain,
    compute_capsule_id,
    json_digest,
)
from agent_action_capsule.canonical import CANONICALIZATION_JCS

if TYPE_CHECKING:
    # Signer is a type annotation only (PEP 563 lazy annotations, via the
    # __future__ import above) -- a real runtime import here recreates the
    # exact guards<->events circular dependency this module's own docstring
    # says it exists to avoid (found live 2026-09-02: ImportError whenever
    # something imports events/conversation before guards finishes
    # initializing). guards/engine.py's own import of build_event_capsule is
    # what closes the cycle if this import is eager.
    from ..guards.signing import Signer

__all__ = ["build_event_capsule"]


def _resolved_action_id(action_id: str | None, verb: str) -> str:
    return action_id or f"{verb}/{uuid.uuid4()}"


def _resolved_timestamp(timestamp: str | None) -> str:
    return timestamp or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


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
    resolved_action_id = _resolved_action_id(action_id, event)
    resolved_timestamp = _resolved_timestamp(timestamp)
    chain = Chain(parent_capsule_id=chain_parent, relation=chain_relation) if chain_parent else None
    capsule_obj = Capsule(
        spec_version="draft-mih-scitt-agent-action-capsule-02",
        format_version=DEFAULT_FORMAT_VERSION,
        canonicalization_id=CANONICALIZATION_JCS,
        action_id=resolved_action_id,
        action_type="fyi",
        operator=operator,
        developer=developer,
        timestamp=resolved_timestamp,
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
