# SPDX-License-Identifier: Apache-2.0
"""caps check: a fold predicate over T1's fold engine.

``weekly_spend + amount <= cap``, where ``weekly_spend`` is the T1 fold's
own evaluated result (a real replay over the ledger, never a number the
guard computes itself) and ``amount`` is this action's own, not-yet-
recorded amount. Records that carry no amount (imported/foreign capsules,
or non-money actions) are skipped by the fold engine itself (spec §3
rule 4), not treated as zero-contributing noise by this check.

The running total is partitioned by the fold's own ``key`` (``developer`` or
``operator``), and the evidence names that key and the value the total was
read under. ``caps_minor`` is keyed by action class; ``resolve_caps_minor``
maps a legacy name to its canonical row so both spellings are one cap.
"""
from __future__ import annotations

from collections.abc import Mapping

from capsule_ledger.ledger.api import LedgerAPI, ScanQuery

from ...folds.definition import FoldDefinition
from ...folds.engine import evaluate_one
from ..action import Action
from ..capsule import ConstraintOutcome, not_applicable_evidence
from ..classes import resolve
from .base import CheckOutcome

__all__ = ["cap_for", "check_caps", "resolve_caps_minor"]


def _canonical(action_class: str) -> str:
    ac = resolve(action_class)
    return ac.name if ac is not None else action_class


def resolve_caps_minor(caps_minor: Mapping[str, int]) -> dict[str, int]:
    """``caps_minor`` keyed by canonical class name. A name outside the
    taxonomy is kept as written. Raises ``ValueError`` when a legacy and a
    canonical key for one class carry different limits."""
    out: dict[str, int] = {}
    for name, cap in caps_minor.items():
        canonical = _canonical(name)
        if canonical in out and out[canonical] != cap:
            raise ValueError(
                f"caps_minor sets two limits for {canonical!r} ({out[canonical]} and {cap}) "
                "under a legacy and a canonical name"
            )
        out[canonical] = cap
    return out


def cap_for(caps_minor: Mapping[str, int], action_class: str | None) -> int | None:
    """The limit for ``action_class`` in a ``resolve_caps_minor`` mapping."""
    if action_class is None:
        return None
    return caps_minor.get(_canonical(action_class))


def _fold_key(action: Action, key: str | None, since: str | None) -> tuple[str | None, ScanQuery]:
    """The action's value for the fold's partition key, and a scan narrowed to it.

    ``ScanQuery.counterparty`` is the ledger's filter on ``operator``.
    """
    if key == "developer":
        return action.developer, ScanQuery(agent=action.developer, since=since)
    if key == "operator":
        return action.operator, ScanQuery(counterparty=action.operator, since=since)
    if key is None:
        return None, ScanQuery(since=since)
    raise ValueError(f"caps cannot partition by fold key {key!r}; it reads developer or operator")


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
                reason="a cap is configured for this action class but the action carries no amount_minor; "
                "the cap could not be evaluated",
                evidence=not_applicable_evidence("caps", in_scope=True, missing_field="amount_minor"),
                check_type="policy",
                method=definition.fold_id,
            )
        )

    key_value, query = _fold_key(action, definition.key, since)
    records = [r.capsule for r in ledger.scan(query)]
    trace = evaluate_one(
        definition,
        records,
        key_value=key_value,
        as_of=as_of or action.resolved_timestamp(),
    )
    weekly_spend = trace.result or 0
    projected = weekly_spend + action.amount_minor
    envelope = trace.to_envelope()
    evidence = {
        "fold": envelope["fold"],
        "fold_key": {"path": definition.key, "value": key_value},
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
