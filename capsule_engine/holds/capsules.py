# SPDX-License-Identifier: Apache-2.0
"""Builders for the four hold-lifecycle record types: ``hold.reserve``,
``hold.release``, ``hold.expire``, ``hold.reconcile``.

A reservation is a capsule, not side-band state (design premise shared by
capsule-emit #51/#52/#53): reserve/release/expire/reconcile are signed,
appended records built the same way ``guards/capsule.py``'s
``build_decision_capsule``/``build_event_capsule`` build every other capsule
this codebase produces -- same ``Capsule``/``Disposition``/``Chain``/
``AssuranceBlock`` primitives, same digest-then-sign-then-reseal sequence, so
these records are ordinary, independently verifiable ledger capsules with no
special-cased verification path.

Record-type identity lives in the ``action_id`` verb (``hold.reserve/<uuid>``
etc.), the same place every other capsule in this codebase carries its verb
(``guards/checks/dedupe.py``'s ``_capsule_verb``) -- not a new top-level
field, and not the spec's closed ``action_type`` enum (-02 §5.1 restricts
``action_type`` to ``fyi``/``decide``; these are ``decide``, since a
reservation is a consequential decision about exposure).

Every numeric field here is integer minor units, MUST-FAIL on float --
mirrors ``folds/reducers.py``'s own discipline for the same reason: these
values flow into a fold's ``sum`` reducer (``hold.active_exposure``,
``folds/catalog_defs/hold.active_exposure.yaml``), which cannot reproduce a
float byte-exactly across implementations.
"""
from __future__ import annotations

from typing import Any

from agent_action_capsule import (
    AssuranceBlock,
    Capsule,
    Chain,
    Disposition,
    compute_capsule_id,
    json_digest,
)

from ..guards.action import Action
from ..guards.signing import Signer
from .errors import FLOAT_IN_HOLD_AMOUNT, NON_INTEGER_HOLD_AMOUNT, HoldError

__all__ = [
    "build_hold_reserve_capsule",
    "build_hold_release_capsule",
    "build_hold_expire_capsule",
    "build_hold_reconcile_capsule",
]

_SPEC_VERSION = "draft-mih-scitt-agent-action-capsule-02"
_FORMAT_VERSION = "2"

# The only registered chain.relation this task uses: "supersedes" for a
# terminal transition that closes/replaces the reserve's open state
# (release, expiry, a successful reconcile), matching the existing
# EUR150k-bridge precedent (`guards/capsule.py`'s own docstring; the
# registry's own definition: "resolution, expiry, escalation close/replace
# the parent's open state").
SUPERSEDES = "supersedes"


def check_integer_amount(value: Any, field: str) -> int:
    """Amounts are integer minor units -- floats MUST-FAIL (same discipline
    as ``folds/reducers.py._check_integer``, applied at record-build time so
    a bad amount fails loudly here with a named reason, not deep inside JCS
    canonicalization when the capsule is later digested)."""
    if isinstance(value, bool):
        raise HoldError(NON_INTEGER_HOLD_AMOUNT, f"{field!r} is a bool, not an integer amount")
    if isinstance(value, float):
        raise HoldError(FLOAT_IN_HOLD_AMOUNT, f"{field!r} carries a float ({value!r}); amounts MUST be integer minor units")
    if not isinstance(value, int):
        raise HoldError(NON_INTEGER_HOLD_AMOUNT, f"{field!r} is {type(value).__name__}, not an integer")
    return value


def _hold_action(action: Action, verb: str) -> Action:
    """A fresh ``Action`` carrying this hold record's own verb/action_id,
    otherwise identical to the action this hold is evaluated for."""
    return Action(
        verb=verb,
        operator=action.operator,
        developer=action.developer,
        action_class=action.action_class,
        action_type="decide",
        timestamp=action.resolved_timestamp(),
        amount_minor=action.amount_minor,
        currency=action.currency,
        target=action.target,
    )


def _seal(body: dict, signer: Signer) -> dict:
    """Sign the pre-signature canonical body, commit the signature into the
    body, then compute capsule_id over everything (identical sequence to
    ``guards/capsule.py``'s two builders -- see that module's own comment on
    why the signature is committed pre-digest)."""
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


def _build(*, hold_action: Action, chain: Chain | None, asg_payload: dict, signer: Signer) -> dict:
    disposition = Disposition(decision="accept", approver="policy", human_disposed=False, verdict_class=None)
    capsule_obj = Capsule(
        spec_version=_SPEC_VERSION,
        format_version=_FORMAT_VERSION,
        action_id=hold_action.resolved_action_id(),
        action_type="decide",
        operator=hold_action.operator,
        developer=hold_action.developer,
        timestamp=hold_action.resolved_timestamp(),
        assurance=AssuranceBlock(
            attestation_mode="self_attested",
            effect_mode="not_applicable",
            ledger_mode="chained" if chain is not None else "standalone",
        ),
        disposition=disposition,
        chain=chain,
    )
    body = capsule_obj.to_dict()
    body["asg_payload"] = asg_payload
    return _seal(body, signer)


def build_hold_reserve_capsule(
    *,
    action: Action,
    reserved_amount_minor: int,
    fold_id: str,
    fold_digest: str,
    fold_envelope: dict,
    checkpoint: dict,
    signer: Signer,
    manifest_digest: str | None = None,
) -> dict:
    """Reserve-at-seal (#51.1): cites the evaluation's own fold envelope
    (fold digest, record range, checkpoint) and the reserved amount. No
    ``chain`` -- a fresh reservation opens a new hold, standalone; release/
    expiry/reconcile are what chain back to *this* capsule's id."""
    check_integer_amount(reserved_amount_minor, "reserved_amount_minor")
    hold_action = _hold_action(action, "hold.reserve")
    asg_payload: dict[str, Any] = {
        "amount_minor": reserved_amount_minor,
        "reserved_amount_minor": reserved_amount_minor,
        "hold_scope": {"fold_id": fold_id, "fold_digest": fold_digest, "subject": action.developer},
        "fold_envelope": fold_envelope,
        "checkpoint": checkpoint,
    }
    if action.currency is not None:
        asg_payload["currency"] = action.currency
    if action.target is not None:
        asg_payload["target"] = action.target
    if manifest_digest is not None:
        asg_payload["manifest_digest"] = manifest_digest
    return _build(hold_action=hold_action, chain=None, asg_payload=asg_payload, signer=signer)


def build_hold_release_capsule(
    *,
    action: Action,
    reserve_capsule_id: str,
    reserved_amount_minor: int,
    signer: Signer,
    reason: str | None = None,
) -> dict:
    """Voluntary cancellation of a still-active hold. Terminal: the reserve's
    exposure fully unwinds (``amount_minor`` is the negative of the reserved
    amount, so the ``hold.active_exposure`` fold nets back to zero for this
    hold)."""
    check_integer_amount(reserved_amount_minor, "reserved_amount_minor")
    hold_action = _hold_action(action, "hold.release")
    chain = Chain(parent_capsule_id=reserve_capsule_id, relation=SUPERSEDES)
    asg_payload: dict[str, Any] = {
        "amount_minor": -reserved_amount_minor,
        "released_amount_minor": reserved_amount_minor,
    }
    if reason is not None:
        asg_payload["reason"] = reason
    return _build(hold_action=hold_action, chain=chain, asg_payload=asg_payload, signer=signer)


def build_hold_expire_capsule(
    *,
    action: Action,
    reserve_capsule_id: str,
    reserved_amount_minor: int,
    signer: Signer,
    reason: str | None = None,
) -> dict:
    """Expiry (#52.1): TERMINAL for this hold -- after this capsule exists,
    nothing may dispatch citing the original reservation. Same net-to-zero
    exposure unwind as release; the two are distinguished by verb (a caller
    choosing to cancel vs. a TTL/policy elapsing), not by any different
    fold-visible effect."""
    check_integer_amount(reserved_amount_minor, "reserved_amount_minor")
    hold_action = _hold_action(action, "hold.expire")
    chain = Chain(parent_capsule_id=reserve_capsule_id, relation=SUPERSEDES)
    asg_payload: dict[str, Any] = {
        "amount_minor": -reserved_amount_minor,
        "expired_amount_minor": reserved_amount_minor,
    }
    if reason is not None:
        asg_payload["reason"] = reason
    return _build(hold_action=hold_action, chain=chain, asg_payload=asg_payload, signer=signer)


def build_hold_reconcile_capsule(
    *,
    action: Action,
    reserve_capsule_id: str,
    execution_capsule_id: str | None,
    reserved_amount_minor: int,
    executed_amount_minor: int,
    tolerance_minor: int,
    signer: Signer,
    manifest_digest: str | None = None,
) -> dict:
    """Planned vs. executed (#53.1): reserve at planned amount, convert at
    executed amount, the delta sealed as this record -- chained to the
    reserve capsule via ``chain`` (the schema's single-parent link) and to
    the execution capsule via the ``asg_payload.execution_capsule_id``
    citation (a plain payload reference, not a registry relation -- the
    schema has only one ``chain`` slot per capsule).

    ``amount_minor`` is the *delta* (``executed - reserved``): summed against
    the reserve's own ``+reserved_amount_minor`` contribution, the
    ``hold.active_exposure`` fold nets to exactly ``executed_amount_minor``
    once this record lands -- "executed once reconciled" (#53.4) falls out
    of the fold algebra, not a special case in the fold definition.

    Only called for an in-tolerance conversion; an over-tolerance conversion
    routes through the existing cap-exceeded ALLOW/DENY/ESCALATE vocabulary
    instead (``holds/engine.py``) and this capsule is never built for it --
    "never silently adjusts the aggregate" (#53.3).
    """
    check_integer_amount(reserved_amount_minor, "reserved_amount_minor")
    check_integer_amount(executed_amount_minor, "executed_amount_minor")
    check_integer_amount(tolerance_minor, "tolerance_minor")
    delta_minor = executed_amount_minor - reserved_amount_minor
    hold_action = _hold_action(action, "hold.reconcile")
    chain = Chain(parent_capsule_id=reserve_capsule_id, relation=SUPERSEDES)
    asg_payload: dict[str, Any] = {
        "amount_minor": delta_minor,
        "reserved_amount_minor": reserved_amount_minor,
        "executed_amount_minor": executed_amount_minor,
        "delta_minor": delta_minor,
        "tolerance_minor": tolerance_minor,
    }
    if execution_capsule_id is not None:
        asg_payload["execution_capsule_id"] = execution_capsule_id
    if manifest_digest is not None:
        asg_payload["manifest_digest"] = manifest_digest
    return _build(hold_action=hold_action, chain=chain, asg_payload=asg_payload, signer=signer)
