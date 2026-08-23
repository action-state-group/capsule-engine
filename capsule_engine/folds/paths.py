# SPDX-License-Identifier: Apache-2.0
"""Dotted-path field resolution into capsule records (JSON objects)."""
from __future__ import annotations

from typing import Any

ABSENT = object()


def validate_path(path: Any) -> None:
    if not isinstance(path, str) or not path:
        raise ValueError(f"field path must be a non-empty string, got {path!r}")
    parts = path.split(".")
    if any(not p for p in parts):
        raise ValueError(f"field path {path!r} has an empty segment")


def get_path(record: dict, path: str, default: Any = ABSENT) -> Any:
    """Resolve a dotted path into a JSON object; missing at any level -> default."""
    cur: Any = record
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur
