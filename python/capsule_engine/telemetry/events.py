# SPDX-License-Identifier: Apache-2.0
"""The six raw metric-shaped events this package ever emits.

Every field here is fixed by ``MetricEvent``'s own schema -- there is no
free-form dict anywhere in this module, so a payload structurally cannot
carry anything beyond ``metric``/``arm``/``install_id``/``value``/
``emitted_at``. That is what makes "no ledger data, no PII" a property of
the type, not a promise about how callers happen to use it.

This module computes and shapes *values*, never a verdict. Nothing here
knows what counts as a good or bad result for any metric -- that judgment
is made elsewhere, later, privately, against numbers these events make
available. See ``funnel.py`` for the one place raw counts get turned into a
rate, and note it stops at the rate: no pass/worry/fail banding lives in
this repository.

M1 (activation), M2 (enforcement-on), M3 (day-14-alive), and M5
(evidence-pull, full arm only) are each a single install's own yes/no fact,
emitted once. M4 (the arm-A-vs-arm-B evidence tax) is a ratio computed
*centrally* across every install's M1 facts -- an individual install has no
visibility into the other arm's aggregate, so this module deliberately
does not attempt to compute it; ``m1_activation_event`` already carries
everything a central aggregator needs (the arm tag) to compute M4 later.
M6 (the viral unit) is not built here at all -- see
``report/render.py``'s ``TelemetryConfig`` for the client-side beacon that
carries it, and this module's own docstring note below on why.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

__all__ = [
    "MetricEvent",
    "ALLOWED_FIELDS",
    "m1_activation_event",
    "m2_enforcement_on_event",
    "m3_day14_alive_event",
    "m5_evidence_pull_event",
]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class MetricEvent:
    """One raw metric fact. ``value`` is always metric-shaped: a bool for a
    yes/no fact, an int for a count -- never a string that could carry free
    text, never a nested object that could carry a ledger record."""

    metric: str
    arm: str
    install_id: str
    value: bool | int
    emitted_at: str

    def to_dict(self) -> dict:
        return {
            "metric": self.metric,
            "arm": self.arm,
            "install_id": self.install_id,
            "value": self.value,
            "emitted_at": self.emitted_at,
        }


ALLOWED_FIELDS = frozenset({"metric", "arm", "install_id", "value", "emitted_at"})


def m1_activation_event(*, install_id: str, arm: str, activated: bool = True) -> MetricEvent:
    """>=1 guard configured within the activation window (also the per-install
    fact M4 is computed centrally from -- see module docstring)."""
    return MetricEvent(metric="m1_activation", arm=arm, install_id=install_id, value=activated, emitted_at=_utc_now())


def m2_enforcement_on_event(*, install_id: str, arm: str, flipped: bool = True) -> MetricEvent:
    """dry_run flipped to enforce, of installs that activated."""
    return MetricEvent(metric="m2_enforcement_on", arm=arm, install_id=install_id, value=flipped, emitted_at=_utc_now())


def m3_day14_alive_event(*, install_id: str, arm: str, alive: bool = True) -> MetricEvent:
    """A guard evaluated >=1 action recently, of installs that activated."""
    return MetricEvent(metric="m3_day14_alive", arm=arm, install_id=install_id, value=alive, emitted_at=_utc_now())


def m5_evidence_pull_event(*, install_id: str, arm: str, touched: bool = True) -> MetricEvent:
    """An evidence feature (permalink, verify, bundle/share) was touched
    unprompted. Callers should only build this for the full arm -- there is
    no evidence surface to touch in guards-only, so the value would be
    meaningless there."""
    return MetricEvent(metric="m5_evidence_pull", arm=arm, install_id=install_id, value=touched, emitted_at=_utc_now())
