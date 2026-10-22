import sys, tempfile, json
from pathlib import Path
import capsule_engine
from capsule_ledger.ledger import LedgerStore
from capsule_engine.guards import GuardEngine, LocalSigner
from capsule_engine.guards.checks import RUNNABLE_CHECKS
from capsule_engine.packs import install_pack, load_pack_dir
from capsule_engine.packs.install import engine_ask_sets
from capsule_engine.report.replay import action_for_check_input
PACK = load_pack_dir(Path(capsule_engine.__file__).parent / "packs" / "catalog" / "seller")
for p in sys.argv[1:]:
    env = json.loads(Path(p).read_text())
    with tempfile.TemporaryDirectory() as tmp, LedgerStore(Path(tmp)/"l") as store:
        inst = install_pack(PACK, project_dir=Path(tmp)/"p", mode="observe"); r = inst.resolved
        wk = r.configured_wickets(RUNNABLE_CHECKS); g, a = engine_ask_sets(inst.pack, wk)
        signer = LocalSigner(key_id="k", secret=b"s")
        eng = GuardEngine(ledger=store, caps_fold=r.caps_fold(), signer_provider=lambda: signer, caps_minor=r.caps_minor() or {},
            manifest_digest=r.manifest_digest, wickets=wk, ask_gate_selectors=g, ask_wickets=a, evaluate_under_record_taxonomy=True)
        act = action_for_check_input(env["record"], item_ref=env.get("item_ref"))
        d = eng.check(act, task_authority_record=env.get("task_authority_record"), commercial_bounds_opening=env.get("commercial_bounds_opening"))
    print("==", Path(p).stem, act.action_class, "->", d.outcome)
    for c in d.constraints:
        if c.result != "n/a" or (c.evidence or {}).get("in_scope"):
            print("    ", c.id, c.result, "|", c.reason[:100])
