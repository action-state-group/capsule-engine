# SPDX-License-Identifier: Apache-2.0
"""task_authority check: is the action inside the task authority it cites?

``Action.task_authority_ref`` is an opaque reference: the SHA-256 digest of
the sealed task-authority record the action cites. The record's body is
supplied beside the action for one decision (``GuardEngine.check(...,
task_authority=...)``), and it is read ONLY when the JCS digest of that body
equals the reference: a body the reference does not bind is never trusted.
The bound body is a plan in the existing shape (``guards/plan.py``
``PlanDefinition``: ``outcome_id``, ``allowed_actions``, ``preconditions``,
optional ``binding``), and the plan's own containment check
(``plan_containment.py``) decides. There is no second plan vocabulary.

A missing body, a body the reference does not bind, and a bound body that is
not a plan are the same case: the bounds could not be read, so the rule
records ``n/a`` naming ``task_authority`` (the reason says which). Applies
only to the configured ``action_classes``.
"""
from __future__ import annotations

from typing import NotRequired, TypedDict

from agent_action_capsule import json_digest
from agent_action_capsule.canonical import FloatInDigestError, UnsafeIntegerError

from ..action import Action
from ..capsule import ConstraintOutcome, NotApplicableEvidence, not_applicable_evidence
from ..plan import PlanDefinitionError, parse_plan_definition
from .base import CheckOutcome
from .plan_containment import check_plan_containment

__all__ = ["TaskAuthorityBody", "check_task_authority"]

_CHECK_ID = "task_authority"
_METHOD = "bound_plan_containment_v0"


class PreconditionBody(TypedDict):
    action: str
    citing: str


class TaskAuthorityBody(TypedDict):
    """The body of a sealed task-authority record: a plan in
    ``guards/plan.py``'s shape. It arrives as decoded JSON, so nothing about
    it is trusted until its digest matches and ``parse_plan_definition``
    accepts it."""

    outcome_id: str
    allowed_actions: list[str]
    preconditions: list[PreconditionBody]
    binding: NotRequired[dict[str, str]]
    window: NotRequired[str]


class TaskAuthorityEvidence(TypedDict):
    task_authority_ref: str
    plan_digest: str
    containment: str


def _outcome(result: str, reason: str, evidence: TaskAuthorityEvidence | NotApplicableEvidence) -> CheckOutcome:
    return CheckOutcome(
        constraint=ConstraintOutcome(
            id=_CHECK_ID, result=result, reason=reason, evidence=evidence, check_type="policy", method=_METHOD
        )
    )


def _unread(reason: str) -> CheckOutcome:
    return _outcome("n/a", f"{reason}; the task's bounds could not be read",
                    not_applicable_evidence(_CHECK_ID, in_scope=True, missing_field="task_authority"))


def check_task_authority(
    action: Action, task_authority: TaskAuthorityBody | None, *, action_classes: list[str]
) -> CheckOutcome:
    if action.action_class not in action_classes:
        return _outcome("n/a", "the rule is not configured for this action class",
                        not_applicable_evidence(_CHECK_ID, in_scope=False))
    ref = action.task_authority_ref
    if ref is None:
        return _outcome("n/a", "the action carries no task_authority_ref; the task's bounds could not be checked",
                        not_applicable_evidence(_CHECK_ID, in_scope=True, missing_field="task_authority_ref"))
    if task_authority is None:
        return _unread("no task-authority body was supplied")
    try:
        bound = json_digest(task_authority) == ref
    except (FloatInDigestError, UnsafeIntegerError) as exc:
        return _unread(f"the supplied task-authority body has no digest ({exc})")
    if not bound:
        return _unread("the supplied task-authority body is not the record task_authority_ref names")
    try:
        plan = parse_plan_definition(task_authority)
    except PlanDefinitionError as exc:
        return _unread(f"the task-authority record is not a plan ({exc})")
    containment = check_plan_containment(action, plan).constraint
    evidence = TaskAuthorityEvidence(
        task_authority_ref=ref, plan_digest=plan.definition_digest(), containment=containment.result
    )
    if containment.result == "pass":
        return _outcome("pass", "the action is inside the task authority it cites", evidence)
    return _outcome("fail", f"the action is not inside the task authority it cites: {containment.reason}", evidence)
