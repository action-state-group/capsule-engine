# SPDX-License-Identifier: Apache-2.0
"""Rulesets for ``capsulectl engine check --ruleset FILE`` (capsule-engine#162).

An ``engine-ruleset/v0`` file names the clauses the engine recomputes on one
external-check-input/v0; ``check`` answers with one external-check-result/v0,
tier ``recomputed``. The format is ``schemas/engine-ruleset-v0.json``; its
``definition_digest`` is SHA-256 over the JCS bytes of the parsed document.
"""
from __future__ import annotations

from .check import RULESET_FORMAT, Refusal, Ruleset, check, load_ruleset

__all__ = ["RULESET_FORMAT", "Refusal", "Ruleset", "check", "load_ruleset"]
