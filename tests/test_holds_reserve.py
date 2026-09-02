# SPDX-License-Identifier: Apache-2.0
"""capsule-emit #51: atomic evaluate-and-reserve.

Acceptance criteria: hold-semantics spec §#51.
"""
from __future__ import annotations

import shutil
import tempfile
import threading
from pathlib import Path

import pytest

from capsule_engine.folds.engine import evaluate_one
from capsule_engine.guards import Action, LocalSigner
from capsule_engine.holds import HoldEngine, HoldStatus
from capsule_ledger.ledger import LedgerStore

DEVELOPER = "procurement-agent@v1"
OPERATOR = "acme-research"


def _action(developer=DEVELOPER, amount_minor=100, action_id=None, target=None):
    return Action(
        verb="transfer_funds",
        operator=OPERATOR,
        developer=developer,
        action_class="money.transfer",
        amount_minor=amount_minor,
        currency="EUR",
        action_id=action_id,
        target=target,
    )


# -- #51.1: reserve-at-seal cites the fold envelope + reserved amount -------


def test_reserve_cites_fold_envelope_and_reserved_amount(hold_engine, store):
    decision = hold_engine.evaluate_and_reserve(_action(amount_minor=500))
    assert decision.outcome == "allow"
    capsule = decision.capsule
    payload = capsule["asg_payload"]

    assert payload["reserved_amount_minor"] == 500
    assert isinstance(payload["reserved_amount_minor"], int)
    envelope = payload["fold_envelope"]
    assert envelope["fold"] == hold_engine._hold_fold.definition_digest()
    assert "range" in envelope and "checkpoint" in envelope
    assert decision.fold_envelope == envelope

    # a real, independently verifiable capsule -- no special-cased path
    result = store.verify(capsule["capsule_id"])
    assert result.ok, result.findings


# -- #51.2: N concurrent gate entries against a cap admitting K -------------


def _run_concurrency_round(n: int, k: int, unit: int) -> tuple[int, int]:
    """Fire ``n`` concurrent evaluate_and_reserve calls (same scope) against
    a cap sized to admit exactly ``k`` of them. Returns (allowed, denied)."""
    tmp = Path(tempfile.mkdtemp())
    try:
        store = LedgerStore(tmp)
        signer = LocalSigner(key_id="k1", secret=b"s1")
        from capsule_engine.folds.loader import load_definition_file

        fold = load_definition_file(
            Path(__file__).parent.parent / "capsule_engine" / "folds" / "catalog_defs" / "hold.active_exposure.yaml"
        )
        engine = HoldEngine(
            ledger=store,
            hold_fold=fold,
            fold_digest=fold.definition_digest(),
            signer_provider=lambda: signer,
            cap_minor={"money.transfer": unit * k},
        )

        outcomes: list[str] = []
        lock = threading.Lock()
        barrier = threading.Barrier(n)

        def worker(i: int) -> None:
            barrier.wait()  # maximize interleaving: every thread starts together
            decision = engine.evaluate_and_reserve(_action(amount_minor=unit, action_id=f"transfer_funds/{i}"))
            with lock:
                outcomes.append(decision.outcome)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        allowed = outcomes.count("allow")
        denied = n - allowed

        # zero interleavings over-reserve: independently recompute the fold
        # over the resulting ledger and confirm it never exceeds the cap.
        records = [r.capsule for r in store.scan()]
        trace = evaluate_one(fold, records, key_value=DEVELOPER)
        assert trace.result is not None and trace.result <= unit * k
        reserve_count = sum(
            1 for r in records if (r.get("action_id") or "").startswith("hold.reserve/")
        )
        assert reserve_count == allowed

        return allowed, denied
    finally:
        store.close()
        shutil.rmtree(tmp)


@pytest.mark.parametrize("round_idx", range(15))
def test_concurrent_evaluate_and_reserve_admits_exactly_k(round_idx):
    """Property/soak: repeat the same N-vs-K race many times, real threads,
    no mocks. K=6 of N=20 unit reservations must be admitted every time."""
    n, k, unit = 20, 6, 100
    allowed, denied = _run_concurrency_round(n, k, unit)
    assert allowed == k, f"round {round_idx}: expected exactly {k} admitted, got {allowed}"
    assert denied == n - k


# -- #51.3: a pending hold blocks a second action before approval lands ----


def test_pending_hold_blocks_action_that_would_exceed_cap(hold_engine):
    # cap is 10,000,000 minor units (holds.yaml); reserve most of it, then a
    # second (still-unapproved) action that would push the aggregate over
    # the cap must be blocked -- purely because of the PENDING hold, before
    # any conversion/approval has happened.
    first = hold_engine.evaluate_and_reserve(
        _action(amount_minor=9_500_000, action_id="transfer_funds/1", target="acct-1")
    )
    assert first.outcome == "allow"
    assert first.hold_status == HoldStatus.ACTIVE

    # a distinct target (dedupe discriminator) so this is isolated to the
    # caps/active-holds mechanism, not an incidental dedupe collision.
    second = hold_engine.evaluate_and_reserve(
        _action(amount_minor=1_000_000, action_id="transfer_funds/2", target="acct-2")
    )
    # money.transfer has an approver_role configured -- a pure cap-exceeded
    # hold escalates rather than hard-denies (D2), but it is NOT admitted.
    assert second.outcome in ("deny", "escalate")
    assert second.outcome != "allow"


# -- #51.4: sequencer/serialization-point unavailable -> fail closed -------


def test_engine_unavailable_fails_closed_no_reserve(store, hold_fold, signer):
    engine = HoldEngine(
        ledger=store,
        hold_fold=hold_fold,
        fold_digest=hold_fold.definition_digest(),
        signer_provider=lambda: signer,
        cap_minor={"money.transfer": 1_000_000},
        engine_available=lambda: False,
    )
    decision = engine.evaluate_and_reserve(_action(amount_minor=100))
    assert decision.outcome == "deny"
    assert not any((r.capsule.get("action_id") or "").startswith("hold.reserve/") for r in store.scan())


def test_engine_unavailable_mutant_disabling_the_check_flips_to_allow(hold_fold, signer):
    """Mutant test: the fail-closed branch in ``evaluate_and_reserve`` is the
    condition under test. Disabling it (as a real code mutation would) must
    flip a would-be-denied reservation to allowed -- proving the check is
    load-bearing, not a decorative no-op. Two independent stores, so the
    only variable between "real" and "mutant" is ``engine_available``, not
    an incidental dedupe collision between the two calls."""
    tmp_real, tmp_mutant = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp())
    try:
        real_store = LedgerStore(tmp_real)
        real_engine = HoldEngine(
            ledger=real_store,
            hold_fold=hold_fold,
            fold_digest=hold_fold.definition_digest(),
            signer_provider=lambda: signer,
            cap_minor={"money.transfer": 1_000_000},
            engine_available=lambda: False,
        )
        real = real_engine.evaluate_and_reserve(_action(amount_minor=100))
        assert real.outcome == "deny"
        real_store.close()

        # the mutant: the engine reports available even though it is not --
        # i.e. the fail-closed condition never fires.
        mutant_store = LedgerStore(tmp_mutant)
        mutant_engine = HoldEngine(
            ledger=mutant_store,
            hold_fold=hold_fold,
            fold_digest=hold_fold.definition_digest(),
            signer_provider=lambda: signer,
            cap_minor={"money.transfer": 1_000_000},
            engine_available=lambda: True,  # mutated: the unreachable sequencer is masked
        )
        mutant = mutant_engine.evaluate_and_reserve(_action(amount_minor=100))
        assert mutant.outcome == "allow", "mutant did not flip the outcome -- the fail-closed check is not load-bearing"
        mutant_store.close()
    finally:
        shutil.rmtree(tmp_real)
        shutil.rmtree(tmp_mutant)


def test_stale_view_fails_closed_no_reserve(store, hold_fold, signer):
    engine = HoldEngine(
        ledger=store,
        hold_fold=hold_fold,
        fold_digest=hold_fold.definition_digest(),
        signer_provider=lambda: signer,
        cap_minor={"money.transfer": 1_000_000},
        freshness_bound_ms=1_000,
        checkpoint_age_ms=lambda: 5_000,
    )
    decision = engine.evaluate_and_reserve(_action(amount_minor=100))
    assert decision.outcome == "deny"
    assert not any((r.capsule.get("action_id") or "").startswith("hold.reserve/") for r in store.scan())


# -- #51.5: replay reproduces the cited aggregate byte-exactly -------------


def test_replay_reproduces_cited_aggregate_byte_exactly(hold_engine, store, hold_fold):
    hold_engine.evaluate_and_reserve(_action(amount_minor=300_000, action_id="transfer_funds/a"))
    hold_engine.evaluate_and_reserve(_action(amount_minor=150_000, action_id="transfer_funds/b", developer="other-dev"))
    d3 = hold_engine.evaluate_and_reserve(_action(amount_minor=200_000, action_id="transfer_funds/c"))
    assert d3.outcome == "allow"

    cited_aggregate = d3.fold_envelope["result"]

    # independent verifier: fresh evaluation over the ledger, including the
    # hold capsules, must reproduce the same aggregate byte-exactly.
    records = [r.capsule for r in store.scan()]
    full_trace = evaluate_one(hold_fold, records, key_value=DEVELOPER, as_of=records[-1]["timestamp"])
    assert full_trace.result == cited_aggregate + 200_000  # includes d3's own reservation

    # the cited aggregate is BEFORE this action's own reservation lands;
    # the independent recompute over the same prefix (excluding d3 itself)
    # must match exactly.
    prefix_records = records[:-1]
    prefix_trace = evaluate_one(hold_fold, prefix_records, key_value=DEVELOPER, as_of=prefix_records[-1]["timestamp"])
    assert prefix_trace.result == cited_aggregate
