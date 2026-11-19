# SPDX-License-Identifier: Apache-2.0
"""Countersign pack policy modules (capsule-engine#160, Amendment J.2).

The countersign engine (capsule-anchor) is neutral: it ships no pack code and
loads a ``PolicyModule`` (``check(bundle, profile_id) -> [CheckResult]``) by
profile id. The modules for the packs we own live here, beside the packs
they check. capsule-anchor is not a dependency: ``modules`` imports its
``DisclosureRefused`` base only when it is installed (so its 422 applies) and otherwise falls
back to ``ValueError``. A module is duck-typed against the bundle (``.records`` or a raw ``dict``) and returns
results with the same ``name`` / ``result`` / ``detail`` fields.
"""
from .modules import (
    DECLARED_COUNT,
    METHOD_FREEZE,
    PROFILE_IDS,
    CheckResult,
    CountersignPackModule,
    DisclosureRefused,
    register_all,
)

__all__ = [
    "CheckResult",
    "CountersignPackModule",
    "DECLARED_COUNT",
    "DisclosureRefused",
    "METHOD_FREEZE",
    "PROFILE_IDS",
    "register_all",
]
