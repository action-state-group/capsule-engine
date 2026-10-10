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

With ``statement_definition`` (``required_disclosure/1.1.0``, its fold
``disclosure.statement_seen/1.0.0``), a class also counts as stated when the
action's deal (``Action.deal_id``) already holds a statement of it: a record
``report/replay.py``'s ``statement_record`` wrote for a claim the seller's
agent made in that deal before the action, read from the bundle in a replay
and from the check input's history live. Both counts are in the evidence,
the deal's beside the counterparty's. The deal stands for the counterparty
here: a seller's deal is one thread with one buyer. An action with neither a
target nor a deal is not applicable, as one with no target is without it;
one with a target and no deal counts the counterparty's statements only, and
one with a deal and no target the deal's only.

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
from ..statements import STATED
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


class StatementScope(TypedDict):
    operator: str
    target: str | None
    deal_id: str | None


class StatementEvidence(TypedDict):
    """``required_disclosure/1.1.0``'s evidence: each fold's digest and
    counts, and the classes neither counted."""

    fold: str
    statement_fold: str
    scope: StatementScope
    prior_counts: dict[str, int]
    stated_counts: dict[str, int]
    missing_classes: list[str]


def require_known_classes(classes: list[str], representation_classes: list[str], *, what: str) -> None:
    unknown = [c for c in classes if c not in representation_classes]
    if unknown:
        raise ValueError(f"{what} names {unknown}, outside the closed set {representation_classes}")


def _outcome(
    result: str, reason: str, evidence: DisclosureEvidence | StatementEvidence | NotApplicableEvidence, method: str,
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
    statement_definition: FoldDefinition | None = None,
) -> CheckOutcome:
    require_known_classes(required_classes, representation_classes, what="required_classes")
    method = definition.fold_id
    if action.action_class not in action_classes:
        return _outcome("n/a", "the rule is not configured for this action class",
                        not_applicable_evidence(_CHECK_ID, in_scope=False), method)
    if statement_definition is not None:
        return _check_with_statements(action, ledger, definition, statement_definition, required_classes)
    target = action.target
    if target is None:
        return _outcome("n/a", "the action names no target; the counterparty could not be identified",
                        not_applicable_evidence(_CHECK_ID, in_scope=True, missing_field="target"), method)

    traces = _counted(definition, _to_target(action, ledger, target), required_classes)
    counts = _counts(traces, required_classes)
    missing = [cls for cls in required_classes if counts[cls] == 0]
    evidence = DisclosureEvidence(
        fold=definition.definition_digest(),
        scope=DisclosureScope(operator=action.operator, target=target),
        prior_counts=counts,
        missing_classes=missing,
    )
    return _decided(missing, evidence, method, traces)


def _to_target(action: Action, ledger: LedgerAPI, target: str) -> list[dict]:
    """This operator's records addressed to ``target``. ``ScanQuery.
    counterparty`` is the ledger's filter on ``operator``; the ledger has no
    filter on target, so the records are selected here."""
    return [
        r.capsule for r in ledger.scan(ScanQuery(counterparty=action.operator))
        if (r.capsule.get("asg_payload") or {}).get("target") == target
    ]


def _in_deal(action: Action, ledger: LedgerAPI, deal_id: str) -> list[dict]:
    """This operator's statement records in the deal ``deal_id``."""
    return [
        r.capsule for r in ledger.scan(ScanQuery(counterparty=action.operator))
        if (((r.capsule.get("asg_payload") or {}).get(STATED)) or {}).get("deal_id") == deal_id
    ]


def _counted(definition: FoldDefinition, records: list[dict], classes: list[str]) -> tuple[EvaluationTrace, ...]:
    return tuple(evaluate_one(definition, records, key_value=cls) for cls in classes)


def _counts(traces: tuple[EvaluationTrace, ...], classes: list[str]) -> dict[str, int]:
    return {cls: trace.result or 0 for cls, trace in zip(classes, traces, strict=True)}


def _decided(
    missing: list[str], evidence: DisclosureEvidence | StatementEvidence, method: str,
    traces: tuple[EvaluationTrace, ...],
) -> CheckOutcome:
    if missing:
        return _outcome("fail", f"no accepted prior statement of {missing} to this counterparty: make the disclosure "
                                 "first or change the task authority; an approval does not waive it", evidence, method,
                        traces)
    return _outcome("pass", "every required class was stated to this counterparty", evidence, method, traces)


def _check_with_statements(
    action: Action, ledger: LedgerAPI, definition: FoldDefinition, statement_definition: FoldDefinition,
    required_classes: list[str],
) -> CheckOutcome:
    """``required_disclosure/1.1.0``: a class counts as stated to the
    counterparty's target or in the action's deal (module docstring)."""
    method = definition.fold_id
    target, deal_id = action.target, action.deal_id
    if target is None and deal_id is None:
        return _outcome("n/a", "the action names no target and no deal; the counterparty could not be identified",
                        not_applicable_evidence(_CHECK_ID, in_scope=True, missing_field="target"), method)
    told = _counted(definition, _to_target(action, ledger, target) if target is not None else [], required_classes)
    stated = _counted(statement_definition, _in_deal(action, ledger, deal_id) if deal_id is not None else [],
                      required_classes)
    prior, in_deal = _counts(told, required_classes), _counts(stated, required_classes)
    missing = [cls for cls in required_classes if prior[cls] == 0 and in_deal[cls] == 0]
    evidence = StatementEvidence(
        fold=definition.definition_digest(),
        statement_fold=statement_definition.definition_digest(),
        scope=StatementScope(operator=action.operator, target=target, deal_id=deal_id),
        prior_counts=prior,
        stated_counts=in_deal,
        missing_classes=missing,
    )
    return _decided(missing, evidence, method, told + stated)
