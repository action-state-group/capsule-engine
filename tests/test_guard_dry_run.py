# SPDX-License-Identifier: Apache-2.0
"""dry_run over the sample ledgers: would-have-held outcomes, never blocking.

nanda_transaction_ledger.jsonl is 36 near-identical `record_transaction`
capsules (same operator/developer/action_type, no discriminating amount or
target) -- replaying them one at a time is exactly the dedupe check's
target case: the first occurrence passes, every repeat would-have-held.
amaury_sample_ledger.jsonl's transfer_funds action demonstrates the same
for the caps check, in dry_run form (see test_guard_eur150k_bridge.py for
the non-dry-run, actually-recorded version of the same action -- it escalates
rather than blocks, since `money.transfer` has an `approver_role` configured;
see D2, 2026-08-05).
"""
import json
from pathlib import Path

from capsule_ledger.guards import Action, GuardEngine

FIXTURES = Path(__file__).parent / "fixtures"
NANDA = FIXTURES / "nanda_transaction_ledger.jsonl"
AMAURY = FIXTURES / "amaury_sample_ledger.jsonl"


def _records(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_dry_run_never_blocks_and_nanda_dedupe_would_have_held(store, caps_fold, signer):
    records = _records(NANDA)
    engine = GuardEngine(ledger=store, caps_fold=caps_fold, signer_provider=lambda: signer)

    outcomes = []
    for rec in records:
        action = Action.from_capsule(rec)
        decision = engine.check(action, dry_run=True)
        assert decision.dry_run is True
        # dry_run never prevents recording -- a capsule is always produced
        # when nothing else (signing/ledger/view) is degraded.
        assert decision.capsule is not None
        outcomes.append(decision.outcome)

    assert outcomes[0] == "allow"
    would_have_held = outcomes[1:]
    assert would_have_held, "expected repeat records to exercise dedupe"
    assert all(o == "deny" for o in would_have_held), outcomes
    assert len(would_have_held) == len(records) - 1


def test_dry_run_amaury_caps_would_have_held(store, caps_fold, signer):
    records = _records(AMAURY)
    engine = GuardEngine(
        ledger=store,
        caps_fold=caps_fold,
        signer_provider=lambda: signer,
        caps_minor={"money.transfer": 10_000_000},
    )

    outcomes = {}
    for rec in records:
        verb = rec["action_id"].split("/", 1)[0]
        if verb == "transfer_funds":
            action = Action(
                verb=verb,
                operator=rec["operator"],
                developer=rec["developer"],
                action_class="money.transfer",
                amount_minor=15_000_000,
                currency="EUR",
            )
        else:
            action = Action.from_capsule(rec)
        decision = engine.check(action, dry_run=True)
        assert decision.dry_run is True
        outcomes[verb] = decision.outcome

    assert outcomes["transfer_funds"] == "escalate"
