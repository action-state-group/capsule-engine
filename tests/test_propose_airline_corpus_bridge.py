# SPDX-License-Identifier: Apache-2.0
"""``[ldg-propose-airline-corpus-bridge]``: ``capsule setup propose`` over a
real ``GuardEngine`` decision-capsule ledger (plan_containment-checked
action capsules -- what a replayed tau2 airline trace produces), not
``setup observe``'s own dispatch/confirmation dry-run pair.

**The bug this closes.** Before ``DecisionCandidate``/``_decision_coverage``
(``setup/candidates.py``, ``setup/propose.py``), ``propose``'s only
evidence rule (``_attainment_coverage``) reads ``action_type == "fyi"``
capsules carrying an emit-layer ``asg_payload.event`` -- the committed
airline ledger fixture below has NEITHER: its one real action capsule is
``action_type == "decide"`` with ``asg_payload.action_class`` set directly
(``guards/capsule.py``'s canonical ``build_decision_capsule`` shape), so
``propose`` found zero matches against it, even unmodified. The first test
below is RED without the bridge (``next()`` over an empty generator raises
``StopIteration`` -- there is no ``outcome.change_authorized`` candidate at
all) and GREEN with it.

**The fixture.** ``examples/live_compile_demo/fixtures/tau2_airline_task40_
trial0.jsonl`` -- one real tau2-bench airline trajectory replayed offline,
no network, via record-grounding-bench's ``rgb shift replay``
(``[ldg-tau2-replay-adapter]``); reused as-is, not regenerated.
"""
from __future__ import annotations

from pathlib import Path

from capsule_ledger.setup.candidates import DecisionCandidate
from capsule_ledger.setup.propose import propose_from_ledger

AIRLINE_FIXTURE = (
    Path(__file__).parent.parent
    / "capsule_ledger"
    / "examples"
    / "live_compile_demo"
    / "fixtures"
    / "tau2_airline_task40_trial0.jsonl"
)


def _load_airline_ledger(store) -> int:
    assert AIRLINE_FIXTURE.is_file(), f"missing committed fixture: {AIRLINE_FIXTURE}"
    return store.import_jsonl(AIRLINE_FIXTURE)


def _append_decision(store, capsule_id: str, *, action_class: str, decision: str) -> None:
    store.append(
        {
            "capsule_id": capsule_id,
            "action_type": "decide",
            "asg_payload": {"action_class": action_class},
            "disposition": {"decision": decision},
        },
        consequential=False,
    )


def test_default_catalog_finds_a_real_match_against_the_replayed_airline_ledger(store):
    """GREEN: the fixture's one booking.modify decision capsule was a
    REJECT (verify_before_dispatch failed -- see the fixture's own
    constraints array), so this honestly reports 0 of 1, never a
    flattered number."""
    count = _load_airline_ledger(store)
    assert count == 17  # the replay adapter's own committed record count -- [ldg-tau2-replay-adapter]
    proposal_set = propose_from_ledger(store)
    outcome = next(p for p in proposal_set.proposals if p.outcome_id == "outcome.change_authorized")
    assert outcome.coverage_n == 0
    assert outcome.coverage_m == 1
    assert outcome.forward_verdict == "DETERMINISTIC"
    assert outcome.backward_verdict == "DETERMINISTIC"
    assert "booking.modify" in outcome.rationale


def test_a_candidate_with_no_evidence_in_this_ledger_is_correctly_absent(store):
    """The bridge must not spuriously match everything: the default
    'remediation' attainment candidate has zero evidence in this ledger
    (no setup-observe dispatch/confirmation pair, and no decide-capsule of
    that action_class either) and must not appear -- a candidate that
    should NOT match, staying absent rather than reporting a false 0-of-0."""
    _load_airline_ledger(store)
    proposal_set = propose_from_ledger(store)
    outcome_ids = {p.outcome_id for p in proposal_set.proposals}
    assert "outcome.remediation_confirmed" not in outcome_ids
    assert "outcome.change_authorized" in outcome_ids


def test_decision_coverage_is_scoped_per_action_class(store):
    """Two decision capsules of DIFFERENT action_classes must not bleed
    into each other's coverage, and a class absent from the ledger stays
    absent (not a spurious 0 of 0)."""
    _append_decision(store, "d1", action_class="alpha", decision="accept")
    _append_decision(store, "d2", action_class="alpha", decision="reject")
    _append_decision(store, "d3", action_class="beta", decision="accept")

    candidates = (
        DecisionCandidate(outcome_id="outcome.alpha", statement="alpha", action_class="alpha"),
        DecisionCandidate(outcome_id="outcome.beta", statement="beta", action_class="beta"),
        DecisionCandidate(outcome_id="outcome.gamma", statement="gamma", action_class="gamma"),
    )
    proposal_set = propose_from_ledger(store, candidates=candidates)
    by_id = {p.outcome_id: p for p in proposal_set.proposals}

    assert by_id["outcome.alpha"].coverage_n == 1
    assert by_id["outcome.alpha"].coverage_m == 2
    assert by_id["outcome.beta"].coverage_n == 1
    assert by_id["outcome.beta"].coverage_m == 1
    assert "outcome.gamma" not in by_id


def test_decision_candidate_round_trips_through_the_declaration_store(store, tmp_path):
    """``candidate_to_canonical_dict``/``candidate_from_canonical_dict`` and
    ``DeclarationStore`` must handle the new kind exactly like the other
    three -- persist_proposals -> DeclarationStore.load must reproduce the
    same candidate, the same property every other kind already has."""
    from capsule_ledger.setup.declarations import DeclarationStore
    from capsule_ledger.setup.propose import persist_proposals

    _load_airline_ledger(store)
    proposal_set = propose_from_ledger(store)
    decl_store = DeclarationStore(tmp_path)
    persist_proposals(proposal_set, decl_store)

    stored = decl_store.load("outcome.change_authorized")
    assert isinstance(stored.candidate, DecisionCandidate)
    assert stored.candidate.action_class == "booking.modify"
    assert stored.forward_verdict == "DETERMINISTIC"
    assert stored.backward_verdict == "DETERMINISTIC"
