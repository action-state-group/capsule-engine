# SPDX-License-Identifier: Apache-2.0
"""Assemble a ``DryRunReport`` from a real ``replay()`` run.

Nothing in this module drafts the model-tuning note itself -- ``model_note``
is only ever the operator's own pre-written text (e.g. pasted in from a
separate LLM call they ran themselves), passed straight through, or omitted
(``None``). Every other field is computed from the real ``GuardDecision``
objects ``replay.py`` produced; there is no synthetic fallback.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import replace as _dataclass_replace
from datetime import datetime, timezone

from ..folds.definition import FoldDefinition
from .model import DryRunReport, GuardSection, ModelNote, ReportRow
from .replay import ReplayResult, filter_since, load_records, replay

__all__ = ["build_dry_run_report", "build_dry_run_report_with_proposal", "GUARD_ORDER", "GUARD_DESCRIPTIONS"]

PROPOSED_CAPS_GUARD_ID = "caps_proposed"

# Fixed check order the guard engine itself evaluates in (engine.py:
# ``constraints = (dedupe_out.constraint, caps_out.constraint, vbd_out.constraint)``)
# -- a held decision is attributed to the first of these that failed.
GUARD_ORDER = ("dedupe", "caps", "verify_before_dispatch")

GUARD_DESCRIPTIONS = {
    "dedupe": "same order, paid twice — matched by sameness fingerprint",
    "caps": "over a granted limit — checked against the cited mandate",
    "verify_before_dispatch": "the cited authority didn't verify at dispatch time",
}

_CURRENCY_SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£"}


def _format_money(amount_minor: int, currency: str | None) -> str:
    symbol = _CURRENCY_SYMBOLS.get(currency or "", "")
    major = amount_minor / 100
    text = f"{major:,.2f}"
    return f"{symbol}{text}" if symbol else f"{text} {currency or ''}".strip()


def _format_compact_money(amount_minor: int, currency: str | None) -> str:
    symbol = _CURRENCY_SYMBOLS.get(currency or "", (currency or "") + " ")
    major = amount_minor / 100
    if abs(major) >= 1000:
        return f"{symbol}{major / 1000:.1f}k"
    return f"{symbol}{major:,.2f}"


def _attributed_guard(constraints) -> tuple[str, object] | None:
    """First failing constraint, in engine evaluation order -- the same rule
    ``engine._decide`` itself uses to pick deny/escalate."""
    by_id = {c.id: c for c in constraints}
    for guard_id in GUARD_ORDER:
        constraint = by_id.get(guard_id)
        if constraint is not None and constraint.result == "fail":
            return guard_id, constraint
    return None


def _when(timestamp: str | None) -> str:
    if not timestamp:
        return "—"
    text = timestamp[:-1] + "+00:00" if timestamp.endswith("Z") else timestamp
    dt = datetime.fromisoformat(text).astimezone(timezone.utc)
    return dt.strftime("%a %H:%M")


def _row_for(sourced, constraint) -> ReportRow:
    action = sourced.action
    why = constraint.reason or f"{constraint.id} check failed"
    if action.amount_minor is not None:
        why = f"{why} — {_format_money(action.amount_minor, action.currency)}"
    evidence = constraint.evidence or {}
    cited_id = evidence.get("matched_capsule_id") or evidence.get("cited_capsule_id")
    return ReportRow(
        when=_when(action.resolved_timestamp()),
        agent=action.developer,
        why=why,
        capsule=sourced.decision.capsule or {},
        amount_minor=action.amount_minor,
        currency=action.currency,
        cited_capsule_id=cited_id,
        cited_capsule=sourced.cited_capsule,
    )


def _value_held_label(rows: list[ReportRow]) -> str:
    totals: Counter[str] = Counter()
    for row in rows:
        if row.amount_minor is not None:
            totals[row.currency or ""] += row.amount_minor
    if not totals:
        return "no amount carried by these actions"
    # Sorted for deterministic output; each currency stays its own figure --
    # never summed across currencies (design's own "two currencies, never summed").
    parts = [_format_compact_money(minor, currency) for currency, minor in sorted(totals.items())]
    return " + ".join(parts)


def build_dry_run_report(
    ledger_paths: list[str],
    *,
    caps_fold: FoldDefinition,
    since: str | None = "7d",
    caps_minor: dict[str, int] | None = None,
    operator: str | None = None,
    model_note: str | None = None,
    model_id: str | None = None,
    manifest_digest: str | None = None,
) -> DryRunReport:
    all_records = load_records(ledger_paths)
    replayed_records = filter_since(all_records, since)
    result: ReplayResult = replay(
        replayed_records, caps_fold=caps_fold, caps_minor=caps_minor, manifest_digest=manifest_digest
    )

    sections: dict[str, list[ReportRow]] = {guard_id: [] for guard_id in GUARD_ORDER}
    operators: Counter[str] = Counter()
    agents: Counter[str] = Counter()
    for sourced in result.decisions:
        operators[sourced.action.operator] += 1
        agents[sourced.action.developer] += 1
        if sourced.decision.outcome == "allow":
            continue
        attributed = _attributed_guard(sourced.decision.constraints)
        if attributed is None:
            continue
        guard_id, constraint = attributed
        sections[guard_id].append(_row_for(sourced, constraint))

    guards = tuple(
        GuardSection(guard_id=guard_id, what=GUARD_DESCRIPTIONS[guard_id], rows=tuple(sections[guard_id]))
        for guard_id in GUARD_ORDER
    )

    resolved_operator = operator or (operators.most_common(1)[0][0] if operators else "")
    since_label = since
    checkpoint = result.record_range[1] if result.decisions else 0
    replay_command = f"capsule guard dry-run --since {since_label or 'all-time'} --share"

    note = ModelNote(quote=model_note, model_id=model_id or "unspecified-model", record_count=sum(len(r) for r in sections.values())) if model_note else None

    return DryRunReport(
        operator=resolved_operator,
        agents=tuple(sorted(agents)),
        since_label=since_label,
        actions_replayed=len(result.decisions),
        record_range=result.record_range,
        checkpoint=checkpoint,
        guards=guards,
        replay_command=replay_command,
        generated_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        model_note=note,
        manifest_digest=manifest_digest,
    )


def build_dry_run_report_with_proposal(
    ledger_paths: list[str],
    *,
    caps_fold: FoldDefinition,
    proposed_caps_minor: dict[str, int],
    since: str | None = "7d",
    caps_minor: dict[str, int] | None = None,
    proposal_rationale: str | None = None,
    operator: str | None = None,
    model_note: str | None = None,
    model_id: str | None = None,
    manifest_digest: str | None = None,
) -> DryRunReport:
    """Extends ``build_dry_run_report``'s report with one additional
    section: actions that were ``allow`` under the currently-configured
    ``caps_minor`` but would ALSO have been held under ``proposed_caps_minor``
    -- the "what would tightening to the proposed threshold catch" delta
    (starter-packs plan: "the conversion moment"). Purely additive -- the
    base sections are untouched, ``render.py`` already loops over
    ``report.guards`` generically, so this needed no template change.

    Replays the same ledger a second time under ``proposed_caps_minor``;
    the two replays visit the same records in the same order, so decisions
    line up positionally for the diff.
    """
    base = build_dry_run_report(
        ledger_paths,
        caps_fold=caps_fold,
        since=since,
        caps_minor=caps_minor,
        operator=operator,
        model_note=model_note,
        model_id=model_id,
        manifest_digest=manifest_digest,
    )

    all_records = load_records(ledger_paths)
    replayed_records = filter_since(all_records, since)
    current = replay(replayed_records, caps_fold=caps_fold, caps_minor=caps_minor, manifest_digest=manifest_digest)
    proposed = replay(replayed_records, caps_fold=caps_fold, caps_minor=proposed_caps_minor, manifest_digest=manifest_digest)

    newly_held_rows: list[ReportRow] = []
    for cur, prop in zip(current.decisions, proposed.decisions, strict=True):
        if cur.decision.outcome != "allow" or prop.decision.outcome == "allow":
            continue
        attributed = _attributed_guard(prop.decision.constraints)
        if attributed is None:
            continue
        _, constraint = attributed
        newly_held_rows.append(_row_for(prop, constraint))

    what = "allowed today, but would ALSO have been held under the proposed cap"
    if proposal_rationale:
        what = f"{what} ({proposal_rationale})"
    proposed_section = GuardSection(guard_id=PROPOSED_CAPS_GUARD_ID, what=what, rows=tuple(newly_held_rows))

    return _dataclass_replace(base, guards=base.guards + (proposed_section,))


def value_held_label(report: DryRunReport) -> str:
    return _value_held_label([row for _, row in report.held_rows])
