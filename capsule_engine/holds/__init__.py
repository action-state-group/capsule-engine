# SPDX-License-Identifier: Apache-2.0
"""Reservation-as-capsule semantics: ``hold.reserve``/``release``/``expire``/
``reconcile`` record types, atomic evaluate-and-reserve, expiry-terminal +
resume, and planned-vs-executed reconciliation (capsule-emit #51/#52/#53).

Ledger-side only: the distributed sequencer across processes/nodes is
capsule-emit's Dapr-side integration, not this package's.
"""
from .capsules import (
    build_hold_expire_capsule,
    build_hold_reconcile_capsule,
    build_hold_release_capsule,
    build_hold_reserve_capsule,
    check_integer_amount,
)
from .engine import HoldDecision, HoldEngine, HoldStatus
from .errors import HoldError
from .policy import HoldPolicy, resolve_hold_policy
from .scope import ScopeKey, ScopeLocks

__all__ = [
    "build_hold_reserve_capsule",
    "build_hold_release_capsule",
    "build_hold_expire_capsule",
    "build_hold_reconcile_capsule",
    "check_integer_amount",
    "HoldDecision",
    "HoldEngine",
    "HoldStatus",
    "HoldError",
    "HoldPolicy",
    "resolve_hold_policy",
    "ScopeKey",
    "ScopeLocks",
]
