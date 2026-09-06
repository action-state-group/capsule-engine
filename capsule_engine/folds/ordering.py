# SPDX-License-Identifier: Apache-2.0
"""Ordering fold — GRC folds batch item 1 (obligations report inventory,
2026-09-03): proves one event class was sealed at or before another, within
a session/deployment. Pure replay over a chained capsule stream, walked in
ledger order and never re-sorted — the same determinism discipline
``folds/engine.py`` documents (spec §3 rules 1/3), even though this fold
does not go through ``FoldDefinition``/``reducers.py``: the closed reducer
set (count/sum/min/max/last) cannot express a structural "A before B"
relation, so this is a bespoke replay function in the same spirit as
``capsule_emit.chain_segment`` — a purpose-built verifier, not a forced fit
into the numeric-accumulation engine.

One fold, four obligations, parameterised by ``before``/``after``
classifiers (the caller's own predicate — this module never invents a
record-kind vocabulary, matching ``chain_segment``'s "log vocabulary only"
discipline):

* EU-50-1 — ``interaction.disclosure`` precedes the first substantive agent
  turn.
* EU-50-3 — ``interaction.disclosure`` (kind=emotion|biometric) precedes the
  relevant action capsule.
* EU-26-7 — a worker notice precedes the first workplace-action capsule.
* EU-27 — the FRIA artifact precedes the first-use capsule.

A session where the gated (``after``) event has not happened yet is
``insufficient_evidence`` — never graded a pass or a fail, because the
obligation has not been triggered. A session is ``not_met`` only once the
gated event has occurred without (or after) the required prior disclosure.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .paths import get_path

__all__ = ["SessionOrderingResult", "OrderingResult", "evaluate_ordering"]


@dataclass(frozen=True)
class SessionOrderingResult:
    session: Any
    verdict: str  # "met" | "not_met" | "insufficient_evidence"
    detail: str


@dataclass(frozen=True)
class OrderingResult:
    sessions: tuple[SessionOrderingResult, ...]
    considered_count: int
    skipped_count: int  # records missing the declared session_key

    @property
    def met_count(self) -> int:
        return sum(1 for s in self.sessions if s.verdict == "met")

    @property
    def not_met_count(self) -> int:
        return sum(1 for s in self.sessions if s.verdict == "not_met")

    @property
    def total_sessions(self) -> int:
        return len(self.sessions)


def evaluate_ordering(
    records: list[dict],
    *,
    session_key: str,
    before: Callable[[dict], bool],
    after: Callable[[dict], bool],
) -> OrderingResult:
    """Walk ``records`` in ledger order (never re-sorted), grouping by
    ``session_key`` (a dotted path resolved via ``paths.get_path``). Within
    each session, ``before`` must be satisfied by some record AT OR BEFORE
    the first record satisfying ``after`` — a single record satisfying both
    counts as "at the latest at" (EU-50-1's own wording), so index equality
    is ``met``, not ``not_met``.

    A record missing the declared ``session_key`` is skipped (counted), the
    same skip-with-count discipline the fold engine uses for a declared read
    field with no default — never an error.
    """
    order: list[Any] = []
    seen: set[Any] = set()
    seen_before_at: dict[Any, int] = {}
    seen_after_at: dict[Any, int] = {}
    skipped = 0
    considered = 0

    for idx, record in enumerate(records):
        considered += 1
        session = get_path(record, session_key, None)
        if session is None:
            skipped += 1
            continue
        if session not in seen:
            seen.add(session)
            order.append(session)
        if before(record) and session not in seen_before_at:
            seen_before_at[session] = idx
        if after(record) and session not in seen_after_at:
            seen_after_at[session] = idx

    sessions: list[SessionOrderingResult] = []
    for session in order:
        after_idx = seen_after_at.get(session)
        before_idx = seen_before_at.get(session)
        if after_idx is None:
            sessions.append(
                SessionOrderingResult(
                    session=session,
                    verdict="insufficient_evidence",
                    detail="the gated event has not occurred yet in this session",
                )
            )
        elif before_idx is None:
            sessions.append(
                SessionOrderingResult(
                    session=session,
                    verdict="not_met",
                    detail=f"gated event at index {after_idx} has no prior disclosure in this session",
                )
            )
        elif before_idx <= after_idx:
            sessions.append(
                SessionOrderingResult(
                    session=session,
                    verdict="met",
                    detail=f"disclosure at index {before_idx} precedes (or coincides with) gated event at index {after_idx}",
                )
            )
        else:
            sessions.append(
                SessionOrderingResult(
                    session=session,
                    verdict="not_met",
                    detail=f"disclosure at index {before_idx} occurs AFTER gated event at index {after_idx}",
                )
            )

    return OrderingResult(sessions=tuple(sessions), considered_count=considered, skipped_count=skipped)
