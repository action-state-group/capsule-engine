# SPDX-License-Identifier: Apache-2.0
"""recurring_charge check: a read of the action's declared recurrence.

Fails when ``Action.recurrence`` is present and is not one of the configured
``one_time_values`` -- that is, when the payment sets up a charge that
repeats. Applies only to the configured ``action_classes``.
"""
from __future__ import annotations

from ..action import Action
from ..capsule import ConstraintOutcome, not_applicable_evidence
from .base import CheckOutcome

__all__ = ["check_recurring_charge"]

_CHECK_ID = "recurring_charge"
_METHOD = "structured_field_v0"


def _outcome(result: str, reason: str, evidence: dict) -> CheckOutcome:
    return CheckOutcome(
        constraint=ConstraintOutcome(
            id=_CHECK_ID, result=result, reason=reason, evidence=evidence, check_type="policy", method=_METHOD
        )
    )


def check_recurring_charge(action: Action, *, one_time_values: list[str], action_classes: list[str]) -> CheckOutcome:
    if action.action_class not in action_classes:
        return _outcome(
            "n/a", "the rule is not configured for this action class", not_applicable_evidence(_CHECK_ID, in_scope=False)
        )
    if action.recurrence is None:
        return _outcome(
            "n/a",
            "the action carries no recurrence; whether it repeats could not be read",
            not_applicable_evidence(_CHECK_ID, in_scope=True, missing_field="recurrence"),
        )
    evidence = {"recurrence": action.recurrence, "one_time_values": sorted(one_time_values)}
    if action.recurrence in one_time_values:
        return _outcome("pass", f"recurrence {action.recurrence!r} is a one-time payment", evidence)
    return _outcome("fail", f"recurrence {action.recurrence!r} sets up a repeating charge", evidence)
