# SPDX-License-Identifier: Apache-2.0
"""Differential concurrency test (fixture-shape discipline, 2026-08-11):
sequential admission and concurrent admission of the same traffic against
the same cap must admit the same total count. This is the exact shape of
check the capsule-emit PR #54 incident (see ``schema.py``'s module
docstring) would have caught before shipping -- a lock/cap/aggregate scope
disagreement let concurrent submissions jointly admit what sequential
execution would deny.

This test is exploratory as much as it is a regression guard: it is the
first time this pack's ``caps`` check has been run under genuine
concurrency rather than one call at a time. Its purpose is to CONFIRM (or
refute) that ``GuardEngine.check()``'s own read-then-decide-then-append
span is safe under concurrent callers sharing one ledger -- not to assume
it is."""
from __future__ import annotations

import tempfile
import threading
from pathlib import Path

import pytest

from capsule_ledger.guards import Action, LocalSigner
from capsule_ledger.ledger import LedgerStore
from capsule_ledger.packs import build_engine, install_pack, load_pack_dir

PACK_DIR = Path(__file__).parent.parent / "capsule_ledger" / "packs" / "catalog" / "payments-safety"

OPERATOR = "acme-checkout"
DEVELOPER = "checkout-concurrency-probe@v1"
CAP_MINOR = 1_000_000
N_ROUNDS = 20


def _make_engine(ledger, project_dir):
    pack = load_pack_dir(PACK_DIR)
    installed = install_pack(pack, project_dir=project_dir, mode="observe")
    signer = LocalSigner(key_id="concurrency-probe-key", secret=b"concurrency-probe-fixed-key")
    return build_engine(installed, ledger=ledger, signer_provider=lambda: signer)


@pytest.mark.xfail(
    reason=(
        "CONFIRMED FINDING (2026-08-11): GuardEngine.check() (capsule_ledger/guards/engine.py) holds no "
        "lock across its own read (check_caps' ledger.scan()) -> decide -> append span. Two concurrent "
        "check() calls for the same developer/action_class both read the pre-write ledger state, both "
        "compute 'under cap', and both append -- the cap is not enforced under concurrency at all (this "
        "test measured 40/40 admitted concurrently vs. 20/40 sequentially, for traffic designed so "
        "sequential execution admits exactly half). Real, core-engine bug, not payments-safety-specific -- "
        "reported live, not silently fixed here. xfail (not skip) so this stays visible and this test "
        "starts passing the moment the engine gets a real fix, without needing this marker hand-removed."
    ),
    strict=True,
)
def test_sequential_and_concurrent_admission_agree_on_total_admitted():
    """Runs N independent rounds (fresh developer window each round via a
    distinct target/timestamp block) both sequentially and concurrently,
    and compares how many of the 2*N submissions each mode actually
    admits (outcome == 'allow'). If GuardEngine.check()'s read-then-decide-
    then-append span isn't atomic across concurrent callers, concurrent
    admits will exceed sequential admits -- reported as a real, not
    silently swallowed, finding."""
    sequential_admits = 0
    concurrent_admits = 0

    with tempfile.TemporaryDirectory() as ld_seq, tempfile.TemporaryDirectory() as pd_seq:
        ledger_seq = LedgerStore(ld_seq)
        engine_seq = _make_engine(ledger_seq, pd_seq)
        try:
            for i in range(N_ROUNDS):
                action_a = Action(
                    verb="dispatch_payout", operator=OPERATOR, developer=f"{DEVELOPER}-seq-{i}",
                    action_class="money.transfer", amount_minor=int(CAP_MINOR * 0.6), currency="EUR",
                    target=f"vendor-concurrency-probe/seq-{i}-a",
                    action_id=f"dispatch_payout/concurrency-probe-seq-{i}-a",
                    timestamp=f"2026-08-11T09:{i:02d}:10Z",
                )
                action_b = Action(
                    verb="dispatch_payout", operator=OPERATOR, developer=f"{DEVELOPER}-seq-{i}",
                    action_class="money.transfer", amount_minor=int(CAP_MINOR * 0.6), currency="EUR",
                    target=f"vendor-concurrency-probe/seq-{i}-b",
                    action_id=f"dispatch_payout/concurrency-probe-seq-{i}-b",
                    timestamp=f"2026-08-11T09:{i:02d}:11Z",
                )
                if engine_seq.check(action_a, dry_run=True).outcome == "allow":
                    sequential_admits += 1
                if engine_seq.check(action_b, dry_run=True).outcome == "allow":
                    sequential_admits += 1
        finally:
            ledger_seq.close()

    with tempfile.TemporaryDirectory() as ld_conc, tempfile.TemporaryDirectory() as pd_conc:
        ledger_conc = LedgerStore(ld_conc)
        engine_conc = _make_engine(ledger_conc, pd_conc)
        try:
            for i in range(N_ROUNDS):
                # Fresh developer per round -- isolates each round's pair from
                # every other round's, so this measures ONLY the within-pair
                # race, the exact shape PR #54 found (two concurrent actions
                # for the SAME group racing each other).
                outcomes = _submit_two_actions_for(engine_conc, DEVELOPER + f"-conc-{i}", i)
                concurrent_admits += sum(1 for o in outcomes if o == "allow")
        finally:
            ledger_conc.close()

    assert concurrent_admits <= sequential_admits, (
        f"concurrent admission ({concurrent_admits}/{2 * N_ROUNDS}) exceeded sequential admission "
        f"({sequential_admits}/{2 * N_ROUNDS}) -- GuardEngine.check()'s read-then-decide-then-append "
        "span is not safe under concurrent callers sharing one developer's window: this is the same "
        "shape of bug capsule-emit PR #54 found in the holds engine (a scope disagreement let a "
        "cross-class race jointly admit what sequential execution would deny), here as a plain "
        "read-then-write race with no lock at all."
    )


def _submit_two_actions_for(engine, developer: str, round_idx: int) -> list[str]:
    outcomes: list[str] = []
    lock = threading.Lock()

    def _one(letter: str, offset_seconds: int):
        action = Action(
            verb="dispatch_payout", operator=OPERATOR, developer=developer,
            action_class="money.transfer", amount_minor=int(CAP_MINOR * 0.6), currency="EUR",
            target=f"vendor-concurrency-probe/conc-{round_idx}-{letter}",
            action_id=f"dispatch_payout/concurrency-probe-conc-{round_idx}-{letter}",
            timestamp=f"2026-08-11T09:{round_idx:02d}:{10 + offset_seconds:02d}Z",
        )
        decision = engine.check(action, dry_run=True)
        with lock:
            outcomes.append(decision.outcome)

    t_a = threading.Thread(target=_one, args=("a", 0))
    t_b = threading.Thread(target=_one, args=("b", 1))
    t_a.start()
    t_b.start()
    t_a.join()
    t_b.join()
    return outcomes
