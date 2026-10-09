# SPDX-License-Identifier: Apache-2.0
"""offer_expiry check: is the proposal the action acts on still current?

The age is the action's own ``timestamp`` minus ``Action.proposal_at``, in
whole seconds, both RFC 3339 timestamps in UTC (``Z``), as sealed on the
record. Within ``max_age_seconds`` passes. Older is not a failure: the
check records ``n/a`` naming ``proposal_at``, which a report reads as
not evaluable, and the reason says to ask again. A proposal_at that is
missing, unreadable or later than the action records the same ``n/a``;
the reason says which, and the sealed timestamps and the pinned limit let a
verifier recompute it. Applies only to the configured ``action_classes``.
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


def _outcome(result: str, reason: str, evidence: ExpiryEvidence | NotApplicableEvidence) -> CheckOutcome:
    return CheckOutcome(
        constraint=ConstraintOutcome(
            id=_CHECK_ID, result=result, reason=reason, evidence=evidence, check_type="policy", method=_METHOD
        )
    )


def _missing(reason: str, field: str) -> CheckOutcome:
    return _outcome("n/a", reason, not_applicable_evidence(_CHECK_ID, in_scope=True, missing_field=field))


def _utc(value: str) -> datetime | None:
    if not _UTC.match(value):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        # The shape matched but the date does not exist (month 13, day 31 of
        # a 30-day month): the timestamp is unreadable, which the caller
        # records as n/a naming the field, never as a pass.
        return None


def check_offer_expiry(action: Action, *, max_age_seconds: int, action_classes: list[str]) -> CheckOutcome:
    if action.action_class not in action_classes:
        return _outcome("n/a", "the rule is not configured for this action class",
                        not_applicable_evidence(_CHECK_ID, in_scope=False))
    if action.proposal_at is None:
        return _missing("the action carries no proposal_at; the proposal's age could not be checked", "proposal_at")
    if action.timestamp is None:
        return _missing("the action carries no timestamp; the proposal's age could not be checked", "timestamp")
    decided = _utc(action.timestamp)
    if decided is None:
        return _missing(f"timestamp {action.timestamp!r} is not an RFC 3339 UTC timestamp", "timestamp")
    proposed = _utc(action.proposal_at)
    if proposed is None:
        return _missing(f"proposal_at {action.proposal_at!r} is not an RFC 3339 UTC timestamp", "proposal_at")
    age = (decided - proposed) // _SECOND
    if age < 0:
        return _missing(f"proposal_at {action.proposal_at!r} is later than the action", "proposal_at")
    if age > max_age_seconds:
        return _missing(
            f"the proposal is {age}s old, past the {max_age_seconds}s limit; not evaluable, re-ask before acting",
            "proposal_at",
        )
    evidence = ExpiryEvidence(proposal_at=action.proposal_at, decided_at=action.timestamp, age_seconds=age,
                              max_age_seconds=max_age_seconds)
    return _outcome("pass", f"the proposal is {age}s old, within the {max_age_seconds}s limit", evidence)
