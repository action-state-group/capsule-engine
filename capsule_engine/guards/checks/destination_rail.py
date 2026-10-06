# SPDX-License-Identifier: Apache-2.0
"""destination_rail check: set membership of the action's payment rail.

Fails when ``Action.rail`` is one of the configured ``watched_rails`` (for
example a peer-to-peer or gift-card rail). Applies only to the configured
``action_classes``. It records the rail it read and
the set it compared against; it does not judge why the rail was chosen.
"""
from __future__ import annotations

from ..action import Action
from ..capsule import ConstraintOutcome, not_applicable_evidence
from .base import CheckOutcome

__all__ = ["check_destination_rail"]

_CHECK_ID = "destination_rail"
_METHOD = "set_membership_v0"


def check_destination_rail(action: Action, *, watched_rails: list[str], action_classes: list[str]) -> CheckOutcome:
    if action.action_class not in action_classes:
        return CheckOutcome(
            constraint=ConstraintOutcome(
                id=_CHECK_ID,
                result="n/a",
                reason="the rule is not configured for this action class",
                evidence=not_applicable_evidence(_CHECK_ID, in_scope=False),
                check_type="policy",
                method=_METHOD,
            )
        )
    if action.rail is None:
        return CheckOutcome(
            constraint=ConstraintOutcome(
                id=_CHECK_ID,
                result="n/a",
                reason="the action carries no rail; the destination rail could not be checked",
                evidence=not_applicable_evidence(_CHECK_ID, in_scope=True, missing_field="rail"),
                check_type="policy",
                method=_METHOD,
            )
        )
    watched = sorted(watched_rails)
    evidence = {"rail": action.rail, "watched_rails": watched}
    if action.rail in watched:
        return CheckOutcome(
            constraint=ConstraintOutcome(
                id=_CHECK_ID,
                result="fail",
                reason=f"rail {action.rail!r} is a watched rail",
                evidence=evidence,
                check_type="policy",
                method=_METHOD,
            )
        )
    return CheckOutcome(
        constraint=ConstraintOutcome(
            id=_CHECK_ID,
            result="pass",
            reason=f"rail {action.rail!r} is not a watched rail",
            evidence=evidence,
            check_type="policy",
            method=_METHOD,
        )
    )
