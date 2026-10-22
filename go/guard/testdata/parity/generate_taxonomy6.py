# Dev tool, not shipped: regenerates taxonomy6-parity.json, the engine's
# decisions for taxonomy6-corpus.json (capsule-engine #117, taxonomy 6:
# marketplace.offer, marketplace.sale and agreement.accept name approver_role
# account_holder, so a below-floor seller offer, price_floor declared ASK,
# asks). The corpus is the engine side's vectors
# (go-vectors/corpus.json), vendored as
# given; this is its generate_python_decisions.py with three changes: it
# reads the corpus beside it under this name, takes the commit as an
# argument, and writes to stdout, run like the other generators here: from
# capsule-engine checked out read-only at the commit named in the output,
#
#   cd <capsule-engine@SHA> && <venv>/bin/python <this file> <SHA> > taxonomy6-parity.json
#
# No catalog pack cites price_floor, so each case runs a GuardEngine
# configured directly with the catalog's price_floor/2.0.0,
# seller.single_commitment/1.0.0 and promise_never/1.0.0 and the case's
# ask_wickets (default [price_floor]), on a fresh ledger; the steps run in
# order and the last decision is recorded. The output also pins the taxonomy
# the engine ran with. No model is loaded.
import dataclasses
import hashlib
import json
import sys
import tempfile
from pathlib import Path

from capsule_ledger.ledger import LedgerStore

sys.path.insert(0, ".")
from capsule_engine.folds.loader import load_definition_file as load_fold  # noqa: E402
from capsule_engine.guards import Action, GuardEngine, LocalSigner  # noqa: E402
from capsule_engine.guards.classes import TAXONOMY_DIGEST, TAXONOMY_VERSION  # noqa: E402
from capsule_engine.guards.wickets import load_definition_file  # noqa: E402

HERE = Path(__file__).parent
SIGNER = LocalSigner(key_id="taxonomy-6-differential-key", secret=b"taxonomy-6-differential-fixed-key")


def main(sha):
    corpus = json.loads((HERE / "taxonomy6-corpus.json").read_text())
    root = Path("capsule_engine")
    defs = root / "guards" / "wickets" / "catalog_defs"
    floor = load_definition_file(defs / "price_floor.yaml")
    single = load_definition_file(defs / "single_commitment.seller.yaml")
    never = load_definition_file(defs / "promise_never.yaml")
    spend = load_fold(root / "folds" / "catalog_defs" / "spend.weekly.v3.yaml")
    golden = corpus["commercial_bounds_opening"]
    openings = {True: golden, False: None, "bad_nonce": {**golden, "nonce": corpus["bad_nonce"]}}
    out = {
        "capsule_engine_commit": sha,
        "source": "go/guard/testdata/parity/generate_taxonomy6.py",
        "taxonomy_version": TAXONOMY_VERSION,
        "taxonomy_jcs_digest": TAXONOMY_DIGEST,
        "taxonomy_file_sha256": hashlib.sha256((root / "guards" / "action_taxonomy.json").read_bytes()).hexdigest(),
        "wicket_digests": {d.wicket_id: d.definition_digest() for d in (floor, single, never)},
        "decisions": {},
    }
    for case in corpus["cases"]:
        classes = case.get("price_floor_action_classes")
        case_floor = dataclasses.replace(floor, config={**floor.config, "action_classes": classes}) if classes else floor
        asks = frozenset(case.get("ask_wickets", ["price_floor"]))
        with tempfile.TemporaryDirectory() as tmp, LedgerStore(Path(tmp) / "ledger") as store:
            engine = GuardEngine(ledger=store, caps_fold=spend, signer_provider=lambda: SIGNER,
                                 wickets=(case_floor, single, never), ask_wickets=asks)
            decision = None
            for step in case["steps"]:
                decision = engine.check(Action(**step["action"]), dry_run=step["dry_run"],
                                        task_authority_record=corpus["task_authority_record"],
                                        commercial_bounds_opening=openings[step["opening"]])
            assert decision is not None
            out["decisions"][case["name"]] = {
                "ask_wickets": sorted(asks),
                "outcome": decision.outcome,
                "decision": decision.capsule["disposition"]["decision"],
                "verdict_class": decision.capsule["disposition"].get("verdict_class"),
                "failing_constraints": sorted(c.id for c in decision.constraints if c.result == "fail"),
                "price_floor_result": next(c.result for c in decision.constraints if c.id == "price_floor"),
                "reason": decision.reason,
            }
    json.dump(out, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main(sys.argv[1])
