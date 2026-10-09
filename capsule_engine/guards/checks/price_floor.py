# SPDX-License-Identifier: Apache-2.0
"""price_floor check: the action's total against the lowest total its task
authority accepts.

The floor is ``min_total_minor`` in the body of the sealed task-authority
record the action cites (``FLOOR_PATH``). The record is read only when the
engine's own digest of it equals ``Action.task_authority_ref``
(``task_authority.bind_task_authority_record``), so the floor comes from the
authority and never from the action. Fails when ``Action.amount_minor`` is
below the floor; the floor itself passes. Both are compared in the action's
own minor units, as ``caps`` compares; the record names no currency. The
mirror of ``caps``, which compares upward only. Applies only to the
configured ``action_classes``.
"""
from __future__ import annotations

from typing import TypedDict

from ..action import Action
from ..capsule import ConstraintOutcome, NotApplicableEvidence, not_applicable_evidence
from .base import CheckOutcome
from .task_authority import TaskAuthorityRecord, UnboundRecord, bind_task_authority_record

__all__ = ["FLOOR_PATH", "check_price_floor"]

_CHECK_ID = "price_floor"
_METHOD = "floor_threshold_v0"
_FLOOR = "min_total_minor"

# Where the floor sits inside the sealed record, one member name per level.
FLOOR_PATH: tuple[str, ...] = ("body", _FLOOR)


class FloorEvidence(TypedDict):
    task_authority_ref: str
    amount_minor: int
    min_total_minor: int
    below_floor: bool


def _outcome(result: str, reason: str, evidence: FloorEvidence | NotApplicableEvidence) -> CheckOutcome:
    return CheckOutcome(
        constraint=ConstraintOutcome(
            id=_CHECK_ID, result=result, reason=reason, evidence=evidence, check_type="policy", method=_METHOD
        )
    )


def _missing(reason: str, field: str) -> CheckOutcome:
    return _outcome("n/a", reason, not_applicable_evidence(_CHECK_ID, in_scope=True, missing_field=field))


def _floor_in(record: TaskAuthorityRecord) -> int | None:
    """The floor at ``FLOOR_PATH``, or ``None`` when the record sets none or
    sets something other than a count of minor units."""
    node: object = record
    for name in FLOOR_PATH:
        # Decoded JSON: each level is checked before it is read.
        if not isinstance(node, dict) or name not in node:
            return None
        node = node[name]
    # bool is an int subclass; true is not a floor.
    if type(node) is not int or node < 0:
        return None
    return node


def check_price_floor(action: Action, record: TaskAuthorityRecord | None, *, action_classes: list[str]) -> CheckOutcome:
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
    floor = _floor_in(bound)
    if floor is None:
        return _missing(f"the task-authority record sets no {_FLOOR} at {'.'.join(FLOOR_PATH)}", _FLOOR)
    below = amount < floor
    evidence = FloorEvidence(task_authority_ref=ref, amount_minor=amount, min_total_minor=floor,
                             below_floor=below)
    if below:
        return _outcome("fail", f"the amount {amount} is below the floor {floor}", evidence)
    return _outcome("pass", f"the amount {amount} is at or above the floor {floor}", evidence)
