# SPDX-License-Identifier: Apache-2.0
"""Where an opted-in event goes. No sink here ever makes a network call --
this package ships no telemetry backend and points at none by default; a
real transport is a deployment-time concern for whoever operates one,
wired in by passing a different ``EventSink``, never by editing this file.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from ..envcompat import env_get
from .events import ALLOWED_FIELDS, MetricEvent

__all__ = ["EventSink", "NullSink", "LocalJSONLSink", "default_sink", "emit"]


class EventSink(Protocol):
    def write(self, event: MetricEvent) -> None: ...


class NullSink:
    """The default: opted-in or not, nothing leaves this process."""

    def write(self, event: MetricEvent) -> None:
        return None


class LocalJSONLSink:
    """Append-only local file, one JSON object per line. Intended for
    operators who want to inspect or forward their own install's telemetry
    themselves -- this package never reads this file back or transmits it."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def write(self, event: MetricEvent) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event.to_dict(), sort_keys=True) + "\n")


def default_sink() -> EventSink:
    path = env_get("CAPSULE_LEDGER_TELEMETRY_SINK_PATH")
    return LocalJSONLSink(path) if path else NullSink()


def emit(event: MetricEvent, *, opted_in: bool, sink: EventSink | None = None) -> bool:
    """Write ``event`` to ``sink`` iff ``opted_in``. Returns whether it was
    written. Re-validates the payload's keys against the fixed schema before
    writing -- belt and suspenders alongside ``MetricEvent`` itself being a
    closed dataclass, so a future edit to this module can't silently widen
    what a payload may carry."""
    if not opted_in:
        return False
    payload = event.to_dict()
    if set(payload) != ALLOWED_FIELDS:
        raise ValueError(f"telemetry payload carries unexpected fields: {set(payload) - ALLOWED_FIELDS}")
    (sink or default_sink()).write(event)
    return True
