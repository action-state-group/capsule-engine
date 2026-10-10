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

A class may also carry a per-action limit, compared against the proposed
action's own amount alone. With one, the evidence adds ``per_action_cap_minor``
and ``tripped``: one ``{limit, threshold_minor, observed_minor}`` entry per
limit exceeded (``per_action`` against the amount, ``window`` against the
projected total), empty on a pass. Without one the evidence keeps its
single-limit shape, so records under a window-only config keep their bytes.

Under a config with ``per_action_reads: spend_authorized_minor``
(``caps/5.0.0``) the per-action limit reads the action's authorised maximum,
``spend_authorized_minor``: the most the payment may take, which can exceed
the expected capture by a pre-authorisation buffer. It reads the capture,
``spend_minor`` (the action's ``amount_minor``), when no authorised maximum was
declared, or one below the capture was. The window limit always reads the
capture, because the rolling total sums what was taken. The evidence adds
``per_action_basis``: the field read, whether it fell back to the capture, and
the value compared.

When the engine reads its limits from activated policy (``policy/limits.py``)
the evidence adds ``limit_sources``: for each limit applied (``window``, and
``per_action`` when set), whether its value is the operator's, from a policy
profile (``operator_profile``, with that profile's digest), or the wicket's
default (``definition_default``), plus any raise activated but still inside
its cooling-off (``pending_raise``). An engine given bare limit tables has no
provenance to report, and its evidence keeps the shape it had.

Under a fold with a ``reversal`` clause (``spend.weekly/3.0.0``) the evidence
adds ``reversals``: how many cancels or refunds took a linked charge back out
of the total and by how much, and how many were unlinked and did nothing.
Under a fold without one the evidence keeps the shape it had.

Under a fold that also counts executed acts (``spend.weekly/3.1.0``, whose
filter admits ``EXECUTED_DECISION``) a replay, or a live check's history
ledger, writes a record for each executed act (``report/replay.py``,
``ExecutedActs``). One whose spend could not be read carries
``asg_payload.spend_unreadable`` and no amount, so the fold skips it. With one
in the window the total is not known: the check is ``n/a``, in scope, naming
``spend_minor``, with the reason ``WINDOW_UNREADABLE_REASON``, and the engine
asks rather than allows (``CheckOutcome.asks_when_unevaluated``). A per-action
limit the action exceeds still fails, and its evidence adds
``window_unreadable_count``.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Literal, NotRequired, TypedDict

from capsule_ledger.ledger.api import LedgerAPI, ScanQuery

from ...folds.definition import FilterClause, FoldDefinition, ReadField, Reduce
from ...folds.engine import ReversalSummary, evaluate_one
from ..action import Action
from ..capsule import ConstraintOutcome, not_applicable_evidence
from ..classes import resolve
from .base import CheckOutcome

__all__ = [
    "EXECUTED_DECISION",
    "SPEND_UNREADABLE",
    "WINDOW_UNREADABLE_REASON",
    "LIMIT_SOURCE_DEFAULT",
    "LIMIT_SOURCE_PROFILE",
    "PER_ACTION_READS",
    "CapTripped",
    "CapsEvidence",
    "FoldKey",
    "LimitSource",
    "LimitSources",
    "PendingRaise",
    "PerActionBasis",
    "TwoLimitCapsEvidence",
    "cap_for",
    "check_caps",
    "counts_executed_acts",
    "require_per_action_reads",
    "resolve_caps_minor",
]

# The values a caps config's ``per_action_reads`` may take. Absent, the
# per-action limit reads the capture, as caps/3.0.0 and caps/4.0.0 do.
PER_ACTION_READS = frozenset({"spend_authorized_minor"})

# Where a limit's value came from (``limit_sources``).
LIMIT_SOURCE_PROFILE = "operator_profile"
LIMIT_SOURCE_DEFAULT = "definition_default"

# The ``disposition.decision`` of the record a replay writes for an act a
# deal executed. No guard decision carries it.
EXECUTED_DECISION = "executed"
# The ``asg_payload`` flag on such a record whose spend could not be read.
SPEND_UNREADABLE = "spend_unreadable"
WINDOW_UNREADABLE_REASON = "an earlier act has no recorded decision, so the total cannot count it"
_DECISION_PATH = "disposition.decision"
_UNREADABLE_PATH = f"asg_payload.{SPEND_UNREADABLE}"


class FoldKey(TypedDict):
    """The partition a running total was read under."""

    path: str | None
    value: str | None


class PendingRaise(TypedDict):
    """A higher value activated for a limit, not yet in force."""

    value_minor: int
    effective_at: str
    limit_source: Literal["operator_profile", "definition_default"]
    profile_digest: NotRequired[str]


class LimitSource(TypedDict):
    """Where one applied limit's value came from."""

    limit_source: Literal["operator_profile", "definition_default"]
    # Only for ``operator_profile``: the digest of the profile that set it.
    profile_digest: NotRequired[str]
    pending_raise: NotRequired[PendingRaise]


class LimitSources(TypedDict):
    """``LimitSource`` per applied limit."""

    window: LimitSource
    per_action: NotRequired[LimitSource]


class CapsEvidence(TypedDict):
    """The facts a pass/fail caps constraint seals."""

    fold: str
    fold_key: FoldKey
    weekly_spend_minor: int
    amount_minor: int
    cap_minor: int
    projected_minor: int
    # Only under a fold with a ``reversal`` clause.
    reversals: NotRequired[ReversalSummary]
    # Only when the engine reads its limits from activated policy.
    limit_sources: NotRequired[LimitSources]
    # Only when an executed act in the window has a spend that cannot be read.
    window_unreadable_count: NotRequired[int]


class CapTripped(TypedDict):
    """One limit an action exceeded, as sealed in the caps evidence."""

    limit: Literal["per_action", "window"]
    threshold_minor: int
    observed_minor: int


class PerActionBasis(TypedDict):
    """The amount a per-action limit read, under ``per_action_reads``."""

    field: Literal["spend_authorized_minor", "spend_minor"]
    fell_back: bool
    observed_minor: int


class TwoLimitCapsEvidence(CapsEvidence):
    """``CapsEvidence`` under a config that also sets a per-action limit."""

    per_action_cap_minor: int
    tripped: list[CapTripped]
    # Only under a config with ``per_action_reads``.
    per_action_basis: NotRequired[PerActionBasis]


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


def counts_executed_acts(definition: FoldDefinition | None) -> bool:
    """Whether ``definition``'s filter admits the record a replay writes for
    an executed act (``EXECUTED_DECISION``)."""
    if definition is None:
        return False
    for clause in definition.filter:
        if clause.field != _DECISION_PATH:
            continue
        if (clause.op == "eq" and clause.value == EXECUTED_DECISION) or (
            clause.op == "in" and EXECUTED_DECISION in clause.value
        ):
            return True
    return False


def _unreadable_in_window(definition: FoldDefinition, records: list[dict], key_value: str | None, as_of: str) -> int:
    """How many executed acts with a spend that could not be read fall in
    ``definition``'s window under ``key_value``: the same key, window and
    anchor the total was read under, counted instead of summed."""
    reads = [ReadField(path="timestamp", erasure_class="commitment-ok"),
             ReadField(path=_DECISION_PATH, erasure_class="commitment-ok"),
             ReadField(path=_UNREADABLE_PATH, erasure_class="commitment-ok", default=False, has_default=True)]
    if definition.key is not None:
        reads.append(ReadField(path=definition.key, erasure_class="commitment-ok"))
    probe = replace(
        definition,
        reads=tuple(reads),
        filter=(FilterClause(field=_DECISION_PATH, op="eq", value=EXECUTED_DECISION),
                FilterClause(field=_UNREADABLE_PATH, op="eq", value=True)),
        reduce=Reduce(reducer="count"),
        reversal=None,
    )
    return evaluate_one(probe, records, key_value=key_value, as_of=as_of).result or 0


def require_per_action_reads(per_action_reads: str | None) -> None:
    """Raise ``ValueError`` unless ``per_action_reads`` is absent or one of
    ``PER_ACTION_READS``."""
    if per_action_reads is not None and per_action_reads not in PER_ACTION_READS:
        raise ValueError(f"caps per_action_reads {per_action_reads!r} is not one of {sorted(PER_ACTION_READS)}")


def _per_action_basis(action: Action, amount_minor: int) -> PerActionBasis:
    """What the per-action limit compares under ``per_action_reads``. An
    authorised maximum below the capture is not a maximum, so the capture is
    read instead."""
    authorized = action.spend_authorized_minor
    if authorized is None or authorized < amount_minor:
        return PerActionBasis(field="spend_minor", fell_back=True, observed_minor=amount_minor)
    return PerActionBasis(field="spend_authorized_minor", fell_back=False, observed_minor=authorized)


def check_caps(
    action: Action,
    ledger: LedgerAPI,
    *,
    definition: FoldDefinition,
    cap_minor: int,
    per_action_cap_minor: int | None = None,
    per_action_reads: str | None = None,
    since: str | None = None,
    as_of: str | None = None,
    limit_sources: LimitSources | None = None,
) -> CheckOutcome:
    require_per_action_reads(per_action_reads)
    if limit_sources is not None and ("per_action" in limit_sources) != (per_action_cap_minor is not None):
        raise ValueError("limit_sources must name a per_action source exactly when a per-action limit is applied")
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
    anchor = as_of or action.resolved_timestamp()
    trace = evaluate_one(definition, records, key_value=key_value, as_of=anchor)
    unreadable = _unreadable_in_window(definition, records, key_value, anchor) if counts_executed_acts(definition) else 0
    weekly_spend = trace.result or 0
    projected = weekly_spend + action.amount_minor
    envelope = trace.to_envelope()
    evidence = CapsEvidence(
        fold=envelope["fold"],
        fold_key=FoldKey(path=definition.key, value=key_value),
        weekly_spend_minor=weekly_spend,
        amount_minor=action.amount_minor,
        cap_minor=cap_minor,
        projected_minor=projected,
    )

    if trace.reversals is not None:
        evidence["reversals"] = trace.reversals
    if limit_sources is not None:
        evidence["limit_sources"] = limit_sources
    if unreadable:
        evidence["window_unreadable_count"] = unreadable

    if per_action_cap_minor is not None:
        basis = _per_action_basis(action, action.amount_minor) if per_action_reads is not None else None
        per_action_amount = basis["observed_minor"] if basis is not None else action.amount_minor
        result, reason, tripped = _judge_two_limits(
            per_action_amount, projected, cap_minor, per_action_cap_minor, basis["field"] if basis is not None else "amount"
        )
        if unreadable and not any(t["limit"] == "per_action" for t in tripped):
            return _window_unreadable(definition, envelope)
        two_limit = TwoLimitCapsEvidence(**evidence, per_action_cap_minor=per_action_cap_minor, tripped=tripped)
        if basis is not None:
            two_limit["per_action_basis"] = basis
        return CheckOutcome(
            constraint=ConstraintOutcome(
                id="caps",
                result=result,
                reason=reason,
                evidence=two_limit,
                check_type="policy",
                method=definition.fold_id,
            ),
            fold_envelopes=(envelope,),
        )

    if unreadable:
        return _window_unreadable(definition, envelope)
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


def _window_unreadable(definition: FoldDefinition, envelope: dict) -> CheckOutcome:
    """``n/a`` in scope, naming ``spend_minor``: an executed act in the
    window has a spend that cannot be read, so the total is not known. The
    engine asks rather than allows (``CheckOutcome.asks_when_unevaluated``)."""
    return CheckOutcome(
        constraint=ConstraintOutcome(
            id="caps",
            result="n/a",
            reason=WINDOW_UNREADABLE_REASON,
            evidence=not_applicable_evidence("caps", in_scope=True, missing_field="spend_minor"),
            check_type="policy",
            method=definition.fold_id,
        ),
        fold_envelopes=(envelope,),
        asks_when_unevaluated=True,
    )


def _judge_two_limits(
    amount: int, projected: int, cap_minor: int, per_action_cap_minor: int, amount_label: str
) -> tuple[str, str, list[CapTripped]]:
    tripped: list[CapTripped] = []
    if amount > per_action_cap_minor:
        tripped.append(CapTripped(limit="per_action", threshold_minor=per_action_cap_minor, observed_minor=amount))
    if projected > cap_minor:
        tripped.append(CapTripped(limit="window", threshold_minor=cap_minor, observed_minor=projected))
    if tripped:
        reason = "; ".join(
            f"{t['limit']} limit {t['threshold_minor']} exceeded by {t['observed_minor']} (minor units)" for t in tripped
        )
        return "fail", reason, tripped
    reason = (
        f"{amount_label} {amount} <= per-action limit {per_action_cap_minor}; "
        f"projected {projected} <= window limit {cap_minor} (minor units)"
    )
    return "pass", reason, tripped
