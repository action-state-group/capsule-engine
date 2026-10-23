# Dev tool, not shipped: regenerates ask-declared-wickets-parity.json, the
# engine's decisions for ask-declared-wickets-corpus.json (capsule-engine
# #113: a failing check asks an approver
# when every obligation bound to it declares default_disposition ASK and the
# class names an approver_role; an integrity check never does). The corpus
# is the engine side's 48 vectors (go-vectors/corpus.json), vendored as
# given; this is its generate_python_decisions.py, run like the other
# generators here: from capsule-engine checked out read-only at the commit
# named in the output, reading the vendored corpus,
#
#   cd <capsule-engine@SHA> && <venv>/bin/python <this file> <SHA> > ask-declared-wickets-parity.json
#
# Each case runs on a fresh ledger under the installed catalog everyday pack,
# its obligations re-declared (redeclare, redeclare_rule_prefix) or extended
# (add_obligations) where the case says (a test-only variant); the steps run
# in order and the last decision is recorded. No model is loaded.
import json
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

from capsule_ledger.ledger import LedgerStore

sys.path.insert(0, ".")
from capsule_engine.guards import Action, LocalSigner  # noqa: E402
from capsule_engine.packs import build_engine, install_pack, load_pack_dir  # noqa: E402
from capsule_engine.packs.install import ask_gate_selectors  # noqa: E402
from capsule_engine.packs.obligation_results import obligation_results  # noqa: E402
from capsule_engine.packs.schema import Obligation  # noqa: E402

HERE = Path(__file__).parent
SIGNER = LocalSigner(key_id="wickets-ask-differential-key", secret=b"wickets-ask-differential-fixed-key")
PACK_DIR = Path("capsule_engine/packs/catalog/everyday")


def _pack(case):
    pack = load_pack_dir(PACK_DIR)
    redeclare = dict(case.get("redeclare", {}))
    prefix = case.get("redeclare_rule_prefix")
    obligations = list(pack.obligations)
    if prefix:
        redeclare.update({o.id: "NEVER" for o in obligations if o.id.startswith(prefix + "-")})
    obligations = [replace(o, default_disposition=redeclare[o.id]) if o.id in redeclare else o for o in obligations]
    for extra in case.get("add_obligations", []):
        check = next(o.check for o in obligations if o.id.startswith(extra["check_of_rule"] + "-"))
        obligations.append(Obligation(id=extra["id"], statement=extra["statement"], check=check,
                                      default_disposition=extra["default_disposition"]))
    return replace(pack, obligations=tuple(obligations))


def main(sha):
    corpus = json.loads((HERE / "ask-declared-wickets-corpus.json").read_text())
    out = {"capsule_engine_commit": sha, "source": "go/guard/testdata/parity/generate_ask_declared_wickets.py",
           "pack_id": load_pack_dir(PACK_DIR).pack_id, "pack_definition_digest": load_pack_dir(PACK_DIR).definition_digest(),
           "decisions": {}}
    for case in corpus["cases"]:
        pack = _pack(case)
        with tempfile.TemporaryDirectory() as tmp, LedgerStore(Path(tmp) / "ledger") as store:
            engine = build_engine(install_pack(pack, project_dir=Path(tmp) / "project", mode="observe"),
                                  ledger=store, signer_provider=lambda: SIGNER)
            decision = None
            for step in case["steps"]:
                decision = engine.check(Action(**step["action"]), dry_run=step["dry_run"],
                                        task_authority_record=step.get("task_authority_record"))
            assert decision is not None
            out["decisions"][case["name"]] = {
                "ask_wickets": sorted(engine.ask_wickets),
                "ask_gate_selectors": sorted(ask_gate_selectors(pack)),
                "outcome": decision.outcome,
                "decision": decision.capsule["disposition"]["decision"],
                "verdict_class": decision.capsule["disposition"].get("verdict_class"),
                "failing_constraints": [c.id for c in decision.constraints if c.result == "fail"],
                "failing_rules": [r.obligation_id for r in obligation_results(pack, decision.constraints)
                                  if r.result == "fail"],
                "reason": decision.reason,
            }
    json.dump(out, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main(sys.argv[1])
