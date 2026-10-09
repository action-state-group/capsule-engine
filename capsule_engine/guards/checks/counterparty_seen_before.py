# SPDX-License-Identifier: Apache-2.0
"""counterparty_seen_before check: has this operator gone ahead with an
action addressed to this counterparty before?

The count is the ``counterparty.seen_before`` fold's own result over this
operator's records, keyed by ``asg_payload.target`` -- the counterparty
reference ``counterparty_identity_change`` finds a payee's prior record by.
Only accepted actions count. A prior count of zero fails the check (a
first-time counterparty); one or more passes it (a repeat). The evidence
says which, with the count and the key it was read under, and names any
input the action's target was built without (``Action.ignored_inputs``).
Applies only to the configured ``action_classes``.
"""
from __future__ import annotations

from functools import cache
from pathlib import Path

from capsule_ledger.ledger.api import LedgerAPI, ScanQuery

from ...folds.catalog import Catalog
from ...folds.definition import FoldDefinition
from ...folds.engine import evaluate_one
from ..action import Action
from ..capsule import ConstraintOutcome, not_applicable_evidence
from .base import CheckOutcome

__all__ = ["check_counterparty_seen_before", "check_recipient_seen_before", "seen_before_fold"]

_CHECK_ID = "counterparty_seen_before"
_FOLD_CATALOG_DIR = Path(__file__).resolve().parent.parent.parent / "folds" / "catalog_defs"


@cache
def seen_before_fold(fold_id: str, fold_digest: str) -> FoldDefinition:
    """The core-catalog fold ``fold_id``. Raises ``ValueError`` when it is
    missing or its definition digest is not ``fold_digest``."""
    entry = Catalog(_FOLD_CATALOG_DIR).get(fold_id)
    if entry is None:
        raise ValueError(f"fold {fold_id!r} is not in the core fold catalog")
    if entry.digest != fold_digest:
        raise ValueError(f"fold {fold_id!r} has digest {entry.digest}, the wicket pins {fold_digest}")
    return entry.definition


def _n_a(check_id: str, reason: str, method: str, *, in_scope: bool, missing_field: str | None = None) -> CheckOutcome:
    return CheckOutcome(
        constraint=ConstraintOutcome(
            id=check_id,
            result="n/a",
            reason=reason,
            evidence=not_applicable_evidence(check_id, in_scope=in_scope, missing_field=missing_field),
            check_type="policy",
            method=method,
        )
    )


def check_counterparty_seen_before(
    action: Action, ledger: LedgerAPI, *, definition: FoldDefinition, action_classes: list[str]
) -> CheckOutcome:
    return _seen_before(_CHECK_ID, "counterparty", action, ledger, definition=definition, action_classes=action_classes)


def check_recipient_seen_before(
    action: Action, ledger: LedgerAPI, *, definition: FoldDefinition, action_classes: list[str]
) -> CheckOutcome:
    """The same count, for a message: has this operator gone ahead with an
    action addressed to this recipient before?"""
    return _seen_before("recipient_seen_before", "recipient", action, ledger, definition=definition,
                        action_classes=action_classes)


def _seen_before(
    check_id: str, noun: str, action: Action, ledger: LedgerAPI, *, definition: FoldDefinition,
    action_classes: list[str],
) -> CheckOutcome:
    method = definition.fold_id
    if action.action_class not in action_classes:
        return _n_a(check_id, "the rule is not configured for this action class", method, in_scope=False)
    if action.target is None:
        return _n_a(check_id, f"the action names no target; the {noun} could not be identified",
                    method, in_scope=True, missing_field="target")

    # ``ScanQuery.counterparty`` is the ledger's filter on ``operator``.
    records = [r.capsule for r in ledger.scan(ScanQuery(counterparty=action.operator))]
    trace = evaluate_one(definition, records, key_value=action.target)
    prior_count = trace.result or 0
    seen_before = prior_count > 0
    evidence = {
        "fold": trace.fold_digest,
        "fold_key": {"path": definition.key, "value": action.target},
        "operator": action.operator,
        "prior_count": prior_count,
        "seen_before": seen_before,
    }
    if action.ignored_inputs:
        evidence["ignored_inputs"] = list(action.ignored_inputs)
    if seen_before:
        return CheckOutcome(
            constraint=ConstraintOutcome(
                id=check_id,
                result="pass",
                reason=f"repeat {noun}: {prior_count} accepted prior action(s) for this operator",
                evidence=evidence,
                check_type="policy",
                method=method,
            ),
            fold_envelopes=(trace.to_envelope(),),
        )
    return CheckOutcome(
        constraint=ConstraintOutcome(
            id=check_id,
            result="fail",
            reason=f"first-time {noun}: no accepted prior action for this operator",
            evidence=evidence,
            check_type="policy",
            method=method,
        ),
        fold_envelopes=(trace.to_envelope(),),
    )
