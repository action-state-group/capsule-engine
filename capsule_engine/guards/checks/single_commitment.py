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

The outcome carries no reference value. A checker result is sealed into a
record that can reach a buyer's copy, so the reason and evidence never hold
``item_ref``, ``task_authority_ref``, a counterparty or the acceptance's
capsule id: any of them would link one buyer's thread to another's. The
evidence says only whether the sale has an acceptance; the seller's own
ledger keeps the acceptance record itself.
"""
from __future__ import annotations

from typing import TypedDict

from capsule_ledger.ledger.api import LedgerAPI, ScanQuery

from ..action import Action
from ..capsule import ConstraintOutcome, NotApplicableEvidence, not_applicable_evidence
from .base import CheckOutcome

__all__ = ["SealedAcceptance", "check_single_commitment", "first_sealed_acceptance"]

_CHECK_ID = "single_commitment"
_METHOD = "first_sealed_acceptance_v1"


class CommitmentEvidence(TypedDict):
    constraint_id: str
    sale_has_acceptance: bool


class SealedAcceptance(TypedDict):
    capsule_id: str
    counterparty: str | None


def _evidence(sale_has_acceptance: bool) -> CommitmentEvidence:
    return CommitmentEvidence(constraint_id=_CHECK_ID, sale_has_acceptance=sale_has_acceptance)


def _outcome(result: str, reason: str, evidence: CommitmentEvidence | NotApplicableEvidence) -> CheckOutcome:
    return CheckOutcome(
        constraint=ConstraintOutcome(
            id=_CHECK_ID, result=result, reason=reason, evidence=evidence, check_type="policy", method=_METHOD
        )
    )


def first_sealed_acceptance(
    operator: str, ledger: LedgerAPI, acceptance_classes: list[str], task_authority_ref: str, item_ref: str
) -> SealedAcceptance | None:
    """The sale's acceptance: the first record in ledger order, among
    ``operator``'s, whose class is in ``acceptance_classes``, whose decision
    was accept, which was not a dry run, and which carries the same
    ``task_authority_ref`` and ``item_ref``. Shared with
    ``release_on_acceptance``, so both read one acceptance per sale."""
    # ``ScanQuery.counterparty`` is the ledger's filter on ``operator``.
    for record in ledger.scan(ScanQuery(counterparty=operator)):
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
        return SealedAcceptance(capsule_id=record.capsule_id, counterparty=payload.get("target"))
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
        # An ignored input is named, never the value it was given.
        reason = ("the item_ref input was not in its agreed shape" if "item_ref" in action.ignored_inputs
                  else "the action names no item")
        return _outcome("n/a", f"{reason}; the sale could not be identified",
                        not_applicable_evidence(_CHECK_ID, in_scope=True, missing_field="item_ref"))

    acceptance = first_sealed_acceptance(
        action.operator, ledger, acceptance_classes, action.task_authority_ref, action.item_ref
    )
    if acceptance is None:
        return _outcome("pass", "no acceptance is sealed for this sale", _evidence(False))
    cites = action.cited_mandate_capsule_id == acceptance["capsule_id"]
    same_counterparty = action.target is not None and action.target == acceptance["counterparty"]
    if cites and same_counterparty:
        return _outcome("pass", "the commitment follows the acceptance, to the counterparty that accepted",
                        _evidence(True))
    return _outcome("fail", "this sale already has an accepted commitment", _evidence(True))
