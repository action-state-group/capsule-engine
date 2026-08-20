# SPDX-License-Identifier: Apache-2.0
import pytest

from capsule_engine.setup.candidates import AttainmentCandidate, RefusedCandidate
from capsule_engine.setup.declarations import DeclarationNotFound, DeclarationStore, candidate_digest


def test_save_and_load_round_trips_the_candidate(tmp_path):
    store = DeclarationStore(tmp_path)
    c = AttainmentCandidate(outcome_id="outcome.x", statement="x happened", action_class="verb_x")
    store.save(c, acceptance_state="proposed", forward_verdict="DETERMINISTIC", backward_verdict="DETERMINISTIC")

    stored = store.load("outcome.x")
    assert stored.candidate == c
    assert stored.acceptance_state == "proposed"
    assert stored.forward_verdict == "DETERMINISTIC"
    assert stored.d_digest == candidate_digest(c)


def test_load_missing_outcome_raises():
    store = DeclarationStore("/tmp/does-not-matter")
    with pytest.raises(DeclarationNotFound):
        store.load("outcome.nope")


def test_set_acceptance_state_preserves_verdict_fields(tmp_path):
    store = DeclarationStore(tmp_path)
    c = RefusedCandidate(outcome_id="outcome.r", statement="r", reason_code="unbounded_goal_unmonitorable")
    store.save(c, acceptance_state="proposed", forward_verdict="REFUSED", backward_verdict="REFUSED", refusal_reason_code="unbounded_goal_unmonitorable")

    updated = store.set_acceptance_state("outcome.r", "refused")
    assert updated.acceptance_state == "refused"
    assert updated.forward_verdict == "REFUSED"
    assert updated.refusal_reason_code == "unbounded_goal_unmonitorable"


def test_save_rejects_unknown_acceptance_state(tmp_path):
    store = DeclarationStore(tmp_path)
    c = AttainmentCandidate(outcome_id="outcome.x", statement="x", action_class="v")
    with pytest.raises(ValueError):
        store.save(c, acceptance_state="bogus")


def test_list_ids_and_exists(tmp_path):
    store = DeclarationStore(tmp_path)
    assert store.list_ids() == []
    assert not store.exists("outcome.x")
    store.save(AttainmentCandidate(outcome_id="outcome.x", statement="x", action_class="v"))
    assert store.list_ids() == ["outcome.x"]
    assert store.exists("outcome.x")


def test_candidate_digest_is_stable_across_save_load(tmp_path):
    store = DeclarationStore(tmp_path)
    c = AttainmentCandidate(outcome_id="outcome.x", statement="x happened", action_class="verb_x")
    d1 = candidate_digest(c)
    store.save(c)
    stored = store.load("outcome.x")
    assert candidate_digest(stored.candidate) == d1


def test_outcome_id_with_slash_does_not_collide_with_underscore_form(tmp_path):
    """A naive ``outcome_id.replace('/', '__')`` filename scheme collides
    ``"a/b"`` with a literal ``"a__b"`` -- both would map to the same file.
    The store must keep them as two distinct, independently loadable
    entries."""
    store = DeclarationStore(tmp_path)
    store.save(AttainmentCandidate(outcome_id="a/b", statement="s1", action_class="v1"))
    store.save(AttainmentCandidate(outcome_id="a__b", statement="s2", action_class="v2"))
    assert sorted(store.list_ids()) == ["a/b", "a__b"]
    assert store.load("a/b").candidate.statement == "s1"
    assert store.load("a__b").candidate.statement == "s2"
