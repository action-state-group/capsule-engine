# SPDX-License-Identifier: Apache-2.0
"""Attainment report: run a pack's ``outcomes[]`` (``EvidenceContract``,
usually ``profile=="obligation"``) against ONE read TRACE record
(``trace_reader.TraceReadResult``) and report, per outcome,
``established``/``failed``/``not_present`` -- the same three words for every
row regardless of which evidence class (FACT/RULE/JUDGED/CONFIRM/STATE/DOC)
the outcome declares, because this report never grades "compliant"; it only
says whether TRACE evidence for the statement exists and, where it exists,
whether it holds.

**Recomputable by construction**: this module reads nothing but *pack* and
*trace_read*/*field_map*, held for each call -- given the same three inputs
a reviewer gets the same rows, every time, with no hidden state.

**Named attribute access, never a computed one.** A ``TraceFieldCheck``'s
``resolve`` reads the verified ``TrustRecord`` through a literal attribute
chain (``lambda record: (record.model.version,)``), not a dotted string
path walked with ``getattr`` -- a typo in the mapping is then a real
``AttributeError`` a test catches, not a value that silently resolves to
``None`` and gets reported as an honest-looking ``not_present``.
``field_names`` carries the same fields as plain strings purely for report
text; it never drives field resolution itself.

**The one rule this module enforces mechanically, never leaves to a mapping
author to remember**: when the TRACE record itself failed verification
(``trace_read.status == READ_FAILED``), EVERY mapped outcome reports
``failed`` -- never ``not_present``. A tampered record does not become
selectively untrustworthy per field; nothing it appears to say can be used,
so no outcome that depends on it can render as though nothing was checked.

**Where a pack outcome has no TRACE-side mapping at all** -- most rows,
honestly, since TRACE v0.2 carries hardware/model/policy attestation, not
session-level disclosure ordering or document-by-digest presence -- the row
is ``not_present``. This is the honest default, never inferred: an
un-mapped outcome makes no claim about what TRACE *could* someday carry,
only that nothing here checks it *today*.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .schema import EvidenceContract, PackDefinition
from .trace_reader import READ_FAILED, SOURCE_TRACE, TraceReadResult

if TYPE_CHECKING:
    from agentrust_trace.models import TrustRecord

__all__ = [
    "STATUS_ESTABLISHED",
    "STATUS_FAILED",
    "STATUS_NOT_PRESENT",
    "TraceFieldCheck",
    "AttainmentRow",
    "build_attainment_report",
    "render_terminal",
]

STATUS_ESTABLISHED = "established"
STATUS_FAILED = "failed"
STATUS_NOT_PRESENT = "not_present"


@dataclass(frozen=True)
class TraceFieldCheck:
    """Declares which TRACE record field(s) answer one pack outcome, and the
    rule the resolved values must satisfy for the outcome to be
    ``established``.

    ``resolve`` reads the fields directly off a verified ``TrustRecord``
    (e.g. ``lambda record: (record.model.version, record.model.aibom_uri)``)
    and returns them in the same order ``field_names`` names them for
    report text. ``rule`` receives that tuple (``None`` per absent field)
    and returns ``True``/``False``.

    A presence-only check (DOC/CONFIRM-shaped: "does this document/receipt
    exist on the record") is ``rule=lambda values: all(v is not None for v in
    values)`` -- the same predicate the absence short-circuit below would
    apply anyway, spelled out explicitly so a reader never has to guess
    whether a check ever reaches ``rule`` with an all-``None`` tuple (it does
    not: see ``build_attainment_report``).
    """

    field_names: tuple[str, ...]
    resolve: Callable[[TrustRecord], tuple[Any, ...]]
    rule: Callable[[tuple[Any, ...]], bool]
    description: str


@dataclass(frozen=True)
class AttainmentRow:
    outcome_id: str
    statement: str
    clause_ref: str | None
    tier: str
    status: str  # STATUS_ESTABLISHED | STATUS_FAILED | STATUS_NOT_PRESENT
    source: str  # e.g. SOURCE_TRACE -- never blurred with another format
    detail: str


def _row(outcome: EvidenceContract, status: str, source: str, detail: str) -> AttainmentRow:
    return AttainmentRow(
        outcome_id=outcome.id,
        statement=outcome.statement,
        clause_ref=outcome.clause_ref,
        tier=outcome.tier,
        status=status,
        source=source,
        detail=detail,
    )


def build_attainment_report(
    pack: PackDefinition,
    trace_read: TraceReadResult,
    field_map: Mapping[str, TraceFieldCheck],
) -> tuple[AttainmentRow, ...]:
    """One ``AttainmentRow`` per outcome in ``pack.outcomes``, in declared
    order. ``field_map`` need not cover every outcome -- an outcome with no
    entry is reported ``not_present`` unconditionally (see module
    docstring); it is never itself downgraded to ``failed`` by a failed
    ``trace_read``, because a check that was never going to consult TRACE at
    all is not made any less checked by TRACE being untrustworthy.
    """
    rows: list[AttainmentRow] = []
    for outcome in pack.outcomes:
        check = field_map.get(outcome.id)
        if check is None:
            rows.append(
                _row(
                    outcome,
                    STATUS_NOT_PRESENT,
                    SOURCE_TRACE,
                    "no TRACE-side evidence mapping declared for this obligation",
                )
            )
            continue

        if trace_read.status == READ_FAILED:
            rows.append(
                _row(
                    outcome,
                    STATUS_FAILED,
                    SOURCE_TRACE,
                    f"TRACE record failed verification, so no claim it appears to make is usable: "
                    f"{trace_read.detail}",
                )
            )
            continue

        assert trace_read.record is not None  # READ_VERIFIED always carries the record
        values = check.resolve(trace_read.record)
        if all(v is None for v in values):
            rows.append(
                _row(
                    outcome,
                    STATUS_NOT_PRESENT,
                    SOURCE_TRACE,
                    f"field(s) {', '.join(check.field_names)} absent on this (verified) TRACE record",
                )
            )
            continue

        if check.rule(values):
            rows.append(_row(outcome, STATUS_ESTABLISHED, SOURCE_TRACE, check.description))
        else:
            rows.append(
                _row(
                    outcome,
                    STATUS_FAILED,
                    SOURCE_TRACE,
                    f"{check.description} -- resolved value(s) {values!r} do not satisfy the rule",
                )
            )
    return tuple(rows)


def render_terminal(rows: tuple[AttainmentRow, ...]) -> str:
    glyphs = {STATUS_ESTABLISHED: "✓", STATUS_FAILED: "✗", STATUS_NOT_PRESENT: "∅"}
    lines = [f"attainment report -- {len(rows)} outcome(s)", ""]
    for row in rows:
        glyph = glyphs.get(row.status, "?")
        clause = f"  clause={row.clause_ref}" if row.clause_ref else ""
        lines.append(f"  {glyph} {row.outcome_id}  tier={row.tier}  source={row.source}  status={row.status}{clause}")
        lines.append(f"      {row.detail}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
