# SPDX-License-Identifier: Apache-2.0
"""Duration string parsing for rolling windows (spec §2: "rolling duration")."""
from __future__ import annotations

import re

_DURATION_RE = re.compile(r"^(\d+)([smhd])$")
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_duration_seconds(duration: str) -> int:
    match = _DURATION_RE.match(duration)
    if not match:
        raise ValueError(f"duration {duration!r} must match '<int>[smhd]' (e.g. '7d', '24h')")
    count, unit = match.groups()
    return int(count) * _UNIT_SECONDS[unit]
