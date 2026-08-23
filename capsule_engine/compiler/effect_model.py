# SPDX-License-Identifier: Apache-2.0
"""The advisory effect model (design §4b gap 1): every effect model this
codebase had before this wave assumed the agent caused the state change it
observed. A read-only advisory deployment's agent cannot act at all -- a
person acts on its recommendations manually -- so any field that quietly
encodes agent-causation breaks visibly there instead of invisibly at a
deployment that never falsifies it.

Three claims, closed set. Two are legitimate near-misses; the third MUST be
refused at compile time, and this module is where that refusal is
mechanical rather than a reviewer's judgment call: ``compile_effect_claim``
returns a verdict pair plus (for the refused claim) the reason code, and
raises nothing -- refusing IS the successful, expected return for that
claim, not an exceptional path. ``packs/loader.py`` is what turns an
Outcome that gets this wrong into a load-time error (an outcome cannot
declare ``effect_claim="agent.caused_resolution"`` with anything other than
the verdict pair this module computes for it).
"""
from __future__ import annotations

from dataclasses import dataclass

from .vocabulary import VerdictPair

__all__ = [
    "ADMISSIBLE_EFFECT_CLAIMS",
    "REFUSED_EFFECT_CLAIMS",
    "EFFECT_CLAIMS",
    "UnknownEffectClaim",
    "EffectClaimCompilation",
    "compile_effect_claim",
]

# The two legitimate near-misses (design §4b gap 1). Both are recordable
# without asserting causation: a human's action can be cited by its own
# capsule (``recommendation.acted_on``) or the ordering of a resolution
# after an action can be cited without asserting the action produced it
# (``resolution.followed_action``).
ADMISSIBLE_EFFECT_CLAIMS = frozenset({"recommendation.acted_on", "resolution.followed_action"})

# The undecomposable claim. A resolved ticket following a recommendation is
# correlation, not causation -- the record can honestly attest the
# recommendation was made, a human acted, the ticket resolved, and the
# elapsed order of those, but never that the agent caused the resolution.
REFUSED_EFFECT_CLAIMS = frozenset({"agent.caused_resolution"})

EFFECT_CLAIMS = ADMISSIBLE_EFFECT_CLAIMS | REFUSED_EFFECT_CLAIMS


class UnknownEffectClaim(ValueError):
    """``effect_claim`` is not one of the closed ``EFFECT_CLAIMS`` set --
    same "unregistered is a typo" doctrine as every other closed vocabulary
    in this repo; this is not an open, registry-resolved vocabulary."""


@dataclass(frozen=True)
class EffectClaimCompilation:
    effect_claim: str
    verdict: VerdictPair
    refusal_reason_code: str | None = None


def compile_effect_claim(effect_claim: str) -> EffectClaimCompilation:
    """Compile one effect claim into its verdict pair. The refused claim
    compiles successfully -- to a REFUSED/REFUSED pair with its reason code
    -- rather than raising, because the refusal itself is the correct,
    recordable outcome (design §4: "a report that visibly refuses one claim
    is a report whose other claims get believed")."""
    if effect_claim in ADMISSIBLE_EFFECT_CLAIMS:
        return EffectClaimCompilation(
            effect_claim=effect_claim, verdict=VerdictPair(forward="DETERMINISTIC", backward="DETERMINISTIC")
        )
    if effect_claim in REFUSED_EFFECT_CLAIMS:
        return EffectClaimCompilation(
            effect_claim=effect_claim,
            verdict=VerdictPair(forward="REFUSED", backward="REFUSED"),
            refusal_reason_code="agent_caused_resolution_undecomposable",
        )
    raise UnknownEffectClaim(f"effect_claim must be one of {sorted(EFFECT_CLAIMS)}; got {effect_claim!r}")
