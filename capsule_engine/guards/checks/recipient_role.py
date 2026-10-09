# SPDX-License-Identifier: Apache-2.0
"""recipient_role check: membership of the disclosure recipient's role.

Reads ``Action.recipient_role``, one member of the closed set ``roles``
the wicket pins (``fulfilling_merchant``, ``third_party``, ``self``; a
seller's set is ``buyer``, ``third_party``, ``self``), and passes when it is
one of ``allowed_roles``. A role outside ``roles`` fails closed and the
evidence says it was not recognised. It records the role, never who the
recipient is. Applies only to the configured ``action_classes``.
"""
from __future__ import annotations

from typing import TypedDict

from ..action import Action
from ..capsule import ConstraintOutcome, NotApplicableEvidence, not_applicable_evidence
from .base import CheckOutcome

__all__ = ["check_recipient_role"]

_CHECK_ID = "recipient_role"
_METHOD = "set_membership_v0"


class RoleEvidence(TypedDict):
    recipient_role: str
    recognised: bool
    allowed_roles: list[str]


def _outcome(result: str, reason: str, evidence: RoleEvidence | NotApplicableEvidence) -> CheckOutcome:
    return CheckOutcome(
        constraint=ConstraintOutcome(
            id=_CHECK_ID, result=result, reason=reason, evidence=evidence, check_type="policy", method=_METHOD
        )
    )


def check_recipient_role(
    action: Action, *, roles: list[str], allowed_roles: list[str], action_classes: list[str]
) -> CheckOutcome:
    if action.action_class not in action_classes:
        return _outcome("n/a", "the rule is not configured for this action class",
                        not_applicable_evidence(_CHECK_ID, in_scope=False))
    if action.recipient_role is None:
        return _outcome("n/a", "the action carries no recipient_role; the recipient's role could not be checked",
                        not_applicable_evidence(_CHECK_ID, in_scope=True, missing_field="recipient_role"))
    recognised = action.recipient_role in roles
    allowed = sorted(allowed_roles)
    evidence = RoleEvidence(recipient_role=action.recipient_role, recognised=recognised, allowed_roles=allowed)
    # A role outside the closed set fails even if ``allowed_roles`` names it.
    if recognised and action.recipient_role in allowed:
        return _outcome("pass", f"the recipient is the {action.recipient_role}", evidence)
    return _outcome("fail", f"recipient_role {action.recipient_role!r} is not one of {allowed} in {sorted(roles)}",
                    evidence)
