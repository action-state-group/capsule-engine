# SPDX-License-Identifier: Apache-2.0
"""release_on_acceptance check: personal data is released only to the buyer
who accepted.

A seller negotiates one sale with several buyers, one thread each. Personal
data may reach a buyer only once that buyer's acceptance is sealed.
``release_classes`` is the closed set of classes a disclosure may declare;
a disclosure in ``action_classes`` whose ``representation_class`` is in it
passes only when ALL hold:

- it names its sale (``task_authority_ref`` and ``item_ref``) and its
  recipient (``target``);
- it cites (``cited_mandate_capsule_id``) the sale's acceptance, found by
  ``single_commitment.first_sealed_acceptance`` so both checks read the same
  record: the sale is the ``item_ref``, under any task authority, and an
  acceptance that is not known (a live history that cannot settle it) is
  none;
- that acceptance was sealed for the same recipient (its ``target`` equals
  the disclosure's);
- the cited record re-verifies in the ledger.

Anything else fails: no citation, a citation of another sale's or another
buyer's acceptance, a record that is not a sealed acceptance, a missing
reference. Two inputs fail closed: an ``action_class`` that is missing or
has no row in the action taxonomy (it is resolved there, so a legacy alias
counts as its canonical class), because the action cannot be shown to be
out of scope; and a disclosure in ``action_classes`` whose
``representation_class`` is missing or outside ``release_classes``, because
no class of personal data is released without an acceptance. Only another
known action class is ``n/a`` out of scope. The engine lists this as an integrity check, so a
failure refuses whatever the pack declares; a one-shot approval does not
clear it.

The outcome carries no value. A checker result is sealed into a record that
can reach a buyer's copy, so the reason and evidence never hold the
disclosed data, ``item_ref``, ``task_authority_ref``, a counterparty or a
capsule id, and every failure on the citation reads the same, so a buyer
cannot learn whether someone else's acceptance exists. The evidence names
only the declared class, whether the release was bound to an acceptance,
and a missing field's name.
"""
from __future__ import annotations

from typing import TypedDict

from capsule_ledger.ledger.api import LedgerAPI

from ..action import Action
from ..capsule import ConstraintOutcome, NotApplicableEvidence, not_applicable_evidence
from ..classes import resolve
from .base import CheckOutcome
from .single_commitment import first_sealed_acceptance

__all__ = ["check_release_on_acceptance"]

_CHECK_ID = "release_on_acceptance"
_METHOD = "cited_sealed_acceptance_v1"
_NOT_BOUND = "the disclosure does not cite this sale's sealed acceptance by this recipient"


class ReleaseEvidence(TypedDict):
    constraint_id: str
    representation_class: str | None
    bound_to_acceptance: bool
    missing_field: str | None


def _evidence(representation_class: str | None, bound: bool, missing_field: str | None = None) -> ReleaseEvidence:
    return ReleaseEvidence(constraint_id=_CHECK_ID, representation_class=representation_class,
                           bound_to_acceptance=bound, missing_field=missing_field)


def _outcome(result: str, reason: str, evidence: ReleaseEvidence | NotApplicableEvidence) -> CheckOutcome:
    return CheckOutcome(
        constraint=ConstraintOutcome(
            id=_CHECK_ID, result=result, reason=reason, evidence=evidence, check_type="policy", method=_METHOD
        )
    )


def _missing(action: Action, cls: str, field: str) -> CheckOutcome:
    # An ignored input is named, never the value it was given.
    shape = f"the {field} input was not in its agreed shape" if field in action.ignored_inputs else f"no {field}"
    return _outcome("fail", f"the disclosure has {shape}; it cannot be bound to an acceptance",
                    _evidence(cls, False, missing_field=field))


def check_release_on_acceptance(
    action: Action,
    ledger: LedgerAPI,
    *,
    release_classes: list[str],
    acceptance_classes: list[str],
    action_classes: list[str],
) -> CheckOutcome:
    ac = resolve(action.action_class) if action.action_class is not None else None
    if ac is None:
        return _outcome("fail", "the action class has no taxonomy row; it cannot be shown to be outside the rule",
                        _evidence(None, False, missing_field="action_class"))
    if ac.name not in action_classes:
        return _outcome("n/a", "the rule is not configured for this action class",
                        not_applicable_evidence(_CHECK_ID, in_scope=False))
    cls = action.representation_class
    if cls not in release_classes:
        # An undeclared class's name is the producer's free text, never recorded.
        return _outcome("fail", "the disclosure declares no class this rule knows; it is not released",
                        _evidence(None, False, missing_field="representation_class"))
    for field, value in (("task_authority_ref", action.task_authority_ref), ("item_ref", action.item_ref),
                         ("target", action.target)):
        if value is None:
            return _missing(action, cls, field)
    if action.cited_mandate_capsule_id is None:
        return _outcome("fail", "the disclosure cites no acceptance", _evidence(cls, False))

    acceptance = first_sealed_acceptance(action.operator, ledger, acceptance_classes, action.item_ref)
    if (
        acceptance is None
        or action.cited_mandate_capsule_id != acceptance["capsule_id"]
        or action.target != acceptance["counterparty"]
    ):
        return _outcome("fail", _NOT_BOUND, _evidence(cls, False))
    verified = ledger.verify(acceptance["capsule_id"])
    if verified is None or not verified.ok:
        return _outcome("fail", _NOT_BOUND, _evidence(cls, False))
    return _outcome("pass", "the disclosure cites this sale's sealed acceptance by this recipient",
                    _evidence(cls, True))
