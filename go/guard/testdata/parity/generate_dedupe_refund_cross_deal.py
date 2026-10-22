# Dev tool, not shipped: regenerates dedupe-refund-cross-deal-parity.json, the
# engine's decisions for dedupe-refund-cross-deal-corpus.json. Two engine
# changes are held here. #108 (49b2fe8): a check that moves money in
# (direction "in") is keyed on the amount it returns (returned_minor, then
# amount_minor, then cancelled_amount_minor, first integer wins) and the
# digest of its typed reverses_ref, in place of the amount; a bare-string
# reverses_ref is absent. #111 (35874b8): a key match where both checks
# state an x-deal-v0.deal_id and they differ fails asking the class's
# approver; any other match is refused. It runs like the other generators
# here, from capsule-engine checked out read-only at the commit named in the
# output,
#
#   cd <capsule-engine@SHA> && <venv>/bin/python <this file> <SHA> > dedupe-refund-cross-deal-parity.json
#
# Each case runs on a fresh ledger under the installed catalog everyday pack,
# every step a dry run; every step's decision is recorded (with its capsule
# id, which a later match's evidence names), with its input as
# the Go port reads it (the sealed capsule: its header, the disclosed
# agent_input, and the agent_input_digest over it). No model is loaded.
import json
import sys
import tempfile
from pathlib import Path

from agent_action_capsule import json_digest
from capsule_ledger.ledger import LedgerStore

sys.path.insert(0, ".")
from capsule_engine.guards import LocalSigner  # noqa: E402
from capsule_engine.guards.checks.dedupe import equivalence_key_for_action, equivalence_key_for_capsule  # noqa: E402
from capsule_engine.packs import build_engine, install_pack, load_pack_dir  # noqa: E402
from capsule_engine.report.replay import action_for_record  # noqa: E402

HERE = Path(__file__).parent
SIGNER = LocalSigner(key_id="dedupe-refund-cross-deal-key", secret=b"dedupe-refund-cross-deal-fixed-key")
PACK_DIR = Path("capsule_engine/packs/catalog/everyday")


def _step(step):
    record = step["check"]["record"]
    capsule = dict(step["check"]["capsule"])
    capsule["model_attestation"] = {"compute_attestation": {"agent_input_digest": json_digest(record)}}
    action = action_for_record(capsule, record)
    assert action.taxonomy_version is not None, "the bridge refused the check"
    return action, dict(capsule, agent_input=record)


def main(sha):
    corpus = json.loads((HERE / "dedupe-refund-cross-deal-corpus.json").read_text())
    pack = load_pack_dir(PACK_DIR)
    out = {"capsule_engine_commit": sha, "source": "internal/guard/testdata/parity/generate_dedupe_refund_cross_deal.py",
           "pack_id": pack.pack_id, "pack_definition_digest": pack.definition_digest(), "cases": {}}
    for case in corpus["cases"]:
        with tempfile.TemporaryDirectory() as tmp, LedgerStore(Path(tmp) / "ledger") as store:
            engine = build_engine(install_pack(pack, project_dir=Path(tmp) / "project", mode="observe"),
                                  ledger=store, signer_provider=lambda: SIGNER)
            ids, steps = [], []
            for step in case["steps"]:
                action, record = _step(step)
                decision = engine.check(action, dry_run=True)
                dedupe = next(c for c in decision.constraints if c.id == "dedupe")
                matched = (dedupe.evidence or {}).get("matched_capsule_id")
                payload = decision.capsule["asg_payload"]
                steps.append({
                    "record": record,
                    "outcome": decision.outcome,
                    "failing_constraints": [c.id for c in decision.constraints if c.result == "fail"],
                    "dedupe_result": dedupe.result,
                    "dedupe_reason": dedupe.reason,
                    "dedupe_evidence": dedupe.evidence,
                    "dedupe_evidence_digest": json_digest(dedupe.evidence) if dedupe.evidence is not None else None,
                    "dedupe_matched_step": ids.index(matched) if matched in ids else None,
                    "candidate_key": equivalence_key_for_action(action),
                    "decision_capsule_key": equivalence_key_for_capsule(decision.capsule),
                    "sealed_returned_minor": payload.get("returned_minor"),
                    "sealed_reverses_ref": payload.get("reverses_ref"),
                    "sealed_deal_id": payload.get("deal_id"),
                    "decision_capsule_id": decision.capsule["capsule_id"],
                })
                ids.append(decision.capsule["capsule_id"])
            out["cases"][case["name"]] = steps
    json.dump(out, sys.stdout, indent=1, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main(sys.argv[1])
