# SPDX-License-Identifier: Apache-2.0
"""CLI-facing hooks: update local state idempotently, emit the corresponding
metric event -- all of it, including the local state file itself, gated on
``consent.is_opted_in()``. An install that never opts in leaves no
telemetry footprint at all: no state file, no local bookkeeping, nothing.

Each function only ever fires its event the first time the underlying fact
becomes true for this install (a guard is "configured" once; re-running
``guard dry-run --cap ...`` a second time updates nothing and emits
nothing new) -- these are one-shot facts, matching what ``events.py``'s
docstring says M1/M2/M5 are. ``record_guard_evaluated`` re-affirms
"alive" on every real evaluation (the state timestamp always advances) but
is still summarized centrally as a single per-install yes/no fact (M3), not
a per-evaluation count. ``record_install_seen`` is not one of the six
metrics -- it is the denominator bookkeeping (this install exists, in this
arm) the central funnel report needs to turn the six one-shot facts into
rates; see ``funnel.py``.
"""
from __future__ import annotations

from datetime import datetime, timezone

from . import events
from .consent import is_opted_in
from .sink import EventSink, emit
from .state import TelemetryState, load_state, save_state

__all__ = [
    "record_install_seen",
    "record_guard_configured",
    "record_enforcement_flip",
    "record_guard_evaluated",
    "record_evidence_touch",
]

_INSTALL_SEEN_METRIC = "install_seen"


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _emit_bookkeeping(install_id: str, arm: str, *, sink: EventSink | None) -> None:
    """Not one of the six metrics -- the denominator fact an install exists
    in a given arm, so the funnel report can turn one-shot facts into rates
    without needing an external cohort list."""
    emit(
        events.MetricEvent(metric=_INSTALL_SEEN_METRIC, arm=arm, install_id=install_id, value=True, emitted_at=_now_utc()),
        opted_in=True,
        sink=sink,
    )


def record_install_seen(arm: str, *, sink: EventSink | None = None) -> None:
    if not is_opted_in():
        return
    s = load_state()
    _emit_bookkeeping(s.install_id, arm, sink=sink)


def record_guard_configured(arm: str, *, sink: EventSink | None = None, state: TelemetryState | None = None) -> None:
    if not is_opted_in():
        return
    s = state or load_state()
    if s.first_guard_configured_at is not None:
        return
    s.first_guard_configured_at = _now_utc()
    save_state(s)
    emit(events.m1_activation_event(install_id=s.install_id, arm=arm), opted_in=True, sink=sink)


def record_enforcement_flip(arm: str, *, sink: EventSink | None = None, state: TelemetryState | None = None) -> None:
    if not is_opted_in():
        return
    s = state or load_state()
    if s.enforce_flipped_at is not None:
        return
    s.enforce_flipped_at = _now_utc()
    save_state(s)
    emit(events.m2_enforcement_on_event(install_id=s.install_id, arm=arm), opted_in=True, sink=sink)


def record_guard_evaluated(arm: str, *, sink: EventSink | None = None, state: TelemetryState | None = None) -> None:
    if not is_opted_in():
        return
    s = state or load_state()
    already_alive = s.last_guard_evaluated_at is not None
    s.last_guard_evaluated_at = _now_utc()
    save_state(s)
    if not already_alive:
        emit(events.m3_day14_alive_event(install_id=s.install_id, arm=arm), opted_in=True, sink=sink)


def record_evidence_touch(arm: str, *, sink: EventSink | None = None, state: TelemetryState | None = None) -> None:
    """M5, full arm only -- callers already gate this on the arm (the
    evidence-surfacing commands are only registered in the full arm), but
    this function re-asserts it so a future caller can't emit a
    meaningless guards-only M5 fact by mistake."""
    if arm != "full" or not is_opted_in():
        return
    s = state or load_state()
    if s.evidence_touched_at is not None:
        return
    s.evidence_touched_at = _now_utc()
    save_state(s)
    emit(events.m5_evidence_pull_event(install_id=s.install_id, arm=arm), opted_in=True, sink=sink)
