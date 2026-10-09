# SPDX-License-Identifier: Apache-2.0
"""dedupe check: exact-match equivalence lookup (index v0).

"index v0" is deliberately literal: an equivalence *digest* computed the
same way for both the candidate action and every ledger capsule scanned in
the window, compared for exact equality -- no fuzzy/semantic matching. The
default formula uses only fields present on any capsule, ours or a foreign
one already sitting in the ledger (operator, developer, action_type, and
the ``verb`` prefix of ``action_id``), so a dedupe hit fires against
capsules this guard never produced. A caller may override it per-action via
``Action.equivalence_key``. The override keys the action side only: the key
is not sealed on the decision, so an earlier record is always keyed by the
formula. A caller's key makes two acts distinct, and never matches an earlier
record that carried the same key (``tests/test_dedupe_equivalence_key.py``).

An act stated against a pinned action taxonomy (``taxonomy_version`` set,
as the deal-check bridge in ``report/replay.py`` sets it from a deal check's
sealed record) is keyed on the act alone: operator, developer, its
``action_class``, its target and its amount. The record that states it (a deal
check is ``fyi``, ``deal-<id>/<seq>``) never enters the key, neither its
``action_type`` nor its ``action_id`` prefix, so the scan covers every type and
a second check carrying the same act matches the decision on the first.
An act that moves money in (``returned_minor`` or ``reverses_ref`` set; its
``amount_minor`` is spend, ``0``) is keyed on the amount it returns and the act
it reverses in place of the amount, so two refunds of different amounts never
collide, and the same refund of the same act does.
Both sides go through ``_act_key``: an action and a capsule each project to
the same fields, and the formula exists once.

A match in the same deal is a double commit and fails. A match in another deal
(both state a ``deal_id`` and they differ) fails with ``asks_approver`` set, so
the engine asks the class's approver; any same-deal match in the window wins
over it. Its reason holds nothing from the earlier act, which may be with
another counterparty; the earlier act is in the evidence only.

An action that states no act (``Action.states_act`` false) is not deduped: its
``dedupe`` is ``n/a``, out of scope, and it never matches anything.
"""
from __future__ import annotations

from dataclasses import dataclass

from agent_action_capsule import json_digest
from capsule_ledger.ledger import LedgerRecord
from capsule_ledger.ledger.api import LedgerAPI, ScanQuery

from ..action import Action
from ..capsule import ConstraintOutcome, not_applicable_evidence
from .base import CheckOutcome

__all__ = ["equivalence_key_for_action", "equivalence_key_for_capsule", "check_dedupe"]

_ANOTHER_DEAL_REASON = "the same act was taken in another deal inside the window: confirm it is not a duplicate"


def _capsule_verb(capsule: dict) -> str:
    action_id = capsule.get("action_id") or ""
    return action_id.split("/", 1)[0] if action_id else ""


def _act_key(
    *,
    operator: str,
    developer: str,
    action_type: str,
    verb: str,
    target: str | None,
    action_class: str | None,
    amount_minor: int | None,
    returned_minor: int | None,
    reverses_ref: str | None,
    taxonomy_pinned: bool,
) -> str:
    if taxonomy_pinned and (returned_minor is not None or reverses_ref is not None):
        return json_digest(
            {
                "operator": operator,
                "developer": developer,
                "action_class": action_class,
                "target": target,
                "returned_minor": returned_minor,
                "reverses_ref": reverses_ref,
            }
        )
    if taxonomy_pinned:
        return json_digest(
            {
                "operator": operator,
                "developer": developer,
                "action_class": action_class,
                "target": target,
                "amount_minor": amount_minor,
            }
        )
    return json_digest(
        {"operator": operator, "developer": developer, "action_type": action_type, "verb": verb, "target": target}
    )


def equivalence_key_for_action(action: Action) -> str:
    if action.equivalence_key is not None:
        return action.equivalence_key
    return _act_key(
        operator=action.operator,
        developer=action.developer,
        action_type=action.action_type,
        verb=action.verb,
        target=action.target,
        action_class=action.action_class,
        amount_minor=action.amount_minor,
        returned_minor=action.returned_minor,
        reverses_ref=action.reverses_ref,
        taxonomy_pinned=action.taxonomy_version is not None,
    )


def equivalence_key_for_capsule(capsule: dict) -> str:
    payload = capsule.get("asg_payload") or {}
    return _act_key(
        operator=capsule.get("operator", ""),
        developer=capsule.get("developer", ""),
        action_type=capsule.get("action_type", ""),
        verb=_capsule_verb(capsule),
        target=payload.get("target"),
        action_class=payload.get("action_class"),
        amount_minor=payload.get("amount_minor"),
        returned_minor=payload.get("returned_minor"),
        reverses_ref=payload.get("reverses_ref"),
        taxonomy_pinned=payload.get("taxonomy_version") is not None,
    )


@dataclass(frozen=True)
class _EarlierAct:
    """What a matched decision records about the act: read once from its
    capsule, for the outcome."""

    capsule_id: str
    deal_id: str | None
    amount_minor: int | None
    currency: str | None
    at: str | None

    @classmethod
    def of(cls, record: LedgerRecord) -> _EarlierAct:
        payload = record.capsule.get("asg_payload") or {}
        return cls(
            capsule_id=record.capsule_id,
            deal_id=payload.get("deal_id"),
            amount_minor=payload.get("amount_minor"),
            currency=payload.get("currency"),
            at=record.capsule.get("timestamp"),
        )


def _in_another_deal(action: Action, earlier: _EarlierAct) -> bool:
    """Both state a deal, and they differ. Unstated on either side, nothing
    shows the deals differ."""
    return action.deal_id is not None and earlier.deal_id is not None and earlier.deal_id != action.deal_id


def _repeat_in_this_deal(action: Action, key: str, earlier: _EarlierAct) -> CheckOutcome:
    """A double commit: refused. The reason names the earlier act only when
    both state the same deal, or neither states one (no deal to link)."""
    same_chain = action.deal_id == earlier.deal_id
    reason = (
        f"equivalent action already recorded ({earlier.capsule_id[:16]}…)"
        if same_chain
        else "equivalent action already recorded"
    )
    return CheckOutcome(
        constraint=ConstraintOutcome(
            id="dedupe",
            result="fail",
            reason=reason,
            evidence={"equivalence_key": key, "matched_capsule_id": earlier.capsule_id},
            check_type="policy",
            method="exact_match_index_v0",
        ),
        chain_parent=earlier.capsule_id if same_chain else None,
        chain_relation="confirms" if same_chain else None,
    )


def _repeat_in_another_deal(key: str, earlier: _EarlierAct) -> CheckOutcome:
    """The same act in another deal: an approver may confirm it is not a
    duplicate. The earlier act can be with another counterparty, and the
    reason can reach this one's copy, so the reason names nothing from it
    and no chain link joins the deals; its capsule id, amount and time are
    evidence, sealed only as a digest."""
    return CheckOutcome(
        constraint=ConstraintOutcome(
            id="dedupe",
            result="fail",
            reason=_ANOTHER_DEAL_REASON,
            evidence={
                "equivalence_key": key,
                "repeat": "other_deal",
                "matched_capsule_id": earlier.capsule_id,
                "matched_amount_minor": earlier.amount_minor,
                "matched_currency": earlier.currency,
                "matched_at": earlier.at,
            },
            check_type="policy",
            method="exact_match_index_v0",
        ),
        asks_approver=True,
    )


def check_dedupe(action: Action, ledger: LedgerAPI, *, since: str | None = None) -> CheckOutcome:
    if not action.states_act:
        return CheckOutcome(
            constraint=ConstraintOutcome(
                id="dedupe",
                result="n/a",
                reason="the record states no act",
                evidence=not_applicable_evidence("dedupe", in_scope=False),
                check_type="policy",
                method="exact_match_index_v0",
            )
        )
    key = equivalence_key_for_action(action)
    scanned_type = None if action.taxonomy_version is not None else action.action_type
    query = ScanQuery(action_type=scanned_type, since=since)
    other_deal = None
    for record in ledger.scan(query):
        if equivalence_key_for_capsule(record.capsule) != key:
            continue
        earlier = _EarlierAct.of(record)
        if not _in_another_deal(action, earlier):
            return _repeat_in_this_deal(action, key, earlier)
        other_deal = other_deal or earlier
    if other_deal is not None:
        return _repeat_in_another_deal(key, other_deal)
    return CheckOutcome(
        constraint=ConstraintOutcome(
            id="dedupe",
            result="pass",
            reason="no equivalent action in window",
            evidence={"equivalence_key": key},
            check_type="policy",
            method="exact_match_index_v0",
        )
    )
