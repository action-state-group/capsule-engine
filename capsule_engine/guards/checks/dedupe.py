# SPDX-License-Identifier: Apache-2.0
"""dedupe check: exact-match equivalence lookup (index v0).

"index v0" is deliberately literal: an equivalence *digest* computed the
same way for both the candidate action and every ledger capsule scanned in
the window, compared for exact equality -- no fuzzy/semantic matching. The
default formula uses only fields present on any capsule, ours or a foreign
one already sitting in the ledger (operator, developer, action_type, and
the ``verb`` prefix of ``action_id``), so a dedupe hit fires against
capsules this guard never produced. A caller may override it per-action via
``Action.equivalence_key``.

An act stated against a pinned action taxonomy (``taxonomy_version`` set,
as the deal-check bridge in ``report/replay.py`` sets it from a deal check's
sealed record) is keyed on the act alone: operator, developer, its
``action_class``, its target and its amount. The record that states it (a deal
check is ``fyi``, ``deal-<id>/<seq>``) never enters the key, neither its
``action_type`` nor its ``action_id`` prefix, so the scan covers every type and
a second check carrying the same act matches the decision on the first.
Both sides go through ``_act_key``: an action and a capsule each project to
the same fields, and the formula exists once.

An action that states no act (``Action.states_act`` false) is not deduped: its
``dedupe`` is ``n/a``, out of scope, and it never matches anything.
"""
from __future__ import annotations

from agent_action_capsule import json_digest
from capsule_ledger.ledger.api import LedgerAPI, ScanQuery

from ..action import Action
from ..capsule import ConstraintOutcome, not_applicable_evidence
from .base import CheckOutcome

__all__ = ["equivalence_key_for_action", "equivalence_key_for_capsule", "check_dedupe"]


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
    taxonomy_pinned: bool,
) -> str:
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
        taxonomy_pinned=payload.get("taxonomy_version") is not None,
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
    for record in ledger.scan(query):
        if equivalence_key_for_capsule(record.capsule) == key:
            return CheckOutcome(
                constraint=ConstraintOutcome(
                    id="dedupe",
                    result="fail",
                    reason=f"equivalent action already recorded ({record.capsule_id[:16]}…)",
                    evidence={"equivalence_key": key, "matched_capsule_id": record.capsule_id},
                    check_type="policy",
                    method="exact_match_index_v0",
                ),
                chain_parent=record.capsule_id,
                chain_relation="confirms",
            )
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
