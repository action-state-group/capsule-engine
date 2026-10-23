# SPDX-License-Identifier: Apache-2.0
"""material_fields_changed and offer_fields_changed checks: a count of
changed fields against a threshold.

The producer counts how many fields on a list differ from what the user
approved (material) or stated (offer), and sends the count with the digest
of the list it counted over (the basis). The list is pinned in the wicket
config, so a change to it moves the definition digest. The check reads the
count only when the basis equals the digest of the pinned list, and fails
when the count is above ``max_changed``. It never reads the fields or their
values. Applies only to the configured ``action_classes``.
"""
from __future__ import annotations

from typing import TypedDict

from agent_action_capsule import json_digest

from ..action import Action
from ..capsule import ConstraintOutcome, NotApplicableEvidence, not_applicable_evidence
from .base import CheckOutcome

__all__ = ["check_material_fields_changed", "check_offer_fields_changed", "fields_basis"]

_METHOD = "pinned_field_count_v0"


class FieldCountEvidence(TypedDict):
    """``changed`` is the count read from the action's ``material_fields_changed``
    or ``offer_fields_changed``; the constraint id says which."""

    changed: int
    fields_basis: str
    max_changed: int


def fields_basis(counted_fields: list[str]) -> str:
    """The basis a producer sends for ``counted_fields``: SHA-256 over the
    JCS bytes of the list, in the order the wicket config pins it."""
    return json_digest(list(counted_fields))


def _check(
    check_id: str,
    count_field: str,
    basis_field: str,
    action: Action,
    count: int | None,
    basis: str | None,
    *,
    counted_fields: list[str],
    max_changed: int,
    action_classes: list[str],
) -> CheckOutcome:
    def outcome(result: str, reason: str, evidence: FieldCountEvidence | NotApplicableEvidence) -> CheckOutcome:
        return CheckOutcome(
            constraint=ConstraintOutcome(
                id=check_id, result=result, reason=reason, evidence=evidence, check_type="policy", method=_METHOD
            )
        )

    if action.action_class not in action_classes:
        return outcome("n/a", "the rule is not configured for this action class",
                       not_applicable_evidence(check_id, in_scope=False))
    if count is None:
        return outcome("n/a", f"the action carries no {count_field}; it could not be checked",
                       not_applicable_evidence(check_id, in_scope=True, missing_field=count_field))
    pinned = fields_basis(counted_fields)
    # An absent basis and one for another list are the same case: the count
    # cannot be read as a count over the pinned list. The sealed basis on the
    # record tells the two apart.
    if basis != pinned:
        reason = (f"the action carries no {basis_field}" if basis is None
                  else f"{basis_field} is not the digest of the pinned field list")
        return outcome("n/a", f"{reason}; the count could not be read",
                       not_applicable_evidence(check_id, in_scope=True, missing_field=basis_field))
    evidence = FieldCountEvidence(changed=count, fields_basis=pinned, max_changed=max_changed)
    if count > max_changed:
        return outcome("fail", f"{count} pinned field(s) changed, above {max_changed}", evidence)
    return outcome("pass", f"{count} pinned field(s) changed, within {max_changed}", evidence)


def check_material_fields_changed(
    action: Action, *, counted_fields: list[str], max_changed: int, action_classes: list[str]
) -> CheckOutcome:
    return _check(
        "material_fields_changed", "material_fields_changed", "material_fields_basis", action,
        action.material_fields_changed, action.material_fields_basis,
        counted_fields=counted_fields, max_changed=max_changed, action_classes=action_classes,
    )


def check_offer_fields_changed(
    action: Action, *, counted_fields: list[str], max_changed: int, action_classes: list[str]
) -> CheckOutcome:
    return _check(
        "offer_fields_changed", "offer_fields_changed", "offer_fields_basis", action,
        action.offer_fields_changed, action.offer_fields_basis,
        counted_fields=counted_fields, max_changed=max_changed, action_classes=action_classes,
    )
