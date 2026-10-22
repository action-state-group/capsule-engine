# Dev tool, not shipped: regenerates dedupe-act-parity.json, the engine's
# decisions for dedupe-act-corpus.json (capsule-engine #106:
# an act stated against a pinned taxonomy,
# as every bridged deal check is, is keyed on operator, developer,
# action_class, target and amount, with the record's action_type and
# action_id prefix left out and the scan over every action_type; any other
# action keeps the legacy key). The corpus and this generator are the engine
# side's vectors, vendored as given with one change to the corpus:
# its taxonomy_version stamps read "6" where the engine side wrote "3" (taxonomy 6,
# 7ae6b95: the record being checked must carry the engine's version, and
# capsulectl stamps 6; the engine's dedupe key reads only whether a version
# is set, never which). This generator runs like the other generators here,
# from capsule-engine checked out read-only at the commit named in the
# output,
#
#   cd <capsule-engine@SHA> && <venv>/bin/python <this file> <SHA> > dedupe-act-parity.json
#
# and writes each step's input as the Go port reads it: a check step as the
# sealed capsule (its header, the disclosed agent_input, and the
# agent_input_digest over it), an action step as the normalized action. Each
# case runs on a fresh ledger under the installed catalog everyday pack, every
# step a dry run, and the last decision is recorded. No model is loaded.
import json
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path

from agent_action_capsule import json_digest
from capsule_ledger.ledger import LedgerStore

sys.path.insert(0, ".")
from capsule_engine.guards import Action, LocalSigner  # noqa: E402
from capsule_engine.guards.checks.dedupe import equivalence_key_for_action, equivalence_key_for_capsule  # noqa: E402
from capsule_engine.packs import build_engine, install_pack, load_pack_dir  # noqa: E402
from capsule_engine.report.replay import action_for_record  # noqa: E402

HERE = Path(__file__).parent
SIGNER = LocalSigner(key_id="dedupe-act-differential-key", secret=b"dedupe-act-differential-fixed-key")
PACK_DIR = Path("capsule_engine/packs/catalog/everyday")


def _step(step):
    """The step's action, and its input as the Go port reads it."""
    if "action" in step:
        return Action(**step["action"]), {"action": step["action"]}
    record = step["check"]["record"]
    capsule = dict(step["check"]["capsule"])
    capsule["model_attestation"] = {"compute_attestation": {"agent_input_digest": json_digest(record)}}
    action = action_for_record(capsule, record)
    assert action.taxonomy_version is not None, "the bridge refused the check"
    return action, {"record": dict(capsule, agent_input=record)}


def main(sha):
    corpus = json.loads((HERE / "dedupe-act-corpus.json").read_text())
    pack = load_pack_dir(PACK_DIR)
    out = {"capsule_engine_commit": sha, "source": "go/guard/testdata/parity/generate_dedupe_act.py",
           "pack_id": pack.pack_id, "pack_definition_digest": pack.definition_digest(),
           "decisions": {}, "key_vectors": []}
    for case in corpus["cases"]:
        with tempfile.TemporaryDirectory() as tmp, LedgerStore(Path(tmp) / "ledger") as store:
            engine = build_engine(install_pack(pack, project_dir=Path(tmp) / "project", mode="observe"),
                                  ledger=store, signer_provider=lambda: SIGNER)
            ids, steps, decision = [], [], None
            for step in case["steps"]:
                action, given = _step(step)
                decision = engine.check(action, dry_run=True)
                ids.append(decision.capsule["capsule_id"])
                steps.append(dict(given, candidate_key=equivalence_key_for_action(action),
                                  decision_capsule_key=equivalence_key_for_capsule(decision.capsule),
                                  decision_action_type=decision.capsule["action_type"],
                                  decision_action_id=decision.capsule["action_id"]))
            dedupe = next(c for c in decision.constraints if c.id == "dedupe")
            matched = (dedupe.evidence or {}).get("matched_capsule_id")
            out["decisions"][case["name"]] = {
                "outcome": decision.outcome,
                "decision": decision.capsule["disposition"]["decision"],
                "failing_constraints": [c.id for c in decision.constraints if c.result == "fail"],
                "dedupe_result": dedupe.result,
                "dedupe_matched_step": ids.index(matched) if matched else None,
                "steps": steps,
            }
    for vector in corpus["key_vectors"]:
        action = Action(**vector["action"])
        out["key_vectors"].append({"name": vector["name"], "action": vector["action"], "fields": vector["fields"],
                                   "digest": json_digest(vector["fields"]),
                                   "equivalence_key_for_action": equivalence_key_for_action(action)})
    json.dump(out, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main(sys.argv[1])
