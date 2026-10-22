#!/usr/bin/env python3
"""caps/5.1.0 over a LEDGER holding capsule-engine's own records of executed acts.

Run from a capsule-engine checkout (a710636c or later, everyday 0.3.6):
    python <this dir>/ledger_cases.py <this dir> <capsule-engine commit>
Each case appends its records to a fresh ledger and checks one money.purchase action through a
GuardEngine built from the catalog everyday pack's limits as tests/test_executed_acts_window.py
builds it (no activated policy, so no limit_sources). Writes ledger-results.json: per case the
records, the action, and caps' result, reason and sealed evidence digest.
internal/guard/g3_engine_test.go reads the same records through ReadLedger and holds Go to it.
"""
import json
import sys
import tempfile
from pathlib import Path

from agent_action_capsule import json_digest
from capsule_ledger.ledger import LedgerStore

sys.path.insert(0, ".")
from capsule_engine.guards import Action, GuardEngine, LocalSigner  # noqa: E402
from capsule_engine.packs import install_pack  # noqa: E402
from tests import test_executed_acts_window as t  # noqa: E402

D = Path(sys.argv[1])
NOW = "2026-10-07T12:00:00Z"


def rec(cid, ts, decision, payload, operator="op-a"):
    return {"capsule_id": cid, "operator": operator, "developer": "dev@v1", "timestamp": ts,
            "disposition": {"decision": decision}, "asg_payload": payload}


def action(amount):
    return {"verb": "buy", "operator": "op-a", "developer": "dev@v1", "action_class": "money.purchase",
            "action_id": f"buy/ledger-{amount}", "timestamp": NOW, "amount_minor": amount, "currency": "USD",
            "target": "merchant/ledger", "rail": "card"}


unreadable = {"action_class": "money.purchase", "spend_unreadable": True}
CASES = {
    "executed-unreadable-in-window": ([rec("x1", "2026-10-06T12:00:00Z", "executed", unreadable)], action(2000)),
    "executed-unreadable-outside-window": ([rec("x1", "2026-09-28T12:00:00Z", "executed", unreadable)], action(2000)),
    "executed-unreadable-other-operator": ([rec("x1", "2026-10-06T12:00:00Z", "executed", unreadable, operator="op-b")], action(2000)),
    "executed-counted": ([rec("x1", "2026-10-06T12:00:00Z", "executed", {"action_class": "booking.create", "amount_minor": 1500})], action(2000)),
    "live-history-copy-counted-once": ([
        rec("h1", "2026-10-06T12:00:00Z", "accept", {"action_class": "money.purchase", "amount_minor": 1500, "live_history": "disposition"}),
        rec("x1", "2026-10-06T12:00:00Z", "executed", {"action_class": "money.purchase", "amount_minor": 1500})], action(2000)),
    "per-action-over-beside-unreadable": ([rec("x1", "2026-10-06T12:00:00Z", "executed", unreadable)], action(3000)),
}

out = {"schema": "executed-acts-036-ledger-results/v0", "capsule_engine_commit": sys.argv[2], "pack": t.PACK.pack_id,
       "pack_digest": t.PACK.definition_digest(), "cases": {}}
for name, (records, act) in sorted(CASES.items()):
    with tempfile.TemporaryDirectory() as tmp, LedgerStore(Path(tmp) / "ledger") as store:
        resolved = install_pack(t.PACK, project_dir=Path(tmp) / "p", mode="observe").resolved
        for r in records:
            store.append(dict(r), consequential=False)
        signer = LocalSigner(key_id="executed-acts-036", secret=b"executed-acts-036-ledger")
        engine = GuardEngine(ledger=store, caps_fold=resolved.caps_fold(), signer_provider=lambda: signer,
                             caps_minor=resolved.caps_minor() or {}, per_action_minor=resolved.per_action_minor(),
                             per_action_reads=resolved.per_action_reads())
        decision = engine.check(Action(**act))
        (caps,) = [c for c in decision.constraints if c.id == "caps"]
        out["cases"][name] = {"records": records, "action": act, "caps": {"result": caps.result, "reason": caps.reason,
                              "evidence_digest": json_digest(caps.evidence), "evidence": caps.evidence}}
(D / "ledger-results.json").write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
