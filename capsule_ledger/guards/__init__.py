# SPDX-License-Identifier: Apache-2.0
"""Guard API: checks that gate actions and record their own outcomes.

``GuardEngine.check(action) -> allow | deny | escalate``, with every
decision appended to the ledger as a capsule (T2's ``LedgerStore.append``).
See ``docs/failure-semantics.md`` for the guard's failure/degradation
behavior.
"""
from .action import Action
from .capsule import ALLOW, DENY, ESCALATE, ConstraintOutcome, build_decision_capsule, build_event_capsule
from .classes import ActionClass, classify
from .engine import GuardDecision, GuardEngine
from .revocation import (
    ROTATION_EVENT,
    KeyWindow,
    RevocationFinding,
    build_key_timeline,
    check_time_fenced_revocation,
)
from .signing import LocalSigner, Signer, SigningKeyUnavailable, key_fingerprint

__all__ = [
    "Action",
    "ALLOW",
    "DENY",
    "ESCALATE",
    "ConstraintOutcome",
    "build_decision_capsule",
    "build_event_capsule",
    "ActionClass",
    "classify",
    "GuardDecision",
    "GuardEngine",
    "LocalSigner",
    "Signer",
    "SigningKeyUnavailable",
    "key_fingerprint",
    "ROTATION_EVENT",
    "KeyWindow",
    "RevocationFinding",
    "build_key_timeline",
    "check_time_fenced_revocation",
]
