# SPDX-License-Identifier: Apache-2.0
"""The design §5 acceptance criterion, literally: the SAME five verbs run
against three structurally different deployment shapes must produce three
*truthfully different* answers -- not three copies of the same template
with the names changed. Plus the companion acceptance line from §3.2's
terraform analogy: re-running ``propose`` against ACCEPTED declarations
diffs cleanly, and that diff is proven (mutant-proofed) to catch a planted
drift, not merely to always say "clean"."""
from __future__ import annotations

import json
from pathlib import Path

from capsule_engine.setup.declarations import DeclarationStore
from capsule_engine.setup.observe import ObserveRecorder
from capsule_engine.setup.propose import diff_against_stored, persist_proposals, propose_from_ledger

FIXTURES = Path(__file__).parent / "fixtures" / "setup"


def _load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _run_pipeline(store, signer, fixture_name: str):
    events = _load_jsonl(FIXTURES / fixture_name)
    recorder = ObserveRecorder(ledger=store, signer=signer, operator="op", developer="dev", heartbeat_every=0)
    summary = recorder.run(events)
    proposal_set = propose_from_ledger(store)
    by_id = {p.outcome_id: p for p in proposal_set.proposals}
    return summary, proposal_set, by_id


def test_positive_control_shows_full_causal_attainment(store, signer):
    """Design §5's complete positive-control deployment: everything is
    instrumented, so the attainment claim is fully DETERMINISTIC on both
    axes and the offer/response claim is fully instrumented too."""
    _, _, by_id = _run_pipeline(store, signer, "deployment_positive_control.jsonl")
    attainment = by_id["outcome.remediation_confirmed"]
    assert attainment.forward_verdict == "DETERMINISTIC"
    assert attainment.backward_verdict == "DETERMINISTIC"
    assert attainment.coverage_n == attainment.coverage_m == 10

    offer_response = by_id["outcome.person_chose"]
    assert offer_response.backward_verdict == "DETERMINISTIC"
    assert offer_response.missing_instrument is None


def test_vendor_declares_shows_partial_causal_coverage_and_missing_instrument(store, signer):
    """Design §5's causal/rung-0/high-privacy deployment: only SOME
    dispatches are externally confirmed (rung-0 evidence is thin), and the
    negative response case was never wired up, so the offer/response claim
    is honestly downgraded to WITH-INSTRUMENTATION -- never silently
    rounded up to DETERMINISTIC."""
    _, _, by_id = _run_pipeline(store, signer, "deployment_vendor_declares.jsonl")
    attainment = by_id["outcome.remediation_confirmed"]
    assert attainment.forward_verdict == "DETERMINISTIC"
    assert 0 < attainment.coverage_n < attainment.coverage_m

    offer_response = by_id["outcome.person_chose"]
    assert offer_response.backward_verdict == "WITH-INSTRUMENTATION"
    assert offer_response.missing_instrument is not None


def test_advisory_only_has_no_attainment_claim_at_all(store, signer):
    """Design §5's advisory-only/agent-cannot-act deployment: zero
    dispatches were ever observed, so the attainment candidate is not
    proposed AT ALL -- absent, not present-and-failing. This is the
    honest answer for a deployment where the agent has no dispatch
    capability to claim attainment over."""
    _, _, by_id = _run_pipeline(store, signer, "deployment_advisory_only.jsonl")
    assert "outcome.remediation_confirmed" not in by_id

    offer_response = by_id["outcome.person_chose"]
    assert offer_response.backward_verdict == "DETERMINISTIC"
    assert offer_response.missing_instrument is None


def test_the_three_deployments_produce_pairwise_different_answers(store, signer, tmp_path):
    """The acceptance criterion stated directly: same five verbs, three
    TRUTHFULLY different answers -- proven here by running all three
    fixtures through fully independent ledgers and comparing the
    (forward, backward, coverage) tuple for the two corpus-dependent
    outcome_ids across every pair of deployments."""
    from capsule_ledger.ledger import LedgerStore

    results = {}
    for name in ("deployment_positive_control.jsonl", "deployment_vendor_declares.jsonl", "deployment_advisory_only.jsonl"):
        with LedgerStore(tmp_path / name) as ledger:
            _, _, by_id = _run_pipeline(ledger, signer, name)
            results[name] = {
                outcome_id: (p.forward_verdict, p.backward_verdict, p.coverage_n, p.coverage_m)
                for outcome_id, p in by_id.items()
            }

    names = list(results)
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            assert results[names[i]] != results[names[j]], f"{names[i]!r} and {names[j]!r} produced identical answers"


def test_reproposing_against_accepted_declarations_diffs_cleanly(store, signer, tmp_path):
    """The §3.2 terraform-analogy acceptance line: accept everything
    proposable, then re-run propose from scratch and diff -- clean."""
    events = _load_jsonl(FIXTURES / "deployment_positive_control.jsonl")
    recorder = ObserveRecorder(ledger=store, signer=signer, operator="op", developer="dev", heartbeat_every=0)
    recorder.run(events)

    proposal_set = propose_from_ledger(store)
    decl_store = DeclarationStore(tmp_path)
    persist_proposals(proposal_set, decl_store)
    for outcome_id in decl_store.list_ids():
        decl_store.set_acceptance_state(outcome_id, "accepted")

    proposal_set_2 = propose_from_ledger(store)
    drift = diff_against_stored(proposal_set_2, decl_store)
    assert drift
    assert all(not d.drifted for d in drift)


def test_reproposing_against_accepted_declarations_detects_a_planted_drift(store, signer, tmp_path):
    """Mutant-proof companion to the clean-diff test above: plant a drift
    in an ACCEPTED declaration's own candidate template and prove the same
    mechanism that just said "clean" now says "drifted" -- then restore
    and re-confirm clean again, so this isn't a diff tool that always
    says one thing."""
    import dataclasses

    import capsule_engine.setup.candidates as candidates_mod

    events = _load_jsonl(FIXTURES / "deployment_positive_control.jsonl")
    recorder = ObserveRecorder(ledger=store, signer=signer, operator="op", developer="dev", heartbeat_every=0)
    recorder.run(events)

    proposal_set = propose_from_ledger(store)
    decl_store = DeclarationStore(tmp_path)
    persist_proposals(proposal_set, decl_store)
    for outcome_id in decl_store.list_ids():
        decl_store.set_acceptance_state(outcome_id, "accepted")

    # Mutate ``statement`` rather than ``action_class``: widening the verb
    # would also change which dispatches match (M), which could make the
    # candidate NOT proposed at all rather than proposed-and-drifted -- the
    # mutant must change the digest without changing coverage, so the diff
    # mechanism (not the coverage query) is what's under test.
    original_candidates = candidates_mod.DEFAULT_CANDIDATES
    mutated = tuple(
        dataclasses.replace(c, statement="MUTATED STATEMENT -- widened without going back through confirm")
        if c.outcome_id == "outcome.remediation_confirmed"
        else c
        for c in original_candidates
    )

    proposal_set_mutated = propose_from_ledger(store, candidates=mutated)
    drift = diff_against_stored(proposal_set_mutated, decl_store)
    entry = next(d for d in drift if d.outcome_id == "outcome.remediation_confirmed")
    assert entry.drifted is True

    proposal_set_restored = propose_from_ledger(store, candidates=original_candidates)
    drift_restored = diff_against_stored(proposal_set_restored, decl_store)
    entry_restored = next(d for d in drift_restored if d.outcome_id == "outcome.remediation_confirmed")
    assert entry_restored.drifted is False
