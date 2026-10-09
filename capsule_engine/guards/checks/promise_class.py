# SPDX-License-Identifier: Apache-2.0
"""promise_class check: a class of statement the agent may not make without
an approval.

Reads ``Action.representation_class``, one member of the closed set
``representation_classes``. When it is one of ``never_without_approval``
the check passes only on a bound approval: ``Action.authorized_by`` is the
SHA-256 digest of the WHOLE sealed approval record, that record is supplied
beside the action for one decision (``GuardEngine.check(...,
authorization_record=...)``), the engine recomputes its digest itself
(``authorization_record_digest``) and reads it ONLY when that equals the
reference, and the record's ``body.representation_class`` (``CLASS_PATH``)
names this action's class. Anything less fails: no reference, no record, a
record the reference does not bind, a record with no digest, or an approval
for another class. Never ``n/a``: an unreadable approval is no approval.

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

__all__ = [
    "CLASS_PATH",
    "AuthorizationBody",
    "AuthorizationRecord",
    "authorization_record_digest",
    "check_promise_class",
]

_CHECK_ID = "promise_class"
_METHOD = "bound_approval_v0"

# Where the approved class sits inside the sealed record, one member name per level.
CLASS_PATH: tuple[str, ...] = ("body", "representation_class")


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
    authorized_by: str | None
    approval_bound: bool


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


def _outcome(result: str, reason: str, evidence: PromiseEvidence | NotApplicableEvidence) -> CheckOutcome:
    return CheckOutcome(
        constraint=ConstraintOutcome(
            id=_CHECK_ID, result=result, reason=reason, evidence=evidence, check_type="policy", method=_METHOD
        )
    )


def check_promise_class(
    action: Action,
    record: AuthorizationRecord | None,
    *,
    representation_classes: list[str],
    never_without_approval: list[str],
    action_classes: list[str],
) -> CheckOutcome:
    require_known_classes(never_without_approval, representation_classes, what="never_without_approval")
    if action.action_class not in action_classes:
        return _outcome("n/a", "the rule is not configured for this action class",
                        not_applicable_evidence(_CHECK_ID, in_scope=False))
    cls = action.representation_class
    if cls is None:
        return _outcome("n/a", "the action carries no representation_class; the statement could not be classed",
                        not_applicable_evidence(_CHECK_ID, in_scope=True, missing_field="representation_class"))
    recognised = cls in representation_classes
    requires_approval = cls in never_without_approval
    try:
        approved = _approved_class(action, record)
    except _Unapproved as exc:
        approved, why = None, str(exc)
    else:
        why = f"the approval is for {approved}, not {cls}"
    bound = approved == cls
    evidence = PromiseEvidence(representation_class=cls, recognised=recognised, requires_approval=requires_approval,
                               authorized_by=action.authorized_by, approval_bound=bound)
    if not recognised:
        return _outcome("fail", f"representation_class {cls!r} is not in {representation_classes}", evidence)
    if requires_approval and not bound:
        return _outcome("fail", f"a {cls} statement needs a bound approval: {why}", evidence)
    if requires_approval:
        return _outcome("pass", f"the {cls} statement cites a bound approval for {cls}", evidence)
    return _outcome("pass", f"a {cls} statement needs no approval", evidence)
