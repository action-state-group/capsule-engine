# SPDX-License-Identifier: Apache-2.0
"""recipient_role check: membership of the disclosure recipient's role.

Reads ``Action.recipient_role``, one member of the closed set ``roles``
the wicket pins (``fulfilling_merchant``, ``third_party``, ``self``; a
seller's set is ``buyer``, ``third_party``, ``self``), and passes when it is
one of ``allowed_roles``. A role outside ``roles`` fails closed and the
evidence says it was not recognised. It records the role, never who the
recipient is. Applies only to the configured ``action_classes``.

``role_classes`` (optional) names roles that pass only for some classes of
data: ``{platform: [email]}`` passes a disclosure to a platform when every
class in ``Action.disclosed_classes`` is ``email``. One that gives any other
class fails, and so does one that names no class, since it cannot be shown
to give only those. A wicket that sets it records the classes it read and the
roles' classes in the evidence; one that does not keeps the evidence it had.
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


class RoleClassesEvidence(RoleEvidence):
    role_classes: dict[str, list[str]]
    disclosed_classes: list[str] | None


def _outcome(
    result: str, reason: str, evidence: RoleEvidence | RoleClassesEvidence | NotApplicableEvidence
) -> CheckOutcome:
    return CheckOutcome(
        constraint=ConstraintOutcome(
            id=_CHECK_ID, result=result, reason=reason, evidence=evidence, check_type="policy", method=_METHOD
        )
    )


def _either(words: list[str]) -> str:
    """``a``, ``a or b``, ``a, b or c``; ``no role`` for none."""
    if not words:
        return "no role"
    return words[0] if len(words) == 1 else f"{', '.join(words[:-1])} or {words[-1]}"


def _passing(allowed: list[str], role_classes: dict[str, list[str]]) -> str:
    """The roles that pass, once: ``fulfilling_merchant or self, or a
    platform given only email``."""
    limited = [f"a {role} given only {_either(classes)}" for role, classes in sorted(role_classes.items())]
    if not allowed:
        return _either(limited)
    return _either(allowed) + "".join(f", or {phrase}" for phrase in limited)


def check_recipient_role(
    action: Action,
    *,
    roles: list[str],
    allowed_roles: list[str],
    action_classes: list[str],
    role_classes: dict[str, list[str]] | None = None,
) -> CheckOutcome:
    limited = {role: sorted(classes) for role, classes in (role_classes or {}).items()}
    misplaced = sorted(set(limited) - set(roles)) + sorted(set(limited) & set(allowed_roles))
    if misplaced or any(not classes for classes in limited.values()):
        raise ValueError(
            "role_classes must name roles in roles and not in allowed_roles, each with at least one class; "
            f"got {sorted(limited)}"
        )
    if action.action_class not in action_classes:
        return _outcome("n/a", "the rule is not configured for this action class",
                        not_applicable_evidence(_CHECK_ID, in_scope=False))
    role = action.recipient_role
    if role is None:
        return _outcome("n/a", "the action carries no recipient_role; the recipient's role could not be checked",
                        not_applicable_evidence(_CHECK_ID, in_scope=True, missing_field="recipient_role"))
    recognised = role in roles
    allowed = sorted(allowed_roles)
    evidence: RoleEvidence | RoleClassesEvidence = RoleEvidence(
        recipient_role=role, recognised=recognised, allowed_roles=allowed
    )
    if role_classes is not None:
        disclosed = list(action.disclosed_classes) if action.disclosed_classes is not None else None
        evidence = RoleClassesEvidence(**evidence, role_classes=limited, disclosed_classes=disclosed)
    passing = _passing(allowed, limited)
    # A role outside the closed set fails even if ``allowed_roles`` names it.
    if not recognised:
        return _outcome("fail", f"recipient_role {role!r} is not a role this rule knows; personal data "
                        f"passes only to {passing}", evidence)
    if role in allowed:
        return _outcome("pass", f"the recipient is the {role}", evidence)
    if role in limited:
        classes = limited[role]
        if action.disclosed_classes is None:
            return _outcome("fail", f"the recipient is a {role}, which may be given only {_either(classes)}; "
                            "the disclosure names no class of data", evidence)
        others = [c for c in action.disclosed_classes if c not in classes]
        if others:
            return _outcome("fail", f"the recipient is a {role}, which may be given only {_either(classes)}; "
                            f"this disclosure also gives {_either(others)}", evidence)
        return _outcome("pass", f"the recipient is a {role}, given only {_either(list(action.disclosed_classes))}",
                        evidence)
    return _outcome("fail", f"the recipient is a {role}; personal data passes only to {passing}", evidence)
