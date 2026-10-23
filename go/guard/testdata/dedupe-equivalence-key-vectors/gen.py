"""Generates dedupe-equivalence-key vectors from capsule-engine (python -P gen.py, run from the worktree venv)."""
import json, sys, tempfile
from pathlib import Path
from agent_action_capsule import json_digest
from capsule_ledger.ledger import LedgerStore
from capsule_engine.folds.loader import load_definition_file
from capsule_engine.guards import Action, GuardEngine, LocalSigner
from capsule_engine.guards.checks.dedupe import equivalence_key_for_action, equivalence_key_for_capsule
import capsule_engine

SIGNER = LocalSigner(key_id="dedupe-equivalence-key", secret=b"dedupe-equivalence-key-fixed-key")
fold = load_definition_file(Path(capsule_engine.__file__).parent / "folds" / "catalog_defs" / "spend.weekly.yaml")

def pay(n, **f):
    return Action(verb="pay", operator="dedupe-equivalence-key", developer="assistant@v1", action_class="money.transfer",
                  currency="EUR", target="payee/garden-centre", amount_minor=1_250,
                  action_id=f"pay/dedupe-equivalence-key-{n}", timestamp=f"2026-10-09T12:{n:02d}:00Z", **f)

SCENARIOS = {
    "retry_same_key_denied": [pay(1, equivalence_key="order-123"), pay(2, equivalence_key="order-123")],
    "two_keys_distinct": [pay(1, equivalence_key="bill-2026-09"), pay(2, equivalence_key="bill-2026-10")],
    "keyed_then_unkeyed_never_match": [pay(1, equivalence_key="order-123"), pay(2)],
    "unkeyed_repeat_denied": [pay(1), pay(2)],
}
out = {"field": "asg_payload.equivalence_key_digest", "rule": (
    "sealed = json_digest(equivalence_key) (JCS of the JSON string, sha256, lowercase hex) when the caller sets a key, "
    "else the field is omitted; dedupe key for an action = sealed digest if set else the default formula; for a capsule = "
    "asg_payload.equivalence_key_digest if present else the formula; field is LOCAL-ONLY (refused by every share)"),
    "key_digests": {k: json_digest(k) for k in ("order-123", "bill-2026-09", "bill-2026-10")}, "scenarios": {}}
for name, actions in SCENARIOS.items():
    with tempfile.TemporaryDirectory() as d:
        store = LedgerStore(Path(d) / "ledger")
        eng = GuardEngine(ledger=store, caps_fold=fold, signer_provider=lambda: SIGNER)
        steps = []
        for a in actions:
            dec = eng.check(a)
            (dd,) = [c for c in dec.constraints if c.id == "dedupe"]
            steps.append({"equivalence_key": a.equivalence_key, "action_key": equivalence_key_for_action(a),
                          "capsule_key": equivalence_key_for_capsule(dec.capsule), "dedupe": dd.result,
                          "outcome": dec.outcome, "capsule": dec.capsule})
        store.close()
    out["scenarios"][name] = steps
json.dump(out, sys.stdout, indent=1, sort_keys=True)
