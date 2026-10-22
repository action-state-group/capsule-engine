# SPDX-License-Identifier: Apache-2.0
"""release_on_acceptance check: personal data is released only to the buyer
who accepted.

A seller negotiates one sale with several buyers, one thread each. Personal
data may reach a buyer only once that buyer's acceptance is sealed. The
wicket's ``binding`` selects how a disclosure is bound to it.

**Cited** (no ``binding``: seller.release_on_acceptance/1.0.0 and 1.1.0).
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
reference, and a ``representation_class`` that is missing or outside
``release_classes``.

**Same deal, same buyer** (``binding: same_deal_same_buyer``,
seller.release_on_acceptance/1.2.0). The disclosure's classes of data
(``disclosed_classes``) are read, never ``representation_class``. Every
class must be in ``release_classes``; a disclosure naming none, or any
other, fails. One whose classes are all in ``pre_acceptance_classes`` (the
listing's public pickup area) passes to any buyer, before or after any
acceptance. Anything else passes only when it names its ``item_ref``, its
``deal_id`` and the counterparty fingerprint its check sealed
(``counterparty_ids`` under ``counterparty_fp_alg``), and the sale's
acceptance (``single_commitment.sale_acceptance``, the same record the cited
binding reads) is known, is in the same deal (``deal_id`` equal), and was
sealed for the same buyer: its ``thread_counterparty``, which the history
carries beside the act and no decision seals
(``report/live_history.py``), is under the same algorithm, and at least one
per-person identity kind (``email``, ``phone``, ``profile_id``,
``relay_address``) is on both sides and every such kind on both sides is
equal; ``name``, ``payee`` and ``domain`` are then not compared: a name is
written many ways, and the thread default seals ``payee`` from it, while a
marketplace domain is shared by every stranger on it. When neither side
seals any per-person kind (an opening that named the buyer by name and
domain only), the buyer is the same when both sides seal a ``name`` and a
``domain`` and both are equal; with the deal equal too, that is the thread's
own buyer. A per-person kind on one side only, a name alone or a domain
alone is no buyer. The ``payee`` is never compared. The acceptance is not
cited and not re-verified: the history entries are the producer's own
sealed records on the user's device, the trust ``single_commitment`` places
in them. A history that may be missing acts, an act of this sale that could
not be read, an acceptance-class act with no disposition, or an earlier
accepted commitment in the history that names no item, leave the
acceptance unknown, which is none here. The counterparty block must be a
check's, whole (``history_state.well_formed_counterparty``) on both sides: a
share whose block holds any member of another kind or shape fails naming
``counterparty``, so a member dropped for its shape never reads as absent
(and never opens the name-and-domain reading), and an acceptance whose
block is not whole carries none, so it binds no share. No acceptance, an unknown one, and
one in another deal or by another buyer fail with one reason, so a buyer
cannot learn whether someone else accepted. A share naming no class, or a
class outside ``release_classes`` (the credential, verification-code, card
and ID classes), has its own reason: it says nothing about other buyers.
The rule applies to every recipient but ``except_recipient_roles`` (a
buyer, a third party, the user), so those classes go to no one but the
platform.

Under either binding two inputs fail closed: an ``action_class`` that is
missing or has no row in the action taxonomy (it is resolved there, so a
legacy alias counts as its canonical class), because the action cannot be
shown to be out of scope. Only another known action class is ``n/a`` out of
scope, and, when the wicket configures ``except_recipient_roles`` (1.1.0
and 1.2.0: ``platform``), a disclosure whose ``recipient_role`` is one of
them: it goes to no buyer, so there is no acceptance to bind, and
``recipient_role`` measures what it may carry. The engine lists this as an
integrity check, so a failure refuses whatever the pack declares; a
one-shot approval does not clear it.

The outcome carries no value. A checker result is sealed into a record that
can reach a buyer's copy, so the reason and evidence never hold the
disclosed data, ``item_ref``, ``task_authority_ref``, a deal, a counterparty
or a capsule id. The evidence names only the class or classes read (class
names from a closed set), whether the release was bound to an acceptance,
and a missing field's name.
"""
from __future__ import annotations

from typing import TypedDict

from cll.ledger.api import LedgerAPI

from ..action import Action
from ..capsule import ConstraintOutcome, NotApplicableEvidence, not_applicable_evidence
from ..classes import resolve
from ..history_state import well_formed_counterparty
from .base import CheckOutcome
from .single_commitment import ThreadCounterparty, first_sealed_acceptance, sale_acceptance

__all__ = ["SAME_DEAL_SAME_BUYER", "check_release_on_acceptance", "same_buyer"]

_CHECK_ID = "release_on_acceptance"
_METHOD = "cited_sealed_acceptance_v1"
_NOT_BOUND = "the disclosure does not cite this sale's sealed acceptance by this recipient"
# The ``binding`` that binds the acceptance from the history (1.2.0).
SAME_DEAL_SAME_BUYER = "same_deal_same_buyer"
_HISTORY_METHOD = "same_deal_same_buyer_v1"
_NOT_ACCEPTED = "the history given shows no acceptance of this sale by this buyer in this deal"
# The identity kinds that name one person (capsule-cli deal_counterparty.go
# perPartyKinds). A name, a payee and a domain are fingerprinted too but are
# not identities.
_PER_PERSON_KINDS = ("email", "phone", "profile_id", "relay_address")
# What names the thread's own buyer when neither side seals a per-person
# kind: both of these, equal. The payee is never read.
_NO_PER_PERSON_KINDS = ("name", "domain")


class ReleaseEvidence(TypedDict):
    constraint_id: str
    representation_class: str | None
    bound_to_acceptance: bool
    missing_field: str | None


class ClassesEvidence(TypedDict):
    """1.2.0's evidence: the class names the disclosure gives, never the data."""

    constraint_id: str
    disclosed_classes: list[str] | None
    bound_to_acceptance: bool
    missing_field: str | None


def _evidence(representation_class: str | None, bound: bool, missing_field: str | None = None) -> ReleaseEvidence:
    return ReleaseEvidence(constraint_id=_CHECK_ID, representation_class=representation_class,
                           bound_to_acceptance=bound, missing_field=missing_field)


def _classes_evidence(classes: tuple[str, ...] | None, bound: bool,
                      missing_field: str | None = None) -> ClassesEvidence:
    return ClassesEvidence(constraint_id=_CHECK_ID, disclosed_classes=list(classes) if classes else None,
                           bound_to_acceptance=bound, missing_field=missing_field)


def _outcome(result: str, reason: str, evidence: ReleaseEvidence | ClassesEvidence | NotApplicableEvidence,
             method: str = _METHOD) -> CheckOutcome:
    return CheckOutcome(
        constraint=ConstraintOutcome(
            id=_CHECK_ID, result=result, reason=reason, evidence=evidence, check_type="policy", method=method
        )
    )


def _missing_shape(action: Action, field: str) -> str:
    # An ignored input is named, never the value it was given.
    return f"the {field} input was not in its agreed shape" if field in action.ignored_inputs else f"no {field}"


def _missing(action: Action, cls: str, field: str) -> CheckOutcome:
    return _outcome("fail", f"the disclosure has {_missing_shape(action, field)}; it cannot be bound to an acceptance",
                    _evidence(cls, False, missing_field=field))


def same_buyer(acceptance: ThreadCounterparty | None, fp_alg: str | None, ids: dict[str, str] | None) -> bool:
    """Whether the counterparty an acceptance's check sealed and the one a
    disclosure's check sealed are one buyer: both fingerprinted under the
    same algorithm, and either at least one per-person identity kind on both
    sides with every per-person kind on both sides equal, or, when neither
    side seals any per-person kind, both a ``name`` and a ``domain`` on both
    sides, each equal. A payee is never read (module docstring)."""
    if acceptance is None or not fp_alg or not ids or acceptance["fp_alg"] != fp_alg:
        return False
    theirs = acceptance["ids"]
    if not any(k in theirs or k in ids for k in _PER_PERSON_KINDS):
        # The deal is checked apart: this is the thread's own buyer by the
        # opening's name and domain.
        return all(k in theirs and k in ids and theirs[k] == ids[k] for k in _NO_PER_PERSON_KINDS)
    shared = [k for k in _PER_PERSON_KINDS if k in theirs and k in ids]
    return bool(shared) and all(theirs[k] == ids[k] for k in shared)


def check_release_on_acceptance(
    action: Action,
    ledger: LedgerAPI,
    *,
    release_classes: list[str],
    acceptance_classes: list[str],
    action_classes: list[str],
    except_recipient_roles: list[str] | None = None,
    binding: str | None = None,
    pre_acceptance_classes: list[str] | None = None,
) -> CheckOutcome:
    """The check under the wicket's ``binding``: cited when it is unset (the
    1.0.0 and 1.1.0 configs, decided as they always were), from the history
    under ``same_deal_same_buyer`` (module docstring)."""
    if binding is not None and binding != SAME_DEAL_SAME_BUYER:
        raise ValueError(f"release_on_acceptance has no binding {binding!r}")
    method = _HISTORY_METHOD if binding is not None else _METHOD
    ac = resolve(action.action_class) if action.action_class is not None else None
    if ac is None:
        evidence = (_classes_evidence(action.disclosed_classes, False, missing_field="action_class")
                    if binding is not None else _evidence(None, False, missing_field="action_class"))
        return _outcome("fail", "the action class has no taxonomy row; it cannot be shown to be outside the rule",
                        evidence, method)
    if ac.name not in action_classes:
        return _outcome("n/a", "the rule is not configured for this action class",
                        not_applicable_evidence(_CHECK_ID, in_scope=False), method)
    if action.recipient_role is not None and action.recipient_role in (except_recipient_roles or ()):
        return _outcome("n/a", f"the recipient is a {action.recipient_role}, not a buyer; recipient_role measures it",
                        not_applicable_evidence(_CHECK_ID, in_scope=False), method)
    if binding is not None:
        return _release_from_history(action, ledger, release_classes=release_classes,
                                     acceptance_classes=acceptance_classes,
                                     pre_acceptance_classes=pre_acceptance_classes or [])
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


def _release_from_history(
    action: Action,
    ledger: LedgerAPI,
    *,
    release_classes: list[str],
    acceptance_classes: list[str],
    pre_acceptance_classes: list[str],
) -> CheckOutcome:
    """The same-deal, same-buyer binding (1.2.0) for a disclosure in scope."""
    classes = action.disclosed_classes
    if classes is None:
        return _outcome("fail", "the share names no class of data",
                        _classes_evidence(None, False, missing_field="disclosed_classes"), _HISTORY_METHOD)
    if any(c not in release_classes for c in classes):
        return _outcome("fail", "a credential, a verification code, a payment card or an ID document is never shared "
                        "with anyone",
                        _classes_evidence(classes, False), _HISTORY_METHOD)
    if all(c in pre_acceptance_classes for c in classes):
        return _outcome("pass", "every class the disclosure names may go to a buyer before acceptance",
                        _classes_evidence(classes, False), _HISTORY_METHOD)
    for field, present in (("item_ref", action.item_ref is not None), ("deal_id", action.deal_id is not None),
                           ("counterparty", bool(action.counterparty_ids) and bool(action.counterparty_fp_alg))):
        if not present:
            return _outcome("fail", f"the disclosure has {_missing_shape(action, field)}; it cannot be bound to an "
                            "acceptance", _classes_evidence(classes, False, missing_field=field), _HISTORY_METHOD)
    if action.counterparty_malformed or not well_formed_counterparty(action.counterparty_fp_alg,
                                                                      action.counterparty_ids):
        # A member dropped for its shape must not read as absent: the whole
        # block is unusable.
        return _outcome("fail", "the counterparty input was not in its agreed shape; it cannot be bound to an "
                        "acceptance", _classes_evidence(classes, False, missing_field="counterparty"),
                        _HISTORY_METHOD)
    sale = sale_acceptance(action.operator, ledger, acceptance_classes, action.item_ref)
    acceptance = sale.acceptance if sale.unknown is None else None
    if (
        acceptance is None
        or acceptance["deal_id"] != action.deal_id
        or not same_buyer(acceptance["thread_counterparty"], action.counterparty_fp_alg, action.counterparty_ids)
    ):
        return _outcome("fail", _NOT_ACCEPTED, _classes_evidence(classes, False), _HISTORY_METHOD)
    return _outcome("pass", "the history given shows this buyer accepted this sale in this deal",
                    _classes_evidence(classes, True), _HISTORY_METHOD)
