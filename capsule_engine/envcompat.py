# SPDX-License-Identifier: Apache-2.0
# Vendored from action-state-group/capsule-ledger, capsule_ledger/envcompat.py,
# commit 696debcdf15c9c990491708dfa01f63faac9ffcb. Do not hand-edit -- re-run scripts/vendor_envcompat.py
# against a capsule-ledger checkout instead.
"""Env-var read helper."""
from __future__ import annotations

import os

__all__ = ["env_get"]


def env_get(name: str, default: str | None = None) -> str | None:
    """Read ``name`` from the environment, else ``default``."""
    return os.environ.get(name, default)
