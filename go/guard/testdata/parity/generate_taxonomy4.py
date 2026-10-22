# Dev tool, not shipped: regenerates taxonomy4-parity.json, the engine's
# decisions for taxonomy4-corpus.json (capsule-engine #109:
# taxonomy 4 gives
# booking.cancel, data.delete, communication.publish and disclosure.personal
# approver_role account_holder, so their ASK-declared gate rules ask), and
# the taxonomy the engine ran with (version, JCS digest, file sha256). The
# corpus and this generator are the engine side's vectors, vendored as
# given except that this one runs like the other generators here: from
# capsule-engine checked out read-only at the commit named in the output,
#
#   cd <capsule-engine@SHA> && <venv>/bin/python <this file> <SHA> > taxonomy4-parity.json
#
# Each case runs on a fresh ledger under the installed catalog everyday pack,
# its obligations re-declared where the case says (a test-only variant);
# the steps run in order and the last decision is recorded. No model is
# loaded.
import hashlib
import json
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

from capsule_ledger.ledger import LedgerStore

sys.path.insert(0, ".")
from capsule_engine.guards import Action, LocalSigner  # noqa: E402
from capsule_engine.guards.classes import TAXONOMY_DIGEST, TAXONOMY_VERSION  # noqa: E402
from capsule_engine.packs import build_engine, install_pack, load_pack_dir  # noqa: E402
from capsule_engine.packs.install import ask_gate_selectors  # noqa: E402
from capsule_engine.packs.obligation_results import obligation_results  # noqa: E402

HERE = Path(__file__).parent
SIGNER = LocalSigner(key_id="taxonomy-4-differential-key", secret=b"taxonomy-4-differential-fixed-key")
PACK_DIR = Path("capsule_engine/packs/catalog/everyday")


def _pack(redeclare):
    pack = load_pack_dir(PACK_DIR)
    obligations = tuple(
        replace(o, default_disposition=redeclare[o.id]) if o.id in redeclare else o for o in pack.obligations
    )
    return replace(pack, obligations=obligations)


def main(sha):
    corpus = json.loads((HERE / "taxonomy4-corpus.json").read_text())
    out = {"capsule_engine_commit": sha, "source": "go/guard/testdata/parity/generate_taxonomy4.py",
           "pack_id": load_pack_dir(PACK_DIR).pack_id, "pack_definition_digest": load_pack_dir(PACK_DIR).definition_digest(),
           "taxonomy_version": TAXONOMY_VERSION, "taxonomy_jcs_digest": TAXONOMY_DIGEST,
           "taxonomy_file_sha256": hashlib.sha256(Path("capsule_engine/guards/action_taxonomy.json").read_bytes()).hexdigest(),
           "decisions": {}}
    for case in corpus["cases"]:
        pack = _pack(case.get("redeclare", {}))
        with tempfile.TemporaryDirectory() as tmp, LedgerStore(Path(tmp) / "ledger") as store:
            engine = build_engine(install_pack(pack, project_dir=Path(tmp) / "project", mode="observe"),
                                  ledger=store, signer_provider=lambda: SIGNER)
            decision = None
            for step in case["steps"]:
                decision = engine.check(Action(**step["action"]), dry_run=step["dry_run"])
            out["decisions"][case["name"]] = {
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
