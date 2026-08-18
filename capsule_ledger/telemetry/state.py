# SPDX-License-Identifier: Apache-2.0
"""Local, per-install state the six metrics are computed from.

Stored as a small JSON file outside the ledger and outside any repo --
never ledger data, never PII: a random install id and a handful of "first
time this happened" timestamps. Written regardless of telemetry opt-in (the
facts are needed locally either way, e.g. for ``capsule telemetry status``);
whether they are ever *emitted* anywhere is gated separately by
``consent.is_opted_in()``.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
from pathlib import Path

from ..envcompat import env_get

__all__ = ["TelemetryState", "state_path", "load_state", "save_state"]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def state_path() -> Path:
    override = env_get("CAPSULE_LEDGER_STATE_DIR")
    base = Path(override) if override else Path.home() / ".local" / "state" / "capsule-ledger"
    return base / "telemetry_state.json"


@dataclass
class TelemetryState:
    install_id: str
    install_at: str
    first_guard_configured_at: str | None = None
    enforce_flipped_at: str | None = None
    last_guard_evaluated_at: str | None = None
    evidence_touched_at: str | None = None


def load_state(path: Path | None = None) -> TelemetryState:
    """Load the local state, creating it (a fresh random install id, "now"
    as the install timestamp) on first ever call."""
    p = path or state_path()
    if p.exists():
        raw = json.loads(p.read_text(encoding="utf-8"))
        known = {f.name for f in fields(TelemetryState)}
        return TelemetryState(**{k: v for k, v in raw.items() if k in known})
    state = TelemetryState(install_id=str(uuid.uuid4()), install_at=_utc_now())
    save_state(state, p)
    return state


def save_state(state: TelemetryState, path: Path | None = None) -> None:
    p = path or state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(asdict(state), indent=2, sort_keys=True), encoding="utf-8")
