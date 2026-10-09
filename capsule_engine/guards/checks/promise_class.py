# SPDX-License-Identifier: Apache-2.0
"""promise_class check: a class of statement the agent may not make without
an approval.

Reads ``Action.representation_class``, one member of the closed set
``representation_classes``. When it is one of ``never_without_approval``
the check fails unless ``Action.authorized_by`` is a SHA-256 digest (64
lowercase hex), the reference to the sealed approval record. Presence and
shape only: the check does not open the approval or check that it covers
this class, so a fabricated reference of the right shape passes here; the
approval record is what a verifier opens. A class outside the closed set
fails closed. Applies only to the configured ``action_classes``.
"""
from __future__ import annotations

import re
from typing import TypedDict

from ..action import Action
from ..capsule import ConstraintOutcome, NotApplicableEvidence, not_applicable_evidence
from .base import CheckOutcome
from .required_disclosure import require_known_classes

__all__ = ["check_promise_class"]

_CHECK_ID = "promise_class"
_METHOD = "set_membership_v0"
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


class PromiseEvidence(TypedDict):
    representation_class: str
    recognised: bool
    requires_approval: bool
    authorized_by: str | None


def _outcome(result: str, reason: str, evidence: PromiseEvidence | NotApplicableEvidence) -> CheckOutcome:
    return CheckOutcome(
        constraint=ConstraintOutcome(
            id=_CHECK_ID, result=result, reason=reason, evidence=evidence, check_type="policy", method=_METHOD
        )
    )


def check_promise_class(
    action: Action, *, representation_classes: list[str], never_without_approval: list[str], action_classes: list[str]
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
    ref = action.authorized_by
    approval = ref if ref is not None and _DIGEST.match(ref) else None
    evidence = PromiseEvidence(representation_class=cls, recognised=recognised, requires_approval=requires_approval,
                               authorized_by=approval)
    if not recognised:
        return _outcome("fail", f"representation_class {cls!r} is not in {representation_classes}", evidence)
    if requires_approval and approval is None:
        return _outcome("fail", f"a {cls} statement needs an approval and the action cites none", evidence)
    if requires_approval:
        return _outcome("pass", f"the {cls} statement cites an approval", evidence)
    return _outcome("pass", f"a {cls} statement needs no approval", evidence)
