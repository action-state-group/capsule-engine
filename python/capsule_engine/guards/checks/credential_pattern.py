# SPDX-License-Identifier: Apache-2.0
"""credential_pattern check: regular-expression match over outgoing content.

Fails when ``Action.outgoing_content`` matches any configured pattern (for
example a one-time code or a password). Nothing derived from the content --
no hash, no length -- reaches the capsule: the evidence carries only the ids
of the patterns that matched and of the patterns checked. A hash of short
content such as a six-digit code would be reversible by enumeration from the
sealed ``evidence_digest``, so two contents with the same matches seal the
same digest by design.
"""
from __future__ import annotations

import re
from typing import TypedDict

from ..action import Action
from ..capsule import ConstraintOutcome, not_applicable_evidence
from .base import CheckOutcome

__all__ = ["CredentialEvidence", "CredentialPattern", "check_credential_pattern"]

_CHECK_ID = "credential_pattern"
_METHOD = "regex_match_v0"


class CredentialPattern(TypedDict):
    """One entry of the credential_pattern wicket's ``patterns`` config."""

    id: str
    regex: str


class CredentialEvidence(TypedDict):
    matched_pattern_ids: list[str]
    pattern_ids: list[str]


def check_credential_pattern(action: Action, *, patterns: list[CredentialPattern]) -> CheckOutcome:
    if action.outgoing_content is None:
        return CheckOutcome(
            constraint=ConstraintOutcome(
                id=_CHECK_ID,
                result="n/a",
                reason="the action sends no outgoing content",
                evidence=not_applicable_evidence(_CHECK_ID, in_scope=False),
                check_type="policy",
                method=_METHOD,
            )
        )
    matched = sorted(p["id"] for p in patterns if re.search(p["regex"], action.outgoing_content))
    evidence = CredentialEvidence(matched_pattern_ids=matched, pattern_ids=sorted(p["id"] for p in patterns))
    if matched:
        return CheckOutcome(
            constraint=ConstraintOutcome(
                id=_CHECK_ID,
                result="fail",
                reason=f"outgoing content matches {matched}",
                evidence=evidence,
                check_type="policy",
                method=_METHOD,
            )
        )
    return CheckOutcome(
        constraint=ConstraintOutcome(
            id=_CHECK_ID,
            result="pass",
            reason="outgoing content matches no configured pattern",
            evidence=evidence,
            check_type="policy",
            method=_METHOD,
        )
    )
