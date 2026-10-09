# SPDX-License-Identifier: Apache-2.0
"""promise_requires_approval check: a class of statement the agent may make
only inside its task authority or with a one-shot approval.

Reads ``Action.representation_class``, one member of the closed set
``representation_classes``. A class outside ``requires_approval`` passes. A
class inside it passes on either of two bases, named in the evidence's
``authority_basis``:

- ``task_authority_ref``: the task authority already allows it. The sealed
  task-authority record is read only when the engine's own digest of it
  equals ``Action.task_authority_ref``
  (``task_authority.bind_task_authority_record``), and its
  ``body.authorized_representation_classes`` (``AUTHORIZED_PATH``) lists the
  class. No approval event is needed.
- ``authorized_by``: a bound approval for a statement outside the task
  authority. ``Action.authorized_by`` is the SHA-256 digest of the WHOLE
  sealed approval record, that record is supplied beside the action for one
  decision (``GuardEngine.check(..., authorization_record=...)``), the
  engine recomputes its digest itself (``authorization_record_digest``) and
  reads it ONLY when that equals the reference, and the record's
  ``body.representation_class`` (``CLASS_PATH``) names this action's class.

Anything less fails: no reference, no record, a record the reference does
not bind, a record with no digest, or an approval for another class. Never
``n/a``: an unreadable approval is no approval. A class no approval may
clear belongs to ``promise_never``, not here.

A class outside the closed set fails closed. Applies only to the configured
``action_classes``.
"""
from __future__ import annotations

from typing import TypedDict

from agent_action_capsule import json_digest
from agent_action_capsule.canonical import FloatInDigestError, UnsafeIntegerError

from ..action import Action
from ..capsule import ConstraintOutcome, NotApplicableEvidence, not_applicable_evidence
from .base import CheckOutcome
from .required_disclosure import require_known_classes
from .task_authority import TaskAuthorityRecord, UnboundRecord, bind_task_authority_record

__all__ = [
    "AUTHORIZED_PATH",
    "CLASS_PATH",
    "AuthorizationBody",
    "AuthorizationRecord",
    "authorization_record_digest",
    "check_promise_requires_approval",
]

_CHECK_ID = "promise_requires_approval"
_METHOD = "authority_or_bound_approval_v0"

# Where the approved class sits inside the sealed approval record, one member
# name per level.
CLASS_PATH: tuple[str, ...] = ("body", "representation_class")
# Where the classes the task authority allows sit inside the sealed
# task-authority record.
AUTHORIZED_PATH: tuple[str, ...] = ("body", "authorized_representation_classes")


class AuthorizationBody(TypedDict):
    representation_class: str


class AuthorizationRecord(TypedDict):
    """The whole sealed approval record as decoded JSON, digested
    byte-for-byte as supplied. Only the approved class is named here; any
    other member the producer seals is covered by the digest and otherwise
    unread."""

    body: AuthorizationBody


class PromiseEvidence(TypedDict):
    representation_class: str
    recognised: bool
    requires_approval: bool
    authority_basis: str | None
    task_authority_ref: str | None
    authorized_by: str | None


def authorization_record_digest(record: AuthorizationRecord) -> str:
    """The digest ``authorized_by`` carries for ``record``: SHA-256 over the
    record's JCS bytes."""
    return json_digest(record)


class _Unapproved(Exception):
    """The supplied approval does not approve this statement; the message says why."""


def _approved_class(action: Action, record: AuthorizationRecord | None) -> str:
    """The class the bound approval names. Raises ``_Unapproved`` when the
    record is missing, unbound or names no class."""
    ref = action.authorized_by
    if ref is None:
        raise _Unapproved("the action cites no approval (authorized_by)")
    if record is None:
        raise _Unapproved("no approval record was supplied for authorized_by")
    try:
        bound = authorization_record_digest(record) == ref
    except (FloatInDigestError, UnsafeIntegerError) as exc:
        raise _Unapproved(f"the supplied approval record has no digest ({exc})") from exc
    if not bound:
        raise _Unapproved("ref mismatch: the supplied approval record is not the record authorized_by names")
    node: object = record
    for name in CLASS_PATH:
        # Decoded JSON: each level is checked before it is read.
        if not isinstance(node, dict) or name not in node:
            raise _Unapproved(f"the approval record names no class at {'.'.join(CLASS_PATH)}")
        node = node[name]
    if not isinstance(node, str):
        raise _Unapproved(f"the approval record's {'.'.join(CLASS_PATH)} is not one class")
    return node


def _authority_allows(action: Action, record: TaskAuthorityRecord | None, cls: str) -> bool:
    """Whether the bound task authority lists ``cls``. An unbound or silent
    record allows nothing; the approval is then the only basis left."""
    try:
        _, bound = bind_task_authority_record(action, record)
    except UnboundRecord:
        # No bound authority is not an error here: the approval is then the
        # only basis, and a failure's reason names what the approval lacked.
        return False
    node: object = bound
    for name in AUTHORIZED_PATH:
        # Decoded JSON: each level is checked before it is read.
        if not isinstance(node, dict) or name not in node:
            return False
        node = node[name]
    return isinstance(node, list) and cls in node


def _outcome(result: str, reason: str, evidence: PromiseEvidence | NotApplicableEvidence) -> CheckOutcome:
    return CheckOutcome(
        constraint=ConstraintOutcome(
            id=_CHECK_ID, result=result, reason=reason, evidence=evidence, check_type="policy", method=_METHOD
        )
    )


def check_promise_requires_approval(
    action: Action,
    task_authority_record: TaskAuthorityRecord | None,
    authorization_record: AuthorizationRecord | None,
    *,
    representation_classes: list[str],
    requires_approval: list[str],
    action_classes: list[str],
) -> CheckOutcome:
    require_known_classes(requires_approval, representation_classes, what="requires_approval")
    if action.action_class not in action_classes:
        return _outcome("n/a", "the rule is not configured for this action class",
                        not_applicable_evidence(_CHECK_ID, in_scope=False))
    cls = action.representation_class
    if cls is None:
        return _outcome("n/a", "the action carries no representation_class; the statement could not be classed",
                        not_applicable_evidence(_CHECK_ID, in_scope=True, missing_field="representation_class"))
    recognised = cls in representation_classes
    needs = cls in requires_approval

    def evidence(basis: str | None) -> PromiseEvidence:
        return PromiseEvidence(representation_class=cls, recognised=recognised, requires_approval=needs,
                               authority_basis=basis, task_authority_ref=action.task_authority_ref,
                               authorized_by=action.authorized_by)

    if not recognised:
        return _outcome("fail", f"representation_class {cls!r} is not in {representation_classes}", evidence(None))
    if not needs:
        return _outcome("pass", f"a {cls} statement needs no approval", evidence(None))
    if _authority_allows(action, task_authority_record, cls):
        return _outcome("pass", f"the task authority allows a {cls} statement", evidence("task_authority_ref"))
    try:
        approved = _approved_class(action, authorization_record)
    except _Unapproved as exc:
        why = str(exc)
    else:
        if approved == cls:
            return _outcome("pass", f"the {cls} statement cites a bound approval for {cls}",
                            evidence("authorized_by"))
        why = f"the approval is for {approved}, not {cls}"
    return _outcome("fail", f"a {cls} statement is outside the task authority and needs a bound approval: {why}",
                    evidence(None))
