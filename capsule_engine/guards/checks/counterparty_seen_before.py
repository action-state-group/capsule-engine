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

An action in those classes that names no target is not applicable, naming
``target``, unless the wicket configures ``missing_target: unseen``
(counterparty_seen_before/4.0.0). Then it, and an action whose target is
empty or only whitespace, fails as a first-time
counterparty with the reason "no payee named": version 4 is configured for
money.purchase, and a purchase that names nobody cannot be shown to go to a
payee seen before.
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
# What an in-scope action naming no target records: ``n/a`` naming the field
# (versions 1 to 3), or a failure as an unseen counterparty (version 4).
MISSING_TARGET_MODES = frozenset({"n/a", "unseen"})
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


def _no_payee(check_id: str, noun: str, action: Action, method: str) -> CheckOutcome:
    """An in-scope action naming no target, read as an unseen counterparty.
    No fold runs: there is no key to count under. ``counterparty_ids`` are
    not read here (``counterparty_list`` reads them), so an action carrying
    only fingerprints fails the same way."""
    evidence = {"missing_field": "target", "operator": action.operator, "seen_before": False}
    if action.ignored_inputs:
        evidence["ignored_inputs"] = list(action.ignored_inputs)
    return CheckOutcome(
        constraint=ConstraintOutcome(
            id=check_id,
            result="fail",
            reason=f"no payee named: the action names no target, so the {noun} is read as first-time",
            evidence=evidence,
            check_type="policy",
            method=method,
        )
    )


def check_counterparty_seen_before(
    action: Action, ledger: LedgerAPI, *, definition: FoldDefinition, action_classes: list[str],
    missing_target: str = "n/a",
) -> CheckOutcome:
    if missing_target not in MISSING_TARGET_MODES:
        raise ValueError(f"missing_target {missing_target!r} is not one of {sorted(MISSING_TARGET_MODES)}")
    return _seen_before(_CHECK_ID, "counterparty", action, ledger, definition=definition, action_classes=action_classes,
                        missing_target=missing_target)


def check_recipient_seen_before(
    action: Action, ledger: LedgerAPI, *, definition: FoldDefinition, action_classes: list[str]
) -> CheckOutcome:
    """The same count, for a message: has this operator gone ahead with an
    action addressed to this recipient before?"""
    return _seen_before("recipient_seen_before", "recipient", action, ledger, definition=definition,
                        action_classes=action_classes)


def _seen_before(
    check_id: str, noun: str, action: Action, ledger: LedgerAPI, *, definition: FoldDefinition,
    action_classes: list[str], missing_target: str = "n/a",
) -> CheckOutcome:
    method = definition.fold_id
    if action.action_class not in action_classes:
        return _n_a(check_id, "the rule is not configured for this action class", method, in_scope=False)
    # Under "unseen" a blank target names nobody either: counted under "" it
    # would let one blank-target act make every later one a repeat.
    if missing_target == "unseen" and (action.target is None or not action.target.strip()):
        return _no_payee(check_id, noun, action, method)
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
