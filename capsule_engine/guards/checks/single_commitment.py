# SPDX-License-Identifier: Apache-2.0
"""single_commitment check: one sale, one accepted commitment.

A seller may negotiate one sale with several buyers, one thread each. Every
thread cites the same ``task_authority_ref``, and ``item_ref`` names what is
sold. Once an acceptance is sealed for that task authority and item, a
commitment for the same task authority and item fails unless BOTH hold:
it cites that acceptance (``cited_mandate_capsule_id``, which
``verify_before_dispatch`` re-verifies) and it is addressed to the
counterparty that accepted (``target`` equal to the acceptance's sealed
``target``). Citing the acceptance alone is not enough: another buyer's
thread could cite it too.

The acceptance is the first record in ledger order, among this operator's,
whose ``action_class`` is in ``acceptance_classes``, whose decision was
accept and which was not a dry run, carrying the same ``task_authority_ref``
and ``item_ref``. Both are opaque references compared for equality; the
check never reads what they refer to.

Applies only to the configured ``commit_classes``. A commitment missing
``task_authority_ref`` or ``item_ref`` cannot be placed in a sale, so it is
``n/a`` naming the field, never a pass. A failure is an integrity failure:
the check is not escalatable, so the engine refuses rather than asks.
"""
from __future__ import annotations

from typing import TypedDict

from capsule_ledger.ledger.api import LedgerAPI, ScanQuery

from ..action import Action
from ..capsule import ConstraintOutcome, NotApplicableEvidence, not_applicable_evidence
from .base import CheckOutcome

__all__ = ["check_single_commitment"]

_CHECK_ID = "single_commitment"
_METHOD = "first_sealed_acceptance_v0"


class CommitmentEvidence(TypedDict):
    task_authority_ref: str
    item_ref: str
    acceptance_capsule_id: str | None
    acceptance_counterparty: str | None
    counterparty: str | None
    cites_acceptance: bool


class _Acceptance(TypedDict):
    capsule_id: str
    counterparty: str | None


def _outcome(result: str, reason: str, evidence: CommitmentEvidence | NotApplicableEvidence) -> CheckOutcome:
    return CheckOutcome(
        constraint=ConstraintOutcome(
            id=_CHECK_ID, result=result, reason=reason, evidence=evidence, check_type="policy", method=_METHOD
        )
    )


def _first_acceptance(
    action: Action, ledger: LedgerAPI, acceptance_classes: list[str], task_authority_ref: str, item_ref: str
) -> _Acceptance | None:
    # ``ScanQuery.counterparty`` is the ledger's filter on ``operator``.
    for record in ledger.scan(ScanQuery(counterparty=action.operator)):
        capsule = record.capsule
        payload = capsule.get("asg_payload") or {}
        if payload.get("action_class") not in acceptance_classes:
            continue
        if (capsule.get("disposition") or {}).get("decision") != "accept":
            continue
        if (payload.get("checkpoint") or {}).get("dry_run") is True:
            continue
        if payload.get("task_authority_ref") != task_authority_ref or payload.get("item_ref") != item_ref:
            continue
        return _Acceptance(capsule_id=record.capsule_id, counterparty=payload.get("target"))
    return None


def check_single_commitment(
    action: Action, ledger: LedgerAPI, *, acceptance_classes: list[str], commit_classes: list[str]
) -> CheckOutcome:
    if action.action_class not in commit_classes:
        return _outcome("n/a", "the rule is not configured for this action class",
                        not_applicable_evidence(_CHECK_ID, in_scope=False))
    if action.task_authority_ref is None:
        return _outcome("n/a", "the action cites no task authority; the sale could not be identified",
                        not_applicable_evidence(_CHECK_ID, in_scope=True, missing_field="task_authority_ref"))
    if action.item_ref is None:
        return _outcome("n/a", "the action names no item; the sale could not be identified",
                        not_applicable_evidence(_CHECK_ID, in_scope=True, missing_field="item_ref"))

    acceptance = _first_acceptance(action, ledger, acceptance_classes, action.task_authority_ref, action.item_ref)
    if acceptance is None:
        return _outcome("pass", "no acceptance is sealed for this sale and item", CommitmentEvidence(
            task_authority_ref=action.task_authority_ref, item_ref=action.item_ref, acceptance_capsule_id=None,
            acceptance_counterparty=None, counterparty=action.target, cites_acceptance=False,
        ))
    cites = action.cited_mandate_capsule_id == acceptance["capsule_id"]
    same_counterparty = action.target is not None and action.target == acceptance["counterparty"]
    evidence = CommitmentEvidence(
        task_authority_ref=action.task_authority_ref,
        item_ref=action.item_ref,
        acceptance_capsule_id=acceptance["capsule_id"],
        acceptance_counterparty=acceptance["counterparty"],
        counterparty=action.target,
        cites_acceptance=cites,
    )
    if cites and same_counterparty:
        return _outcome("pass", "the commitment follows the acceptance, to the counterparty that accepted", evidence)
    return _outcome("fail", "an acceptance is already sealed for this sale and item; a commitment must cite it "
                            "and be addressed to the counterparty that accepted", evidence)
