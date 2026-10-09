# SPDX-License-Identifier: Apache-2.0
"""task_authority check: is the action inside the task authority it cites?

``Action.task_authority_ref`` is an opaque reference: the SHA-256 digest of
the WHOLE sealed task-authority record the action cites. That record is
supplied beside the action for one decision (``GuardEngine.check(...,
task_authority_record=...)``). The engine recomputes the record's digest
itself (``task_authority_record_digest``) and reads the record ONLY when it
equals the reference: a record the reference does not bind is never trusted,
and no producer-asserted binding is accepted in its place. The plan is then
taken from inside the bound record at ``PLAN_PATH``, in the existing shape
(``guards/plan.py`` ``PlanDefinition``: ``outcome_id``, ``allowed_actions``,
``preconditions``, optional ``binding``), and the plan's own containment
check (``plan_containment.py``) decides. There is no second plan vocabulary.

A missing record, a record the reference does not bind, a bound record with
no plan at ``PLAN_PATH``, and a plan that does not parse are the same case:
the bounds could not be read, so the rule records ``n/a`` naming
``task_authority_record`` (the reason says which). Applies only to the
configured ``action_classes``.
"""
from __future__ import annotations

from typing import NotRequired, TypedDict

from agent_action_capsule import json_digest
from agent_action_capsule.canonical import FloatInDigestError, UnsafeIntegerError

from ..action import Action
from ..capsule import ConstraintOutcome, NotApplicableEvidence, not_applicable_evidence
from ..plan import PlanDefinition, PlanDefinitionError, parse_plan_definition
from .base import CheckOutcome
from .plan_containment import check_plan_containment

__all__ = [
    "PLAN_PATH",
    "TaskAuthorityBody",
    "TaskAuthorityRecord",
    "UnboundRecord",
    "bind_task_authority_record",
    "check_task_authority",
    "task_authority_record_digest",
]

_CHECK_ID = "task_authority"
_METHOD = "bound_plan_containment_v0"
_INPUT = "task_authority_record"

# Where the plan sits inside the sealed record, one member name per level.
PLAN_PATH: tuple[str, ...] = ("body",)


class PreconditionBody(TypedDict):
    action: str
    citing: str


class TaskAuthorityBody(TypedDict):
    """The plan inside a sealed task-authority record, in ``guards/plan.py``'s
    shape. Nothing about it is trusted until the record's digest matches and
    ``parse_plan_definition`` accepts it."""

    outcome_id: str
    allowed_actions: list[str]
    preconditions: list[PreconditionBody]
    binding: NotRequired[dict[str, str]]
    window: NotRequired[str]
    min_total_minor: NotRequired[int]
    authorized_representation_classes: NotRequired[list[str]]


class TaskAuthorityRecord(TypedDict):
    """The whole sealed task-authority record as decoded JSON, digested
    byte-for-byte as supplied. Only the plan member is named here; any
    other member the producer seals is covered by the digest and otherwise
    unread."""

    body: TaskAuthorityBody


class TaskAuthorityEvidence(TypedDict):
    task_authority_ref: str
    plan_digest: str
    containment: str


def task_authority_record_digest(record: TaskAuthorityRecord) -> str:
    """The digest ``task_authority_ref`` carries for ``record``: SHA-256 over
    the record's JCS bytes."""
    return json_digest(record)


class _PlanUnreadable(Exception):
    """The bound record yields no plan; the message says why."""


def _plan_in(record: TaskAuthorityRecord) -> PlanDefinition:
    node = record
    for name in PLAN_PATH:
        # Decoded JSON: each level is checked before it is read.
        if not isinstance(node, dict) or name not in node:
            raise _PlanUnreadable(f"the task-authority record carries no plan at {'.'.join(PLAN_PATH)}")
        node = node[name]
    try:
        return parse_plan_definition(node)
    except PlanDefinitionError as exc:
        raise _PlanUnreadable(f"the plan in the task-authority record does not parse ({exc})") from exc


class UnboundRecord(Exception):
    """The record the action's ``task_authority_ref`` names cannot be read.
    ``missing_field`` names the input an in-scope n/a records."""

    def __init__(self, message: str, missing_field: str) -> None:
        self.missing_field = missing_field
        super().__init__(message)


def bind_task_authority_record(
    action: Action, record: TaskAuthorityRecord | None
) -> tuple[str, TaskAuthorityRecord]:
    """``(ref, record)`` once the engine's own digest of ``record`` equals
    ``action.task_authority_ref``. Raises ``UnboundRecord`` otherwise: a
    record the reference does not bind is never read."""
    ref = action.task_authority_ref
    if ref is None:
        raise UnboundRecord("the action carries no task_authority_ref; the task's bounds could not be checked",
                            "task_authority_ref")
    unread = "; the task's bounds could not be read"
    if record is None:
        raise UnboundRecord(f"no task-authority record was supplied{unread}", _INPUT)
    try:
        bound = task_authority_record_digest(record) == ref
    except (FloatInDigestError, UnsafeIntegerError) as exc:
        raise UnboundRecord(f"the supplied task-authority record has no digest ({exc}){unread}", _INPUT) from exc
    if not bound:
        raise UnboundRecord(
            f"ref mismatch: the supplied task-authority record is not the record task_authority_ref names{unread}",
            _INPUT,
        )
    return ref, record


def _outcome(result: str, reason: str, evidence: TaskAuthorityEvidence | NotApplicableEvidence) -> CheckOutcome:
    return CheckOutcome(
        constraint=ConstraintOutcome(
            id=_CHECK_ID, result=result, reason=reason, evidence=evidence, check_type="policy", method=_METHOD
        )
    )


def _unread(reason: str) -> CheckOutcome:
    return _outcome("n/a", f"{reason}; the task's bounds could not be read",
                    not_applicable_evidence(_CHECK_ID, in_scope=True, missing_field=_INPUT))


def check_task_authority(
    action: Action, record: TaskAuthorityRecord | None, *, action_classes: list[str]
) -> CheckOutcome:
    if action.action_class not in action_classes:
        return _outcome("n/a", "the rule is not configured for this action class",
                        not_applicable_evidence(_CHECK_ID, in_scope=False))
    try:
        ref, bound = bind_task_authority_record(action, record)
    except UnboundRecord as exc:
        return _outcome("n/a", str(exc),
                        not_applicable_evidence(_CHECK_ID, in_scope=True, missing_field=exc.missing_field))
    try:
        plan = _plan_in(bound)
    except _PlanUnreadable as exc:
        return _unread(str(exc))
    containment = check_plan_containment(action, plan).constraint
    evidence = TaskAuthorityEvidence(
        task_authority_ref=ref, plan_digest=plan.definition_digest(), containment=containment.result
    )
    if containment.result == "pass":
        return _outcome("pass", "the action is inside the task authority it cites", evidence)
    return _outcome("fail", f"the action is not inside the task authority it cites: {containment.reason}", evidence)
