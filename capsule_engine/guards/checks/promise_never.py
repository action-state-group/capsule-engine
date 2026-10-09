# SPDX-License-Identifier: Apache-2.0
"""promise_never check: a class of statement the agent never makes.

Reads ``Action.representation_class``, one member of the closed set
``representation_classes``. A class in ``never`` fails whenever the action
makes that statement. Nothing the action carries clears it: not
``authorized_by``, not a bound approval record, not the task authority. The
only way to lift it is a change to the pinned rule set that removes the
class from ``never``. ``authorized_by`` is recorded in the evidence so a
reader sees an approval was cited and did not count.

A class outside the closed set fails closed. Applies only to the configured
``action_classes``.
"""
from __future__ import annotations

from typing import TypedDict

from ..action import Action
from ..capsule import ConstraintOutcome, NotApplicableEvidence, not_applicable_evidence
from .base import CheckOutcome
from .required_disclosure import require_known_classes

__all__ = ["check_promise_never"]

_CHECK_ID = "promise_never"
_METHOD = "class_membership_v0"


class NeverEvidence(TypedDict):
    representation_class: str
    recognised: bool
    never: bool
    authorized_by: str | None


def _outcome(result: str, reason: str, evidence: NeverEvidence | NotApplicableEvidence) -> CheckOutcome:
    return CheckOutcome(
        constraint=ConstraintOutcome(
            id=_CHECK_ID, result=result, reason=reason, evidence=evidence, check_type="policy", method=_METHOD
        )
    )


def check_promise_never(
    action: Action, *, representation_classes: list[str], never: list[str], action_classes: list[str]
) -> CheckOutcome:
    require_known_classes(never, representation_classes, what="never")
    if action.action_class not in action_classes:
        return _outcome("n/a", "the rule is not configured for this action class",
                        not_applicable_evidence(_CHECK_ID, in_scope=False))
    cls = action.representation_class
    if cls is None:
        return _outcome("n/a", "the action carries no representation_class; the statement could not be classed",
                        not_applicable_evidence(_CHECK_ID, in_scope=True, missing_field="representation_class"))
    recognised = cls in representation_classes
    is_never = cls in never
    evidence = NeverEvidence(representation_class=cls, recognised=recognised, never=is_never,
                             authorized_by=action.authorized_by)
    if not recognised:
        return _outcome("fail", f"representation_class {cls!r} is not in {representation_classes}", evidence)
    if is_never:
        return _outcome("fail", f"the agent never makes a {cls} statement; no approval clears it, only a change "
                                "to the rule set", evidence)
    return _outcome("pass", f"a {cls} statement is not one the agent never makes", evidence)
