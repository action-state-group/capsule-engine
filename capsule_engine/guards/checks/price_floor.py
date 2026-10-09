# SPDX-License-Identifier: Apache-2.0
"""price_floor check: the action's total against the lowest total the user
will take.

The floor is private. The sealed task-authority record the action cites
carries only ``bounds_commitment`` at ``COMMITMENT_PATH``: a salted
commitment (commit_alg ``sha256-jcs-nonce256``) to a ``commercial-bounds/v0``
document holding ``min_total_minor``. The document, its nonce and the
commitment come to the checker beside the action as the checker input's
``commercial_bounds_opening``, on the user's own device only
(``GuardEngine.check(..., commercial_bounds_opening=...)``). The record is
read only once the engine's own digest of it equals
``Action.task_authority_ref`` (``task_authority.bind_task_authority_record``),
and the floor only once the opening opens the commitment that record seals
(``bounds_commitment``).

Fails when ``Action.amount_minor`` is below the opened floor; the floor
itself passes. Both are compared in the action's own minor units, as ``caps``
compares; the document names no currency. An opening that does not open the
sealed commitment also fails, naming ``bounds_commitment``: it is a wrong or
altered record, so the rule does not step aside. A missing opening is
``n/a`` naming ``commercial_bounds_opening``: a counterparty or a stranger
rightly has none. A task authority that commits to no floor is ``n/a``
naming ``bounds_commitment``. Applies only to the configured
``action_classes``.

The floor, the nonce and the document never leave this check: the reason
and evidence hold neither, only the commitment the record already seals,
the action's own amount and whether it is below.
"""
from __future__ import annotations

import re
from typing import TypedDict

from agent_action_capsule.canonical import FloatInDigestError, UnsafeIntegerError, jcs, json_digest

from ..action import Action
from ..capsule import ConstraintOutcome, NotApplicableEvidence, not_applicable_evidence
from .base import CheckOutcome
from .task_authority import TaskAuthorityRecord, UnboundRecord, bind_task_authority_record

__all__ = [
    "BOUNDS_KIND",
    "COMMITMENT_PATH",
    "CommercialBoundsDocument",
    "CommercialBoundsOpening",
    "bounds_commitment",
    "check_price_floor",
]

_CHECK_ID = "price_floor"
_METHOD = "committed_floor_threshold_v1"
_COMMITMENT = "bounds_commitment"
_OPENING = "commercial_bounds_opening"
_FLOOR = "min_total_minor"
_HEX64 = re.compile(r"[0-9a-f]{64}")

BOUNDS_KIND = "commercial-bounds/v0"

# Where the commitment sits inside the sealed record, one member name per level.
COMMITMENT_PATH: tuple[str, ...] = ("body", _COMMITMENT)


class CommercialBoundsDocument(TypedDict):
    type: str
    min_total_minor: int


class CommercialBoundsOpening(TypedDict):
    """external-check-input/v0 ``commercial_bounds_opening``, as decoded JSON.
    Nothing in it is trusted until it opens the sealed commitment."""

    document: CommercialBoundsDocument
    nonce: str
    bounds_commitment: str


class FloorEvidence(TypedDict):
    task_authority_ref: str
    bounds_commitment: str
    amount_minor: int
    below_floor: bool


class OpeningMismatchEvidence(TypedDict):
    task_authority_ref: str
    bounds_commitment: str
    opens_commitment: bool


def bounds_commitment(nonce: str, document: CommercialBoundsDocument) -> str:
    """commit_alg ``sha256-jcs-nonce256``: SHA-256 over the JCS bytes of
    ``{"nonce", "text"}``, with ``text`` the document's JCS bytes."""
    return json_digest({"nonce": nonce, "text": jcs(document).decode("utf-8")})


def _outcome(
    result: str, reason: str, evidence: FloorEvidence | OpeningMismatchEvidence | NotApplicableEvidence
) -> CheckOutcome:
    return CheckOutcome(
        constraint=ConstraintOutcome(
            id=_CHECK_ID, result=result, reason=reason, evidence=evidence, check_type="policy", method=_METHOD
        )
    )


def _missing(reason: str, field: str) -> CheckOutcome:
    return _outcome("n/a", reason, not_applicable_evidence(_CHECK_ID, in_scope=True, missing_field=field))


def _commitment_in(record: TaskAuthorityRecord) -> str | None:
    """The commitment at ``COMMITMENT_PATH``, or ``None`` when the record
    seals none or seals something other than a SHA-256 hex digest."""
    node: object = record
    for name in COMMITMENT_PATH:
        # Decoded JSON: each level is checked before it is read.
        if not isinstance(node, dict) or name not in node:
            return None
        node = node[name]
    return node if isinstance(node, str) and _HEX64.fullmatch(node) else None


def _opened_floor(opening: object, sealed: str) -> int | None:
    """The floor in ``opening`` once it opens ``sealed``, or ``None`` when it
    does not: a nonce or document out of shape, a stated commitment other
    than the sealed one, or one the nonce and document do not recompute."""
    # Decoded JSON from the checker input: each member is checked before it is read.
    if not isinstance(opening, dict) or opening.get(_COMMITMENT) != sealed:
        return None
    nonce, document = opening.get("nonce"), opening.get("document")
    if not isinstance(nonce, str) or not _HEX64.fullmatch(nonce) or not isinstance(document, dict):
        return None
    try:
        if bounds_commitment(nonce, document) != sealed:
            return None
    # A document holding a float or an unsafe integer has no JCS digest, so
    # it opens no commitment: that is the mismatch the caller fails, not an
    # error to raise.
    except (FloatInDigestError, UnsafeIntegerError):
        return None
    floor = document.get(_FLOOR)
    # bool is an int subclass; true is not a floor.
    if document.get("type") != BOUNDS_KIND or type(floor) is not int or floor < 0:
        return None
    return floor


def check_price_floor(
    action: Action,
    record: TaskAuthorityRecord | None,
    opening: CommercialBoundsOpening | None,
    *,
    action_classes: list[str],
) -> CheckOutcome:
    if action.action_class not in action_classes:
        return _outcome("n/a", "the rule is not configured for this action class",
                        not_applicable_evidence(_CHECK_ID, in_scope=False))
    amount = action.amount_minor
    if amount is None:
        return _missing("the action carries no amount_minor; it could not be compared with the floor", "amount_minor")
    try:
        ref, bound = bind_task_authority_record(action, record)
    except UnboundRecord as exc:
        return _missing(str(exc), exc.missing_field)
    sealed = _commitment_in(bound)
    if sealed is None:
        return _missing(f"the task-authority record commits to no floor at {'.'.join(COMMITMENT_PATH)}",
                        _COMMITMENT)
    if opening is None:
        return _missing("no commercial-bounds opening was supplied; the committed floor could not be read",
                        _OPENING)
    floor = _opened_floor(opening, sealed)
    if floor is None:
        return _outcome(
            "fail",
            "the commercial-bounds opening does not open the bounds_commitment the task authority seals",
            OpeningMismatchEvidence(task_authority_ref=ref, bounds_commitment=sealed, opens_commitment=False),
        )
    below = amount < floor
    evidence = FloorEvidence(task_authority_ref=ref, bounds_commitment=sealed, amount_minor=amount,
                             below_floor=below)
    if below:
        return _outcome("fail", "the amount is below the committed floor", evidence)
    return _outcome("pass", "the amount is at or above the committed floor", evidence)
