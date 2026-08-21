# SPDX-License-Identifier: Apache-2.0
"""``[ldg-propose-live-drafting-mode]``: the opt-in ``capsule setup propose
--drafter`` seam. ``StaticRationaleDrafter`` is the deterministic, no-
network reference (mirrors the judge harness's ``StaticScorer``);
``DeepEvalRationaleDrafter`` is the default BYOM implementation -- its real-
wiring tests only run when ``deepeval`` is actually installed, and the
"not installed" path is exercised unconditionally since that's this repo's
own default state (same convention as ``tests/test_judge_scorers.py``).

The load-bearing property under test throughout: drafting touches
``ProposedOutcome.rationale`` and NOTHING else -- forward/backward verdict,
coverage_n/coverage_m, missing_instrument, refusal_reason_code, and the
frozen declaration digest a candidate compiles to are byte-identical
whether or not a drafter ran.
"""
from __future__ import annotations

import dataclasses
import io

import pytest

from capsule_ledger.setup.declarations import DeclarationStore
from capsule_ledger.setup.observe import ObserveRecorder
from capsule_ledger.setup.propose import propose_from_ledger
from capsule_ledger.setup.prose_drafter import (
    DRAFTER_DEPENDENCY_MISSING,
    DeepEvalRationaleDrafter,
    DrafterError,
    RationaleDrafter,
    StaticRationaleDrafter,
    draft_rationales,
)


def _observe(store, signer, events):
    recorder = ObserveRecorder(
        ledger=store, signer=signer, operator="op", developer="dev", heartbeat_every=0, heartbeat_stream=io.StringIO()
    )
    return recorder.run(events)


def _seeded_proposal_set(store, signer):
    events = []
    for i in range(1, 6):
        events.append({"kind": "dispatch", "dispatch_id": f"d{i}", "action_class": "remediation", "tool": "remediate"})
    for i in (1, 2):
        events.append({"kind": "confirmation", "commitment_ref": f"d{i}", "status": "confirmed"})
    _observe(store, signer, events)
    return propose_from_ledger(store)


# -- StaticRationaleDrafter ---------------------------------------------------


def test_static_rationale_drafter_satisfies_the_protocol():
    assert isinstance(StaticRationaleDrafter(), RationaleDrafter)


def test_static_rationale_drafter_is_deterministic(store, signer):
    proposal_set = _seeded_proposal_set(store, signer)
    outcome = proposal_set.proposals[0]
    drafter = StaticRationaleDrafter()
    assert drafter.draft(outcome) == drafter.draft(outcome)


def test_draft_rationales_changes_only_the_rationale_field(store, signer):
    off = _seeded_proposal_set(store, signer)
    on = draft_rationales(off, StaticRationaleDrafter())

    assert len(on.proposals) == len(off.proposals)
    assert on.records_observed == off.records_observed
    for before, after in zip(off.proposals, on.proposals, strict=True):
        assert after.outcome_id == before.outcome_id
        assert after.statement == before.statement
        assert after.forward_verdict == before.forward_verdict
        assert after.backward_verdict == before.backward_verdict
        assert after.coverage_n == before.coverage_n
        assert after.coverage_m == before.coverage_m
        assert after.missing_instrument == before.missing_instrument
        assert after.refusal_reason_code == before.refusal_reason_code
        assert after.candidate == before.candidate
        # ... the one field that IS allowed to differ, and does:
        assert after.rationale != before.rationale
        assert before.rationale in after.rationale


def test_draft_rationales_round_trips_to_the_original_when_diffed_back(store, signer):
    """Byte-identity holds in BOTH directions: replacing ``on``'s drafted
    rationale back with the original recovers ``off`` exactly."""
    off = _seeded_proposal_set(store, signer)
    on = draft_rationales(off, StaticRationaleDrafter())
    restored = dataclasses.replace(
        on,
        proposals=tuple(
            dataclasses.replace(p, rationale=orig.rationale) for p, orig in zip(on.proposals, off.proposals, strict=True)
        ),
    )
    assert restored == off


def test_draft_rationales_never_changes_the_declaration_digest(store, signer, tmp_path):
    """The re-derivability guard: ``persist_proposals`` -> ``DeclarationStore``
    freezes ``d_digest`` from the candidate alone (``declarations.py``'s
    ``candidate_digest``), never from ``rationale`` -- a drafted proposal set
    must freeze to the SAME digest as the undrafted one for every
    outcome_id. This is the check a mutant that let prose leak into the
    digest computation would fail."""
    from capsule_ledger.setup.propose import persist_proposals

    off = _seeded_proposal_set(store, signer)
    on = draft_rationales(off, StaticRationaleDrafter())

    store_off = DeclarationStore(tmp_path / "off")
    store_on = DeclarationStore(tmp_path / "on")
    persist_proposals(off, store_off)
    persist_proposals(on, store_on)

    outcome_ids = {p.outcome_id for p in off.proposals}
    assert outcome_ids  # sanity: this ledger produced at least one candidate
    for outcome_id in outcome_ids:
        assert store_off.load(outcome_id).d_digest == store_on.load(outcome_id).d_digest


# -- DeepEvalRationaleDrafter --------------------------------------------------


def _deepeval_installed() -> bool:
    try:
        import deepeval  # noqa: F401
    except ImportError:
        return False
    return True


def test_deepeval_rationale_drafter_missing_dependency_raises_a_named_reason():
    if _deepeval_installed():
        pytest.skip("deepeval is installed in this environment -- the not-installed path isn't reachable here")
    with pytest.raises(DrafterError) as exc_info:
        DeepEvalRationaleDrafter()
    assert exc_info.value.reason == DRAFTER_DEPENDENCY_MISSING
    assert "capsule-ledger[judge]" in str(exc_info.value)


@pytest.mark.skipif(not _deepeval_installed(), reason="deepeval is an optional dependency (pip install capsule-ledger[judge])")
def test_deepeval_rationale_drafter_wiring_uses_gevals_reason_as_prose(store, signer, monkeypatch):
    # No real model call: GEval.measure is patched so this proves the
    # wiring (construction, one GEval instance per outcome, .reason ->
    # rationale mapping) against the REAL installed deepeval API without
    # needing network access or an API key at call time.
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-construction-only")
    proposal_set = _seeded_proposal_set(store, signer)
    drafter = DeepEvalRationaleDrafter(model="gpt-4o-mini")

    def fake_measure(self, test_case, **kwargs):
        self.score = 1.0
        self.reason = f"drafted prose for {self.name}"
        return self.score

    monkeypatch.setattr(drafter._GEval, "measure", fake_measure)
    on = draft_rationales(proposal_set, drafter)
    for p in on.proposals:
        assert p.rationale == f"drafted prose for propose-drafter::{p.outcome_id}"


@pytest.mark.skipif(not _deepeval_installed(), reason="deepeval is an optional dependency (pip install capsule-ledger[judge])")
def test_deepeval_rationale_drafter_raises_named_reason_when_geval_yields_no_reason(store, signer, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-construction-only")
    proposal_set = _seeded_proposal_set(store, signer)
    drafter = DeepEvalRationaleDrafter()

    def fake_measure(self, test_case, **kwargs):
        self.score = 1.0
        self.reason = None
        return self.score

    monkeypatch.setattr(drafter._GEval, "measure", fake_measure)
    with pytest.raises(DrafterError):
        draft_rationales(proposal_set, drafter)
