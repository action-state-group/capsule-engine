#!/usr/bin/env python3
"""Decide every G3 step through capsule-engine and write engine-results.json.

Run from a capsule-engine checkout (a710636c or later, everyday 0.3.6):
    python <this dir>/engine_results.py <this dir> <capsule-engine commit>
Each step's envelope (inputs/<sequence>/step-<k>.input.json) is decided as the
engine's own tests/test_executed_acts_window.py decides a live check: a fresh
engine under the catalog everyday pack whose ledger is the envelope's history
(history_ledger, with the pack's caps fold). The 7-day half of
r05-spending-limits is read off the caps constraint exactly as that test reads
it, beside the decision's outcome and verdict, caps' result and sealed evidence
digest, and each rule's own result. The Go plugin's results are go-results.json;
internal/plugin/g3_vectors_test.go holds the card halves equal, and
internal/guard/g3_engine_test.go the rest.
"""
import json
import sys
from pathlib import Path

from agent_action_capsule import json_digest

sys.path.insert(0, ".")
from capsule_engine.packs.obligation_results import obligation_results  # noqa: E402
from tests import test_executed_acts_window as t  # noqa: E402

D = Path(sys.argv[1])
sequences = {}
for seq in sorted(p.name for p in (D / "inputs").iterdir() if p.is_dir()):
    steps = []
    for k in range(len(list((D / "inputs" / seq).glob("step-*.input.json")))):
        envelope = json.loads((D / "inputs" / seq / f"step-{k}.input.json").read_text())
        decision = t._decide_envelope(envelope)
        (caps,) = [c for c in decision.constraints if c.id == "caps"]
        steps.append({"step": k, "history_acts": len(envelope["history"]), **t._window(decision),
                      "outcome": decision.outcome, "verdict": decision.verdict,
                      "asked_unevaluated_checks": list(decision.asked_unevaluated_checks),
                      "caps": {"result": caps.result, "reason": caps.reason, "evidence_digest": json_digest(caps.evidence)},
                      "rules": {r.obligation_id: r.result for r in obligation_results(t.PACK, decision.constraints)}})
    sequences[seq] = steps
out = {"schema": "g3-executed-acts-engine-results/v0", "rule": "r05-spending-limits#7d", "pack": t.PACK.pack_id,
       "pack_digest": t.PACK.definition_digest(), "capsule_engine_commit": sys.argv[2], "sequences": sequences}
(D / "engine-results.json").write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
