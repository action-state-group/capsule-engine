# SPDX-License-Identifier: Apache-2.0
# Copied from action-state-group/capsule-ledger, capsule_ledger/envcompat.py, at commit
# 696debcdf15c9c990491708dfa01f63faac9ffcb. capsule-ledger is archived, so this copy is now the
# maintained one.
"""Env-var read helper, honouring the pre-rename ``CAPSULE_LEDGER_*`` names."""
from __future__ import annotations

import os

__all__ = ["env_get", "LEGACY_ALIASES"]

# New name -> the name it had under capsule-ledger. The old name is read when the new one is
# unset, for one release (removed in 0.5.0); the new name always wins.
LEGACY_ALIASES = {
    "CAPSULE_ARM": "CAPSULE_LEDGER_ARM",
    "CAPSULE_STATE_DIR": "CAPSULE_LEDGER_STATE_DIR",
    "CAPSULE_TELEMETRY": "CAPSULE_LEDGER_TELEMETRY",
    "CAPSULE_TELEMETRY_SINK_PATH": "CAPSULE_LEDGER_TELEMETRY_SINK_PATH",
}


def env_get(name: str, default: str | None = None) -> str | None:
    """Read ``name`` from the environment, else its legacy alias, else ``default``."""
    value = os.environ.get(name)
    if value is None and name in LEGACY_ALIASES:
        value = os.environ.get(LEGACY_ALIASES[name])
    return default if value is None else value
