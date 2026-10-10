# SPDX-License-Identifier: Apache-2.0
"""single_commitment check: one sale, one accepted commitment.

A seller may negotiate one sale with several buyers, one thread each.
``item_ref`` names the sale: 256 random bits capsulectl draws once per sale,
equal across its threads. Once an acceptance is sealed for that item, a
commitment for the same item fails unless it follows that acceptance, which
it does when BOTH hold: it cites the acceptance (``cited_mandate_capsule_id``,
which ``verify_before_dispatch`` re-verifies) and it is addressed to the
counterparty that accepted (``target`` equal to the acceptance's sealed
``target``); or when it is in the acceptance's own deal (``deal_id`` equal,
both set), since a sale's thread is one buyer's. Citing the acceptance alone
is not enough: another buyer's thread could cite it too.

The sale is never keyed on ``task_authority_ref``: capsulectl seals a task
authority per thread, salted, so two threads of one sale never share one.

The acceptance is the first record in ledger order, among this operator's,
whose ``action_class`` is in ``acceptance_classes``, whose decision was
accept and which was not a dry run, carrying the same ``item_ref``. It is an
opaque reference compared for equality; the check never reads what it
refers to.

On a ledger built from a live check's history (``report/live_history.py``)
the check refuses to pass when it cannot know: the history may be missing
acts (``incomplete``), an act of this sale could not be read (``unread``),
or an act of an acceptance class for this sale, before any accepted one,
seals no disposition (``no_disposition``), so whether it was accepted is
not known. Each is ``n/a``, in scope, naming ``history`` or ``disposition``
as the missing input, and fails closed: the engine refuses
(``CheckOutcome.fails_closed``). A decision is never inferred from an act's
``authority_basis``.

Applies only to the configured ``commit_classes``. A commitment missing
``task_authority_ref`` or ``item_ref`` cannot be placed in a sale, so it is
``n/a`` naming the field, never a pass. A failure is an integrity failure:
the check is not escalatable, so the engine refuses rather than asks.

The outcome carries no reference value. A checker result is sealed into a
record that can reach a buyer's copy, so the reason and evidence never hold
``item_ref``, ``task_authority_ref``, a counterparty, a deal or the
acceptance's capsule id: any of them would link one buyer's thread to
another's. The evidence says only whether the sale has an acceptance; the
seller's own ledger keeps the acceptance record itself.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict

from capsule_ledger.ledger.api import LedgerAPI, ScanQuery

from ..action import Action
from ..capsule import ConstraintOutcome, NotApplicableEvidence, not_applicable_evidence
from ..history_state import INCOMPLETE, NO_DISPOSITION, UNREAD, history_state
from .base import CheckOutcome

__all__ = ["SaleAcceptance", "SealedAcceptance", "check_single_commitment", "first_sealed_acceptance",
           "sale_acceptance"]

_CHECK_ID = "single_commitment"
_METHOD = "first_sealed_acceptance_v1"
_UNKNOWN_REASONS = {
    "history": "the history may be missing an act of this sale; whether it has an acceptance is not known",
    "disposition": ("an earlier commitment in this sale seals no disposition; whether it was accepted is not "
                    "known"),
}


class CommitmentEvidence(TypedDict):
    constraint_id: str
    sale_has_acceptance: bool


class SealedAcceptance(TypedDict):
    capsule_id: str
    counterparty: str | None
    deal_id: str | None


@dataclass(frozen=True)
class SaleAcceptance:
    """What the ledger says about a sale's acceptance: ``acceptance`` when
    one is sealed; else ``unknown`` names the input whose absence leaves it
    unknown (``history`` or ``disposition``), or is ``None`` when the sale
    has none."""

    acceptance: SealedAcceptance | None
    unknown: str | None = None


def _evidence(sale_has_acceptance: bool) -> CommitmentEvidence:
    return CommitmentEvidence(constraint_id=_CHECK_ID, sale_has_acceptance=sale_has_acceptance)


def _outcome(
    result: str, reason: str, evidence: CommitmentEvidence | NotApplicableEvidence, *, fails_closed: bool = False
) -> CheckOutcome:
    return CheckOutcome(
        constraint=ConstraintOutcome(
            id=_CHECK_ID, result=result, reason=reason, evidence=evidence, check_type="policy", method=_METHOD
        ),
        fails_closed=fails_closed,
    )


def sale_acceptance(operator: str, ledger: LedgerAPI, acceptance_classes: list[str], item_ref: str) -> SaleAcceptance:
    """The sale's acceptance: the first record in ledger order, among
    ``operator``'s, whose class is in ``acceptance_classes``, whose decision
    was accept, which was not a dry run, and which carries the same
    ``item_ref``. Unknown when, before it, an act of this sale could not be
    read or an act of an acceptance class seals no disposition, or when the
    ledger says its history may be missing acts (module docstring)."""
    found: SealedAcceptance | None = None
    unknown: str | None = None
    # ``ScanQuery.counterparty`` is the ledger's filter on ``operator``.
    for record in ledger.scan(ScanQuery(counterparty=operator)):
        capsule = record.capsule
        payload = capsule.get("asg_payload") or {}
        state = history_state(capsule)
        if state == INCOMPLETE:
            return SaleAcceptance(acceptance=None, unknown="history")
        if found is not None or unknown is not None or payload.get("item_ref") != item_ref:
            continue
        if state == UNREAD:
            unknown = "history"
            continue
        if payload.get("action_class") not in acceptance_classes:
            continue
        if state == NO_DISPOSITION:
            unknown = "disposition"
            continue
        if (capsule.get("disposition") or {}).get("decision") != "accept":
            continue
        if (payload.get("checkpoint") or {}).get("dry_run") is True:
            continue
        found = SealedAcceptance(capsule_id=record.capsule_id, counterparty=payload.get("target"),
                                 deal_id=payload.get("deal_id"))
    return SaleAcceptance(acceptance=found, unknown=unknown)


def first_sealed_acceptance(
    operator: str, ledger: LedgerAPI, acceptance_classes: list[str], item_ref: str
) -> SealedAcceptance | None:
    """The sale's acceptance (``sale_acceptance``), or ``None`` when the sale
    has none or it is not known. Shared with ``release_on_acceptance``, so
    both read one acceptance per sale, and an unknown one releases
    nothing."""
    return sale_acceptance(operator, ledger, acceptance_classes, item_ref).acceptance


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

    sale = sale_acceptance(action.operator, ledger, acceptance_classes, action.item_ref)
    if sale.unknown is not None:
        return _outcome("n/a", _UNKNOWN_REASONS[sale.unknown],
                        not_applicable_evidence(_CHECK_ID, in_scope=True, missing_field=sale.unknown),
                        fails_closed=True)
    acceptance = sale.acceptance
    if acceptance is None:
        return _outcome("pass", "no acceptance is sealed for this sale", _evidence(False))
    cites = action.cited_mandate_capsule_id == acceptance["capsule_id"]
    same_counterparty = action.target is not None and action.target == acceptance["counterparty"]
    same_deal = action.deal_id is not None and action.deal_id == acceptance["deal_id"]
    if (cites and same_counterparty) or same_deal:
        return _outcome("pass", "the commitment follows the acceptance, to the counterparty that accepted",
                        _evidence(True))
    return _outcome("fail", "this sale already has an accepted commitment", _evidence(True))
