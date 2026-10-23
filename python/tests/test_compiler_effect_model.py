# SPDX-License-Identifier: Apache-2.0
"""The advisory effect model (design §4b gap 1): agent.caused_resolution
MUST compile REFUSED; recommendation.acted_on and resolution.followed_action
are the admissible near-misses."""
from __future__ import annotations

import pytest

from capsule_engine.compiler.effect_model import (
    ADMISSIBLE_EFFECT_CLAIMS,
    REFUSED_EFFECT_CLAIMS,
    UnknownEffectClaim,
    compile_effect_claim,
)


@pytest.mark.parametrize("claim", sorted(ADMISSIBLE_EFFECT_CLAIMS))
def test_admissible_claims_compile_deterministic_both_ways(claim):
    compiled = compile_effect_claim(claim)
    assert compiled.verdict.forward == "DETERMINISTIC"
    assert compiled.verdict.backward == "DETERMINISTIC"
    assert compiled.refusal_reason_code is None


@pytest.mark.parametrize("claim", sorted(REFUSED_EFFECT_CLAIMS))
def test_the_undecomposable_claim_compiles_refused_both_ways_not_raises(claim):
    """Refusing is the successful, expected outcome for this claim -- it
    must not raise. The claim can only be recorded, never proven."""
    compiled = compile_effect_claim(claim)
    assert compiled.verdict.forward == "REFUSED"
    assert compiled.verdict.backward == "REFUSED"
    assert compiled.refusal_reason_code == "agent_caused_resolution_undecomposable"


def test_agent_caused_resolution_is_the_reserved_claim_name():
    assert "agent.caused_resolution" in REFUSED_EFFECT_CLAIMS


def test_unknown_effect_claim_raises():
    with pytest.raises(UnknownEffectClaim):
        compile_effect_claim("agent.definitely_caused_it")
