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
here: capsulectl opens one thread per buyer. A statement record names no
counterparty, so a counterparty that changes within a deal is not seen here
(``counterparty_identity_change`` reads that). An action with neither a
target nor a deal is not applicable, as one with no target is without it;
one with a target and no deal counts the counterparty's statements only, and
one with a deal and no target the deal's only.

With ``count_listing_classes`` (``required_disclosure/1.2.0``), a class also
counts as stated when it is in ``Action.listing_classes``: the classes every
live listing of the sale stated when the check was made, as capsulectl sealed
them in the check's body (the intersection over the latest post per platform).
A listing is the agent's words to every buyer, so a class it states is stated
to this one too. The value is read from the check, never recomputed: a re-post
changes later checks only. With no value (no post) or an empty one, a listing
counts for nothing, and the class needs one of the other two paths. The
evidence lists the value beside the two counts.

``required_classes`` must come from the closed set ``representation_classes``;
a class outside it is a configuration error and raises ``ValueError``.
"""
from __future__ import annotations

from typing import TypedDict

from ...folds.definition import FoldDefinition
from ...folds.engine import EvaluationTrace, evaluate_one
from ..action import Action
from ..capsule import ConstraintOutcome, NotApplicableEvidence, not_applicable_evidence
from ..history import History, Query
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


class ListingEvidence(StatementEvidence):
    """``required_disclosure/1.2.0``'s evidence: 1.1.0's, and the listing's
    classes the action carried (``None`` with no post)."""

    listing_classes: list[str] | None


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
    history: History,
    *,
    definition: FoldDefinition,
    representation_classes: list[str],
    required_classes: list[str],
    action_classes: list[str],
    statement_definition: FoldDefinition | None = None,
    count_listing_classes: bool = False,
) -> CheckOutcome:
    require_known_classes(required_classes, representation_classes, what="required_classes")
    if count_listing_classes and statement_definition is None:
        raise ValueError("count_listing_classes goes with statement_fold_id: required_disclosure/1.2.0 builds on 1.1.0")
    method = definition.fold_id
    if action.action_class not in action_classes:
        return _outcome("n/a", "the rule is not configured for this action class",
                        not_applicable_evidence(_CHECK_ID, in_scope=False), method)
    if statement_definition is not None:
        return _check_with_statements(action, history, definition, statement_definition, required_classes,
                                      listing=count_listing_classes)
    target = action.target
    if target is None:
        return _outcome("n/a", "the action names no target; the counterparty could not be identified",
                        not_applicable_evidence(_CHECK_ID, in_scope=True, missing_field="target"), method)

    traces = _counted(definition, _to_target(action, history, target), required_classes)
    counts = _counts(traces, required_classes)
    missing = [cls for cls in required_classes if counts[cls] == 0]
    evidence = DisclosureEvidence(
        fold=definition.definition_digest(),
        scope=DisclosureScope(operator=action.operator, target=target),
        prior_counts=counts,
        missing_classes=missing,
    )
    return _decided(missing, evidence, method, traces)


def _to_target(action: Action, history: History, target: str) -> list[dict]:
    """This operator's records addressed to ``target``."""
    return [
        r.capsule for r in history.find(Query(where={"operator": action.operator, "asg_payload.target": target}))
        if (r.capsule.get("asg_payload") or {}).get("target") == target
    ]


def _in_deal(action: Action, history: History, deal_id: str) -> list[dict]:
    """This operator's statement records in the deal ``deal_id``."""
    return [
        r.capsule for r in history.find(Query(where={"operator": action.operator, f"asg_payload.{STATED}.deal_id": deal_id}))
        if (((r.capsule.get("asg_payload") or {}).get(STATED)) or {}).get("deal_id") == deal_id
    ]


def _counted(definition: FoldDefinition, records: list[dict], classes: list[str]) -> tuple[EvaluationTrace, ...]:
    return tuple(evaluate_one(definition, records, key_value=cls) for cls in classes)


def _counts(traces: tuple[EvaluationTrace, ...], classes: list[str]) -> dict[str, int]:
    return {cls: trace.result or 0 for cls, trace in zip(classes, traces, strict=True)}


def _decided(
    missing: list[str], evidence: DisclosureEvidence | StatementEvidence | ListingEvidence, method: str,
    traces: tuple[EvaluationTrace, ...],
) -> CheckOutcome:
    if missing:
        return _outcome("fail", f"no accepted prior statement of {missing} to this counterparty: make the disclosure "
                                 "first or change the task authority; an approval does not waive it", evidence, method,
                        traces)
    return _outcome("pass", "every required class was stated to this counterparty", evidence, method, traces)


def _check_with_statements(
    action: Action, history: History, definition: FoldDefinition, statement_definition: FoldDefinition,
    required_classes: list[str], *, listing: bool = False,
) -> CheckOutcome:
    """``required_disclosure/1.1.0``: a class counts as stated to the
    counterparty's target or in the action's deal; with ``listing`` (1.2.0),
    also when the sale's listings in force state it (module docstring)."""
    method = definition.fold_id
    target, deal_id = action.target, action.deal_id
    if target is None and deal_id is None:
        return _outcome("n/a", "the action names no target and no deal; the counterparty could not be identified",
                        not_applicable_evidence(_CHECK_ID, in_scope=True, missing_field="target"), method)
    told = _counted(definition, _to_target(action, history, target) if target is not None else [], required_classes)
    stated = _counted(statement_definition, _in_deal(action, history, deal_id) if deal_id is not None else [],
                      required_classes)
    prior, in_deal = _counts(told, required_classes), _counts(stated, required_classes)
    listed = action.listing_classes if listing and action.listing_classes is not None else ()
    missing = [cls for cls in required_classes if prior[cls] == 0 and in_deal[cls] == 0 and cls not in listed]
    scope = StatementScope(operator=action.operator, target=target, deal_id=deal_id)
    if listing:
        evidence: StatementEvidence = ListingEvidence(
            fold=definition.definition_digest(),
            statement_fold=statement_definition.definition_digest(),
            scope=scope,
            prior_counts=prior,
            stated_counts=in_deal,
            missing_classes=missing,
            listing_classes=None if action.listing_classes is None else list(action.listing_classes),
        )
    else:
        evidence = StatementEvidence(
            fold=definition.definition_digest(),
            statement_fold=statement_definition.definition_digest(),
            scope=scope,
            prior_counts=prior,
            stated_counts=in_deal,
            missing_classes=missing,
        )
    return _decided(missing, evidence, method, told + stated)
