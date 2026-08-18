# SPDX-License-Identifier: Apache-2.0
"""caps check: a fold predicate over T1's fold engine.

``weekly_spend + amount <= cap``, where ``weekly_spend`` is the T1 fold's
own evaluated result (a real replay over the ledger, never a number the
guard computes itself) and ``amount`` is this action's own, not-yet-
recorded amount. Records that carry no amount (imported/foreign capsules,
or this developer's non-money actions) are skipped by the fold engine
itself (spec §3 rule 4), not treated as zero-contributing noise by this
check.
"""
from __future__ import annotations

from ...folds.definition import FoldDefinition
from ...folds.engine import evaluate_one
from ...ledger.api import LedgerAPI, ScanQuery
from ..action import Action
from ..capsule import ConstraintOutcome
from .base import CheckOutcome

__all__ = ["check_caps"]


def check_caps(
    action: Action,
    ledger: LedgerAPI,
    *,
    definition: FoldDefinition,
    cap_minor: int,
    since: str | None = None,
    as_of: str | None = None,
) -> CheckOutcome:
    if action.amount_minor is None:
        return CheckOutcome(
            constraint=ConstraintOutcome(
                id="caps",
                result="n/a",
                reason="action carries no amount; cap check not applicable",
                check_type="policy",
                method=definition.fold_id,
            )
        )

    records = [r.capsule for r in ledger.scan(ScanQuery(agent=action.developer, since=since))]
    trace = evaluate_one(
        definition,
        records,
        key_value=action.developer,
        as_of=as_of or action.resolved_timestamp(),
    )
    weekly_spend = trace.result or 0
    projected = weekly_spend + action.amount_minor
    envelope = trace.to_envelope()
    evidence = {
        "fold": envelope["fold"],
        "weekly_spend_minor": weekly_spend,
        "amount_minor": action.amount_minor,
        "cap_minor": cap_minor,
        "projected_minor": projected,
    }

    if projected <= cap_minor:
        return CheckOutcome(
            constraint=ConstraintOutcome(
                id="caps",
                result="pass",
                reason=f"weekly spend {weekly_spend} + {action.amount_minor} <= cap {cap_minor} (minor units)",
                evidence=evidence,
                check_type="policy",
                method=definition.fold_id,
            ),
            fold_envelopes=(envelope,),
        )
    return CheckOutcome(
        constraint=ConstraintOutcome(
            id="caps",
            result="fail",
            reason=f"weekly spend {weekly_spend} + {action.amount_minor} exceeds cap {cap_minor} (minor units)",
            evidence=evidence,
            check_type="policy",
            method=definition.fold_id,
        ),
        fold_envelopes=(envelope,),
    )
