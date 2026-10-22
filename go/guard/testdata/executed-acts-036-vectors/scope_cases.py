#!/usr/bin/env python3
"""The 7-day window on a history declared incomplete, as capsule-engine's live check decides it.

Run from a capsule-engine checkout (a710636c or later, everyday 0.3.6):
    python <this dir>/scope_cases.py <this dir> <capsule-engine commit>
Decides ../emit/pay-history-incomplete.json and ../emit/pay-history-empty-incomplete.json (the
plugin's own envelopes, history_scope.complete false) as tests/test_executed_acts_window.py
decides a live check, and writes scope-results.json: the 7-day half of r05 as that test reads it.
caps.py does not read history_scope, so the engine evaluates the window where this plugin leaves
it not_evaluable (an open engine-side follow-up); internal/guard/g3_engine_test.go pins the difference.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, ".")
from tests import test_executed_acts_window as t  # noqa: E402

D = Path(sys.argv[1])
out = {"schema": "executed-acts-036-scope-results/v0", "capsule_engine_commit": sys.argv[2], "pack": t.PACK.pack_id,
       "pack_digest": t.PACK.definition_digest(), "cases": {}}
for name in ("pay-history-incomplete", "pay-history-empty-incomplete"):
    envelope = json.loads((D / ".." / "emit" / f"{name}.json").read_text())
    out["cases"][name] = {"history_scope": envelope["history_scope"], **t._window(t._decide_envelope(envelope))}
(D / "scope-results.json").write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
