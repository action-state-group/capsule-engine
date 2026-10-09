# SPDX-License-Identifier: Apache-2.0
"""required_disclosure check: has each required class of statement been
made to this counterparty before the action?

For each class in ``required_classes``, the count is the
``disclosure.class_seen`` fold's own result, keyed by
``asg_payload.representation_class``, over this operator's records addressed
to ``Action.target``. Only accepted actions that were not dry runs count. A
zero for any required class fails the check; the evidence lists each class's
count and the classes still missing. It checks that a statement of the class
was recorded, never what it said or whether it was true. Applies only to
the configured ``action_classes``.

A failure means the action cannot proceed as constructed: make the
disclosure first, or change the task authority. It is never cleared by an
approval: this check reads no approval record and ignores
``Action.authorized_by``, because a one-shot approval does not waive a
statement the person already required.

``required_classes`` must come from the closed set ``representation_classes``;
a class outside it is a configuration error and raises ``ValueError``.
"""
from __future__ import annotations

from typing import TypedDict

from capsule_ledger.ledger.api import LedgerAPI, ScanQuery

from ...folds.definition import FoldDefinition
from ...folds.engine import EvaluationTrace, evaluate_one
from ..action import Action
from ..capsule import ConstraintOutcome, NotApplicableEvidence, not_applicable_evidence
from .base import CheckOutcome

__all__ = ["check_required_disclosure", "require_known_classes"]

_CHECK_ID = "required_disclosure"


class DisclosureScope(TypedDict):
    operator: str
    target: str


class DisclosureEvidence(TypedDict):
    fold: str
    scope: DisclosureScope
    prior_counts: dict[str, int]
    missing_classes: list[str]


def require_known_classes(classes: list[str], representation_classes: list[str], *, what: str) -> None:
    unknown = [c for c in classes if c not in representation_classes]
    if unknown:
        raise ValueError(f"{what} names {unknown}, outside the closed set {representation_classes}")


def _outcome(
    result: str, reason: str, evidence: DisclosureEvidence | NotApplicableEvidence, method: str,
    traces: tuple[EvaluationTrace, ...] = (),
) -> CheckOutcome:
    return CheckOutcome(
        constraint=ConstraintOutcome(
            id=_CHECK_ID, result=result, reason=reason, evidence=evidence, check_type="policy", method=method
        ),
        fold_envelopes=tuple(trace.to_envelope() for trace in traces),
    )


def check_required_disclosure(
    action: Action,
    ledger: LedgerAPI,
    *,
    definition: FoldDefinition,
    representation_classes: list[str],
    required_classes: list[str],
    action_classes: list[str],
) -> CheckOutcome:
    require_known_classes(required_classes, representation_classes, what="required_classes")
    method = definition.fold_id
    if action.action_class not in action_classes:
        return _outcome("n/a", "the rule is not configured for this action class",
                        not_applicable_evidence(_CHECK_ID, in_scope=False), method)
    target = action.target
    if target is None:
        return _outcome("n/a", "the action names no target; the counterparty could not be identified",
                        not_applicable_evidence(_CHECK_ID, in_scope=True, missing_field="target"), method)

    # ``ScanQuery.counterparty`` is the ledger's filter on ``operator``; the
    # ledger has no filter on target, so the records are selected here.
    records = [
        r.capsule for r in ledger.scan(ScanQuery(counterparty=action.operator))
        if (r.capsule.get("asg_payload") or {}).get("target") == target
    ]
    traces = tuple(evaluate_one(definition, records, key_value=cls) for cls in required_classes)
    counts = {cls: trace.result or 0 for cls, trace in zip(required_classes, traces, strict=True)}
    missing = [cls for cls in required_classes if counts[cls] == 0]
    evidence = DisclosureEvidence(
        fold=definition.definition_digest(),
        scope=DisclosureScope(operator=action.operator, target=target),
        prior_counts=counts,
        missing_classes=missing,
    )
    if missing:
        return _outcome("fail", f"no accepted prior statement of {missing} to this counterparty: make the disclosure "
                                 "first or change the task authority; an approval does not waive it", evidence, method,
                        traces)
    return _outcome("pass", "every required class was stated to this counterparty", evidence, method, traces)
