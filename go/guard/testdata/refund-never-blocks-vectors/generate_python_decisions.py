# Vectors for a refund never blocking the spend window. Run from a capsule-engine checkout on branch
# refund-never-blocks-spend-window:  python -P <this file> <out_dir>
import json, sys, tempfile
sys.path.insert(0, ".")  # the checkout root, for tests/ (python -P drops it)
from capsule_ledger.ledger import LedgerStore
from capsule_engine.guards import LocalSigner
from tests.test_refund_never_blocks_spend_window import (
    MONEY_IN_BODIES, PURCHASE, LATER, REFUND, _engine, _money_in, _purchase, _caps)
from capsule_engine.report.replay import MONEY_IN

def seq(kind, window):
    with tempfile.TemporaryDirectory() as d, LedgerStore(d) as store:
        e = _engine(store, LocalSigner(key_id="test-key-1", secret=b"test-secret"), window=window)
        steps = []
        for label, act in (("purchase", _purchase(1, PURCHASE)), (kind, _money_in(kind)), ("purchase", _purchase(3, LATER))):
            dec = e.check(act); c = _caps(dec)
            steps.append({"act": label, "amount_minor": act.amount_minor, "outcome": dec.outcome,
                          "caps_result": c.result, "caps_evidence": c.evidence})
        return {"window_minor": window, "steps": steps}

out = {"money_in_direction": MONEY_IN, "refund_stated_spend_minor": REFUND,
       "bridge": {k: {"body": {**b, "direction": MONEY_IN, "spend_minor": REFUND}, "amount_minor": _money_in(k).amount_minor}
                  for k, b in MONEY_IN_BODIES.items()},
       "sequences": {f"{k}/{name}": seq(k, w) for k in sorted(MONEY_IN_BODIES)
                     for name, w in (("under-limit", 1000), ("over-limit", PURCHASE + LATER - 1))}}
json.dump(out, open(sys.argv[1] + "/python-decisions.json", "w"), sort_keys=True, indent=1)
