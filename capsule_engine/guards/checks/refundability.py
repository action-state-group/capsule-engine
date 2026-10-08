# SPDX-License-Identifier: Apache-2.0
"""refundability check: can the payment be refunded?

Reads ``Action.refundable`` and fails when it is ``False``. It records the
value it read and the rail beside it (``None`` when the action names none);
it does not read the refund terms themselves. Applies only to the configured
``action_classes``.
"""
from __future__ import annotations

from typing import TypedDict

from ..action import Action
from ..capsule import ConstraintOutcome, NotApplicableEvidence, not_applicable_evidence
from .base import CheckOutcome

__all__ = ["check_refundability"]

_CHECK_ID = "refundability"
_METHOD = "declared_boolean_v0"


class RefundabilityEvidence(TypedDict):
    refundable: bool
    rail: str | None


def _outcome(result: str, reason: str, evidence: RefundabilityEvidence | NotApplicableEvidence) -> CheckOutcome:
    return CheckOutcome(
        constraint=ConstraintOutcome(
            id=_CHECK_ID, result=result, reason=reason, evidence=evidence, check_type="policy", method=_METHOD
        )
    )


def check_refundability(action: Action, *, action_classes: list[str]) -> CheckOutcome:
    if action.action_class not in action_classes:
        return _outcome("n/a", "the rule is not configured for this action class",
                        not_applicable_evidence(_CHECK_ID, in_scope=False))
    if action.refundable is None:
        return _outcome("n/a", "the action carries no refundable; whether it can be undone could not be checked",
                        not_applicable_evidence(_CHECK_ID, in_scope=True, missing_field="refundable"))
    evidence = RefundabilityEvidence(refundable=action.refundable, rail=action.rail)
    if action.refundable:
        return _outcome("pass", "the payment is refundable", evidence)
    return _outcome("fail", "the payment is not refundable", evidence)
