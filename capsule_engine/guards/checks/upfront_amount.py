# SPDX-License-Identifier: Apache-2.0
"""upfront_amount check: the part of a payment asked for before delivery,
against two limits.

Reads ``Action.upfront_amount_minor`` and fails when it is above
``upfront_max_minor``, or above ``upfront_max_bps`` basis points of
``Action.amount_minor`` (the total). Integer arithmetic only: the share is
compared as ``upfront * 10000 > amount * bps``. Without a total only the
absolute limit is checked, and the evidence says so. Applies only to the
configured ``action_classes``.
"""
from __future__ import annotations

from typing import TypedDict

from ..action import Action
from ..capsule import ConstraintOutcome, NotApplicableEvidence, not_applicable_evidence
from .base import CheckOutcome

__all__ = ["check_upfront_amount"]

_CHECK_ID = "upfront_amount"
_METHOD = "upfront_threshold_v0"
_BPS = 10_000


class UpfrontEvidence(TypedDict):
    upfront_amount_minor: int
    amount_minor: int | None
    upfront_max_minor: int
    upfront_max_bps: int
    over_absolute: bool
    over_share: bool


def _outcome(result: str, reason: str, evidence: UpfrontEvidence | NotApplicableEvidence) -> CheckOutcome:
    return CheckOutcome(
        constraint=ConstraintOutcome(
            id=_CHECK_ID, result=result, reason=reason, evidence=evidence, check_type="policy", method=_METHOD
        )
    )


def check_upfront_amount(
    action: Action, *, upfront_max_minor: int, upfront_max_bps: int, action_classes: list[str]
) -> CheckOutcome:
    if action.action_class not in action_classes:
        return _outcome("n/a", "the rule is not configured for this action class",
                        not_applicable_evidence(_CHECK_ID, in_scope=False))
    upfront = action.upfront_amount_minor
    if upfront is None:
        return _outcome("n/a", "the action carries no upfront_amount_minor; it could not be checked",
                        not_applicable_evidence(_CHECK_ID, in_scope=True, missing_field="upfront_amount_minor"))
    total = action.amount_minor
    over_absolute = upfront > upfront_max_minor
    over_share = total is not None and upfront * _BPS > total * upfront_max_bps
    evidence = UpfrontEvidence(
        upfront_amount_minor=upfront,
        amount_minor=total,
        upfront_max_minor=upfront_max_minor,
        upfront_max_bps=upfront_max_bps,
        over_absolute=over_absolute,
        over_share=over_share,
    )
    if over_absolute or over_share:
        which = " and ".join(n for n, hit in (("the absolute limit", over_absolute), ("the share limit", over_share)) if hit)
        return _outcome("fail", f"the up-front amount {upfront} is above {which}", evidence)
    if total is None:
        return _outcome("pass", f"the up-front amount {upfront} is within the absolute limit; no total to share against",
                        evidence)
    return _outcome("pass", f"the up-front amount {upfront} is within both limits", evidence)
