# SPDX-License-Identifier: Apache-2.0
"""counterparty_identity_change check: compare the account a counterparty is
paid into against the value last recorded for that counterparty.

The prior value is read from the ledger: the most recent accepted decision
whose ``asg_payload.target`` equals this action's ``target`` and which
recorded a ``counterparty_account_ref``. A counterparty with no such record
has nothing to compare against, so the rule does not apply to this action.
Applies only to the configured ``action_classes``.
"""
from __future__ import annotations

from capsule_ledger.ledger.api import LedgerAPI, ScanQuery

from ..action import Action
from ..capsule import ConstraintOutcome, not_applicable_evidence
from .base import CheckOutcome

__all__ = ["check_counterparty_identity_change"]

_CHECK_ID = "counterparty_identity_change"
_METHOD = "prior_recorded_value_v0"


def _n_a(reason: str, *, in_scope: bool, missing_field: str | None = None) -> CheckOutcome:
    return CheckOutcome(
        constraint=ConstraintOutcome(
            id=_CHECK_ID,
            result="n/a",
            reason=reason,
            evidence=not_applicable_evidence(_CHECK_ID, in_scope=in_scope, missing_field=missing_field),
            check_type="policy",
            method=_METHOD,
        )
    )


def _prior_record(action: Action, ledger: LedgerAPI) -> tuple[str, str] | None:
    """(capsule_id, counterparty_account_ref) of the latest accepted decision
    for this target that recorded one, in ledger order."""
    prior = None
    for record in ledger.scan(ScanQuery(action_type=action.action_type)):
        capsule = record.capsule
        payload = capsule.get("asg_payload") or {}
        if payload.get("target") != action.target:
            continue
        if (capsule.get("disposition") or {}).get("decision") != "accept":
            continue
        ref = payload.get("counterparty_account_ref")
        if ref is not None:
            prior = (record.capsule_id, ref)
    return prior


def check_counterparty_identity_change(action: Action, ledger: LedgerAPI, *, action_classes: list[str]) -> CheckOutcome:
    if action.action_class not in action_classes:
        return _n_a("the rule is not configured for this action class", in_scope=False)
    if action.target is None:
        return _n_a("the action names no target; the counterparty could not be identified",
                    in_scope=True, missing_field="target")
    if action.counterparty_account_ref is None:
        return _n_a("the action carries no counterparty_account_ref; it could not be compared",
                    in_scope=True, missing_field="counterparty_account_ref")
    prior = _prior_record(action, ledger)
    if prior is None:
        return _n_a("no prior account is recorded for this counterparty; there is nothing to compare",
                    in_scope=False)
    prior_capsule_id, prior_ref = prior
    evidence = {
        "target": action.target,
        "counterparty_account_ref": action.counterparty_account_ref,
        "prior_capsule_id": prior_capsule_id,
        "prior_counterparty_account_ref": prior_ref,
    }
    if action.counterparty_account_ref != prior_ref:
        return CheckOutcome(
            constraint=ConstraintOutcome(
                id=_CHECK_ID,
                result="fail",
                reason="the counterparty's account differs from the one last recorded for it",
                evidence=evidence,
                check_type="policy",
                method=_METHOD,
            )
        )
    return CheckOutcome(
        constraint=ConstraintOutcome(
            id=_CHECK_ID,
            result="pass",
            reason="the counterparty's account matches the one last recorded for it",
            evidence=evidence,
            check_type="policy",
            method=_METHOD,
        )
    )
