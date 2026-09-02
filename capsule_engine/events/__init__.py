# SPDX-License-Identifier: Apache-2.0
"""Passive ``fyi`` event capsules -- degradation/recovery, policy-manifest
activation, conversation turns, tool-call reads, and anything else that
records what happened without gating or deciding it.

Deliberately guard-vocabulary-free (no ``ALLOW``/``DENY``/``ESCALATE``, no
``ConstraintOutcome``, no ``build_decision_capsule``): see ``capsule.py``'s
own docstring for why.
"""
from __future__ import annotations

from .capsule import build_event_capsule

__all__ = ["build_event_capsule"]
