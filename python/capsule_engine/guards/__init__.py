# SPDX-License-Identifier: Apache-2.0
"""Guard API: checks that gate actions and record their own outcomes.

``GuardEngine.check(action) -> allow | deny | escalate``, with every
decision appended to the ledger as a capsule (T2's ``LedgerStore.append``).
See ``docs/failure-semantics.md`` for the guard's failure/degradation
behavior.

Does NOT re-export ``build_event_capsule`` (moved to ``events.capsule`` in
the W3 split, 2026-09-01) -- a prior backward-compat re-export here
recreated the exact circular import (``guards`` -> ``events.capsule`` ->
``guards.signing``) that split was meant to eliminate: anything that
imported ``events``/``conversation`` before ``guards`` finished
initializing hit ``ImportError: cannot import name 'build_event_capsule'
from partially initialized module`` (found live 2026-09-02, real CI
failure in a downstream repo). Every internal caller here already imports
``events.capsule`` directly; import from there.
"""
from .action import Action
from .capsule import ALLOW, DENY, ESCALATE, ConstraintOutcome, build_decision_capsule
from .classes import ActionClass, classify
from .engine import GuardDecision, GuardEngine
from .plan import PlanDefinition, PlanPrecondition, parse_plan_definition
from .revocation import (
    ROTATION_EVENT,
    KeyWindow,
    RevocationFinding,
    build_key_timeline,
    check_time_fenced_revocation,
)
from .signing import LocalSigner, Signer, SigningKeyUnavailable, key_fingerprint
from .tool_call import TOOL_CALL_LANE, ToolCallLane

__all__ = [
    "Action",
    "ALLOW",
    "DENY",
    "ESCALATE",
    "ConstraintOutcome",
    "build_decision_capsule",
    "ActionClass",
    "classify",
    "GuardDecision",
    "GuardEngine",
    "PlanDefinition",
    "PlanPrecondition",
    "parse_plan_definition",
    "LocalSigner",
    "Signer",
    "SigningKeyUnavailable",
    "key_fingerprint",
    "ROTATION_EVENT",
    "KeyWindow",
    "RevocationFinding",
    "build_key_timeline",
    "check_time_fenced_revocation",
    "TOOL_CALL_LANE",
    "ToolCallLane",
]
