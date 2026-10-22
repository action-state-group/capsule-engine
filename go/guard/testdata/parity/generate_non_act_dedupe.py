# Dev tool, not shipped: regenerates non-act-dedupe-parity.json, the engine's
# dedupe finding on every record of two capsulectl deal bundles
# (deal-bundles/, capsule-engine tests/fixtures/deal-bundles at the commit
# named in the output, byte for byte), replayed as capsule-engine #107's own
# test replays them (report/replay.py replay over the spend.weekly fold, every
# record in order, dry runs). #107: a deal record whose sealed
# x-deal-v0.record_type is not "check" states no act, so its dedupe is n/a,
# out of scope, and never matches; a check is still deduped. From #121
# (97a2aff) the replay gives a non-act record no decision at all
# (ReplayResult.undecided); its row records that, and the dedupe the engine
# reads for it when it is checked alone, as test_dedupe_non_act_records.py
# does (check_dedupe on an empty ledger). The vectors are
# the engine side's (go-vectors for deduping non-act records and the refund key);
# this generator regenerates them at the pinned commit:
#
#   cd <capsule-engine@SHA> && <venv>/bin/python <this file> <SHA> > non-act-dedupe-parity.json
#
# No model is loaded.
import json
import sys
import tempfile
from pathlib import Path

from agent_action_capsule import json_digest
from capsule_ledger.ledger import LedgerStore

sys.path.insert(0, ".")
from capsule_engine.folds.loader import load_definition_file as load_fold  # noqa: E402
from capsule_engine.guards.checks.dedupe import check_dedupe  # noqa: E402
from capsule_engine.report.replay import action_for_record, load_disclosed, load_records, replay  # noqa: E402

HERE = Path(__file__).parent
SPEND_WEEKLY = Path("capsule_engine/folds/catalog_defs/spend.weekly.yaml")


def main(sha):
    out = {"capsule_engine_commit": sha, "source": "internal/guard/testdata/parity/generate_non_act_dedupe.py",
           "bundles": {}}
    for name in ("deal-purchase-then-partial-cancel", "deal-purchase-then-refund"):
        path = [HERE / "deal-bundles" / f"{name}.bundle.json"]
        records, disclosed = load_records(path), load_disclosed(path)
        result = replay(records, caps_fold=load_fold(SPEND_WEEKLY), disclosed=disclosed)
        decided = {s.record["capsule_id"]: s for s in result.decisions}
        undecided = {r["capsule_id"] for r in result.undecided}
        rows = []
        for record in records:
            cid = record["capsule_id"]
            if cid in decided:
                dedupe = next(c for c in decided[cid].decision.constraints if c.id == "dedupe")
                replayed = "decision"
            else:
                assert cid in undecided, cid
                with tempfile.TemporaryDirectory() as tmp, LedgerStore(tmp) as ledger:
                    dedupe = check_dedupe(action_for_record(record, disclosed[cid]), ledger).constraint
                replayed = "no_decision"
            rows.append({"capsule_id": cid,
                         "sealed_record_type": disclosed[cid]["x-deal-v0"]["record_type"],
                         "replay": replayed,
                         "dedupe_result": dedupe.result,
                         "dedupe_evidence_digest": json_digest(dedupe.evidence) if dedupe.evidence is not None else None})
        out["bundles"][name] = rows
    json.dump(out, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main(sys.argv[1])
