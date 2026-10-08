# SPDX-License-Identifier: Apache-2.0
"""counterparty_list check: is this action's counterparty on a list the user
supplied?

The list is the user's, not the pack's. The wicket definition ships an empty
deny list (``counterparty_list.yaml``); a policy profile replaces ``mode``,
``entries`` and ``disposition`` (``policy/profile.py``), so two households
with different lists run the same wicket at the same digest.

``mode`` is ``deny`` (fail when the counterparty is listed: "never buy from
X") or ``allow`` (fail when it is not: "only ever these"). A profile that
sets a list must name its mode; neither is assumed.

What it matches on: ``Action.target``, the declared counterparty reference
the caller already supplies and that ``counterparty_seen_before`` and
``counterparty_identity_change`` key on. Not a merchant's display name,
which is not an identifier, and not a domain looked up anywhere: the check
is a pure function of the action and its config, with no I/O. Matching is
exact on the reference string, with no case folding or trimming, because
any normalisation would be a guess about whether two references name one
counterparty. A reference is only as stable as the caller that declares it;
if a caller declares a domain, a re-registered domain matches the same
entry, and nothing here can tell.

An in-scope action with no target, or an empty one, is ``n/a`` naming
``target`` as the missing field: the list was not consulted, so it neither
passes nor fails.

``disposition`` is read by the engine, not here (``guards/engine.py``):
``deny`` refuses on a fail; ``ask`` sends the fail to an approver where the
action class names one and refuses where it does not, the same rule a caps
or first-time-counterparty fail follows.
"""
from __future__ import annotations

from typing import TypedDict

from agent_action_capsule.canonical import json_digest

from ..action import Action
from ..capsule import ConstraintOutcome, NotApplicableEvidence, not_applicable_evidence
from .base import CheckOutcome

__all__ = ["DISPOSITIONS", "MODES", "CounterpartyListEvidence", "check_counterparty_list", "require_disposition"]

_CHECK_ID = "counterparty_list"
_METHOD = "exact_reference_match_v0"
MODES = frozenset({"deny", "allow"})
DISPOSITIONS = frozenset({"deny", "ask"})


class CounterpartyListEvidence(TypedDict):
    """What a pass or a fail records: the reference read, how it was
    matched, which list (by digest and size) and the entry it matched."""

    match_field: str
    match_rule: str
    value: str
    mode: str
    list_digest: str
    list_size: int
    matched_entry: str | None


def require_disposition(disposition: str) -> None:
    """Refuse a disposition the engine does not know, when it is built."""
    if disposition not in DISPOSITIONS:
        raise ValueError(f"counterparty_list disposition {disposition!r} must be one of {sorted(DISPOSITIONS)}")


def _outcome(result: str, reason: str, evidence: CounterpartyListEvidence | NotApplicableEvidence) -> CheckOutcome:
    return CheckOutcome(
        constraint=ConstraintOutcome(
            id=_CHECK_ID, result=result, reason=reason, evidence=evidence, check_type="policy", method=_METHOD
        )
    )


def check_counterparty_list(
    action: Action, *, mode: str, entries: list[str], action_classes: list[str]
) -> CheckOutcome:
    if mode not in MODES:
        raise ValueError(f"counterparty_list mode {mode!r} must be one of {sorted(MODES)}")
    if action.action_class not in action_classes:
        return _outcome(
            "n/a",
            "the rule is not configured for this action class",
            not_applicable_evidence(_CHECK_ID, in_scope=False),
        )
    if not action.target:
        return _outcome(
            "n/a",
            "the action names no target; the counterparty list could not be checked",
            not_applicable_evidence(_CHECK_ID, in_scope=True, missing_field="target"),
        )
    listed = sorted(entries)
    matched = action.target if action.target in listed else None
    evidence: CounterpartyListEvidence = {
        "match_field": "target",
        "match_rule": "exact",
        "value": action.target,
        "mode": mode,
        "list_digest": json_digest(listed),
        "list_size": len(listed),
        "matched_entry": matched,
    }
    # deny: a match fails. allow: a miss fails.
    result = "fail" if (matched is not None) == (mode == "deny") else "pass"
    verdict = "is on" if matched is not None else "is not on"
    reason = f"counterparty {action.target!r} {verdict} the {mode} list (consulted {len(listed)} entries)"
    return _outcome(result, reason, evidence)
