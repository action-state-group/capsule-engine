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
"""
from __future__ import annotations

from agent_action_capsule import json_digest

from ...ledger.api import LedgerAPI, ScanQuery
from ..action import Action
from ..capsule import ConstraintOutcome
from .base import CheckOutcome

__all__ = ["equivalence_key_for_action", "equivalence_key_for_capsule", "check_dedupe"]


def _capsule_verb(capsule: dict) -> str:
    action_id = capsule.get("action_id") or ""
    return action_id.split("/", 1)[0] if action_id else ""


def equivalence_key_for_action(action: Action) -> str:
    if action.equivalence_key is not None:
        return action.equivalence_key
    return json_digest(
        {
            "operator": action.operator,
            "developer": action.developer,
            "action_type": action.action_type,
            "verb": action.verb,
            "target": action.target,
        }
    )


def equivalence_key_for_capsule(capsule: dict) -> str:
    return json_digest(
        {
            "operator": capsule.get("operator", ""),
            "developer": capsule.get("developer", ""),
            "action_type": capsule.get("action_type", ""),
            "verb": _capsule_verb(capsule),
            "target": (capsule.get("asg_payload") or {}).get("target"),
        }
    )


def check_dedupe(action: Action, ledger: LedgerAPI, *, since: str | None = None) -> CheckOutcome:
    key = equivalence_key_for_action(action)
    query = ScanQuery(action_type=action.action_type, since=since)
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
