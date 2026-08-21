# SPDX-License-Identifier: Apache-2.0
import pytest

from capsule_engine.setup.candidates import (
    DEFAULT_CANDIDATES,
    AttainmentCandidate,
    DecisionCandidate,
    OfferResponseCandidate,
    RefusedCandidate,
    candidate_from_canonical_dict,
    candidate_to_canonical_dict,
)


def test_default_candidates_cover_all_four_kinds():
    kinds = {c.kind for c in DEFAULT_CANDIDATES}
    assert kinds == {"attainment", "offer_response", "refused", "decision"}


def test_default_candidates_exercise_both_seeded_refusal_reason_codes():
    reason_codes = {c.reason_code for c in DEFAULT_CANDIDATES if isinstance(c, RefusedCandidate)}
    assert reason_codes == {"unbounded_goal_unmonitorable", "agent_caused_resolution_undecomposable"}


@pytest.mark.parametrize(
    "candidate",
    [
        AttainmentCandidate(outcome_id="outcome.a", statement="a", action_class="verb_a"),
        OfferResponseCandidate(outcome_id="outcome.b", statement="b", offer_namespace="ns"),
        RefusedCandidate(outcome_id="outcome.c", statement="c", reason_code="unbounded_goal_unmonitorable"),
        RefusedCandidate(
            outcome_id="outcome.d", statement="d", reason_code="agent_caused_resolution_undecomposable",
            effect_claim="agent.caused_resolution",
        ),
        DecisionCandidate(outcome_id="outcome.e", statement="e", action_class="verb_e"),
    ],
)
def test_canonical_dict_round_trips(candidate):
    d = candidate_to_canonical_dict(candidate)
    reconstructed = candidate_from_canonical_dict(d)
    assert reconstructed == candidate


def test_canonical_dict_is_stable_field_order_independent():
    c = AttainmentCandidate(outcome_id="outcome.a", statement="a", action_class="verb_a")
    d1 = candidate_to_canonical_dict(c)
    d2 = candidate_to_canonical_dict(c)
    assert d1 == d2


def test_a_planted_drift_changes_the_canonical_dict():
    """The mutant this proves against: silently widening ``action_class``
    after acceptance must be VISIBLE in the canonical dict -- that
    visibility is exactly what ``propose.diff_against_stored`` compares."""
    original = AttainmentCandidate(outcome_id="outcome.a", statement="a", action_class="narrow_verb")
    drifted = AttainmentCandidate(outcome_id="outcome.a", statement="a", action_class="broad_verb")
    assert candidate_to_canonical_dict(original) != candidate_to_canonical_dict(drifted)
