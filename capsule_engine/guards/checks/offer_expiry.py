# SPDX-License-Identifier: Apache-2.0
"""offer_expiry check: is the proposal the action acts on still current?

The age is the action's own ``timestamp`` minus ``Action.proposal_at``, in
whole seconds, both RFC 3339 timestamps in UTC (``Z``), as sealed on the
record. Within ``max_age_seconds`` passes. Older fails: the proposal has
expired, and acting needs a new proposed action, asked about afresh; the
evidence names both timestamps, the age and the limit. When the age cannot
be established (``proposal_at`` or ``timestamp`` missing, unreadable, or a
proposal later than the action) the check also fails, with
``expiry_unverified`` and the field it could not read: an offer whose
expiry is unknown is never let through as current. Applies only to the
configured ``action_classes``.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import TypedDict

from ..action import Action
from ..capsule import ConstraintOutcome, NotApplicableEvidence, not_applicable_evidence
from .base import CheckOutcome

__all__ = ["check_offer_expiry"]

_CHECK_ID = "offer_expiry"
_METHOD = "age_threshold_v0"
_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z$")
_SECOND = timedelta(seconds=1)


class ExpiryEvidence(TypedDict):
    proposal_at: str
    decided_at: str
    age_seconds: int
    max_age_seconds: int
    expired: bool


class UnverifiedEvidence(TypedDict):
    expiry_unverified: bool
    missing_field: str


def _outcome(
    result: str, reason: str, evidence: ExpiryEvidence | UnverifiedEvidence | NotApplicableEvidence
) -> CheckOutcome:
    return CheckOutcome(
        constraint=ConstraintOutcome(
            id=_CHECK_ID, result=result, reason=reason, evidence=evidence, check_type="policy", method=_METHOD
        )
    )


def _unverified(reason: str, field: str) -> CheckOutcome:
    # The intended disposition for this case is ASK_UNVERIFIED, which the
    # pack schema's DEFAULT_DISPOSITION_VALUES does not have yet (adding it
    # is an open item); the definition declares ASK, and this marker is what
    # tells the two apart.
    return _outcome("fail", f"{reason}; the offer's expiry could not be established",
                    UnverifiedEvidence(expiry_unverified=True, missing_field=field))


def _utc(value: str) -> datetime | None:
    if not _UTC.match(value):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        # The shape matched but the date does not exist (month 13, day 31 of
        # a 30-day month): the timestamp is unreadable, which the caller
        # records as unverified, never as a pass.
        return None


def check_offer_expiry(action: Action, *, max_age_seconds: int, action_classes: list[str]) -> CheckOutcome:
    if action.action_class not in action_classes:
        return _outcome("n/a", "the rule is not configured for this action class",
                        not_applicable_evidence(_CHECK_ID, in_scope=False))
    if action.proposal_at is None:
        return _unverified("the action carries no proposal_at", "proposal_at")
    if action.timestamp is None:
        return _unverified("the action carries no timestamp", "timestamp")
    decided = _utc(action.timestamp)
    if decided is None:
        return _unverified(f"timestamp {action.timestamp!r} is not an RFC 3339 UTC timestamp", "timestamp")
    proposed = _utc(action.proposal_at)
    if proposed is None:
        return _unverified(f"proposal_at {action.proposal_at!r} is not an RFC 3339 UTC timestamp", "proposal_at")
    age = (decided - proposed) // _SECOND
    if age < 0:
        return _unverified(f"proposal_at {action.proposal_at!r} is later than the action", "proposal_at")
    expired = age > max_age_seconds
    evidence = ExpiryEvidence(proposal_at=action.proposal_at, decided_at=action.timestamp, age_seconds=age,
                              max_age_seconds=max_age_seconds, expired=expired)
    if expired:
        return _outcome(
            "fail",
            f"proposal expired: it is {age}s old, past the {max_age_seconds}s limit; a new proposed action is required",
            evidence,
        )
    return _outcome("pass", f"the proposal is {age}s old, within the {max_age_seconds}s limit", evidence)
