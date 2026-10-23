# SPDX-License-Identifier: Apache-2.0
# Copied from action-state-group/capsule-ledger, capsule_ledger/envcompat.py, at commit
# 696debcdf15c9c990491708dfa01f63faac9ffcb. capsule-ledger is archived, so this copy is now the
# maintained one.
"""Env-var read helper."""
from __future__ import annotations

import os

__all__ = ["env_get"]


def env_get(name: str, default: str | None = None) -> str | None:
    """Read ``name`` from the environment, else ``default``."""
    return os.environ.get(name, default)
