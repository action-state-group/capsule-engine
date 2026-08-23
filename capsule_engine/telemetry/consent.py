# SPDX-License-Identifier: Apache-2.0
"""Opt-in gate for telemetry. Off unless a real person turned it on.

No default-on path exists anywhere in this module: ``is_opted_in()`` returns
``False`` for every environment except an explicit, plainly-worded env var
value. There is no persisted "you already agreed" state that survives an
upgrade or a fresh install silently re-enabling anything.
"""
from __future__ import annotations

from ..envcompat import env_get

__all__ = ["DISCLOSURE_TEXT", "is_opted_in", "ENV_VAR"]

ENV_VAR = "CAPSULE_LEDGER_TELEMETRY"
_TRUE_VALUES = {"1", "true", "on", "yes"}

DISCLOSURE_TEXT = f"""\
capsule-ledger telemetry (off by default):

If you turn this on, this install reports six yes/no or count-shaped facts
about how the guard package gets used -- e.g. "was a guard configured
within 2 days of install", never *what* was configured, blocked, or held.
No agent name, counterparty, amount, action content, or ledger data of any
kind is ever included. Each report carries only: which metric, which
packaging arm (guards-only or full), a random install id that identifies
no person or organization, the fact/count itself, and a timestamp.

Reports are aggregated across installs; nothing here computes or stores an
individual verdict about your own usage.

Turn on with: {ENV_VAR}=1
Check current status any time: capsule telemetry status
Turn off: unset {ENV_VAR} (or set it to 0)
"""


def is_opted_in() -> bool:
    return (env_get(ENV_VAR, "") or "").strip().lower() in _TRUE_VALUES
