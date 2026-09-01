# SPDX-License-Identifier: Apache-2.0
"""Local, vendored registry-convention snapshots -- see ``conventions.py``."""
from __future__ import annotations

from .conventions import (
    ActionConvention,
    FieldConvention,
    conventions_digest,
    describe_action_class,
    describe_field_value,
)

__all__ = [
    "ActionConvention",
    "FieldConvention",
    "conventions_digest",
    "describe_action_class",
    "describe_field_value",
]
