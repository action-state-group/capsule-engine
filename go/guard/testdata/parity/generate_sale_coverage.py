# Dev tool, not shipped: regenerates sale-coverage-parity.json, the engine's
# decisions for sale-coverage-corpus.json (capsule-engine #119, asg/everyday
# 0.3.3: six checks read a marketplace.sale). The corpus is the engine side's as
# given; this is its generate_python_decisions.py with three changes: it
# reads the corpus beside it under this name, takes the commit as an
# argument, and writes to stdout, run like the other generators here: from
# capsule-engine checked out read-only at the commit named in the output,
#   cd <capsule-engine@SHA> && <venv>/bin/python <this file> <SHA> > sale-coverage-parity.json
"""Decision-level differential vectors for asg/everyday/0.3.3's sale coverage.

Runs every case in corpus.json through a GuardEngine built from a pack
installed in observe mode, once under the catalog pack (asg/everyday/0.3.3
at 47c58cd, 0.3.4 from 97a2aff, 0.3.5 from c878edf0, 0.3.6 from a710636c,
labelled by its version)
and once under asg/everyday/0.3.2 (the released bytes kept under
tests/fixtures/packs/everyday-0.3.2), each case on a fresh ledger, and writes
what the Python engine decided to python-decisions.json. The Go port must
reproduce `outcome`, `decision`, `verdict_class`, `failing_constraints` and
`results_of_six` per case per pack.

Run from capsule-engine checked out at the commit under test (see above).
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path

from capsule_ledger.ledger import LedgerStore

from capsule_engine.guards import Action, LocalSigner
from capsule_engine.guards.classes import TAXONOMY_DIGEST, TAXONOMY_VERSION
from capsule_engine.packs import build_engine, install_pack, load_pack_dir

HERE = Path(__file__).parent
SIGNER = LocalSigner(key_id="everyday-0-3-3-differential-key", secret=b"everyday-0-3-3-differential-fixed-key")
SIX = ("channel_change", "material_fields_changed", "offer_fields_changed", "refundability", "task_authority",
       "upfront_amount")


def main(commit: str) -> None:
    checkout = Path(".")
    corpus = json.loads((HERE / "sale-coverage-corpus.json").read_text())
    packs = {
        # the catalog pack: asg/everyday/0.3.6 from a710636c (0.3.5 before)
        "0.3.6": checkout / "capsule_engine" / "packs" / "catalog" / "everyday",
        "0.3.2": checkout / "tests" / "fixtures" / "packs" / "everyday-0.3.2",
    }
    out: dict = {
        "capsule_engine_commit": commit,
        "taxonomy_version": TAXONOMY_VERSION,
        "taxonomy_jcs_digest": TAXONOMY_DIGEST,
        "taxonomy_file_sha256": hashlib.sha256(
            (checkout / "capsule_engine" / "guards" / "action_taxonomy.json").read_bytes()).hexdigest(),
        "packs": {},
        "decisions": {},
    }
    for label, pack_dir in packs.items():
        pack = load_pack_dir(pack_dir)
        out["packs"][label] = {
            "pack_id": pack.pack_id,
            "definition_digest": pack.definition_digest(),
            "pack_yaml_sha256": hashlib.sha256((pack_dir / "pack.yaml").read_bytes()).hexdigest(),
            "taxonomy_pin": None if pack.taxonomy is None else
            {"taxonomy_version": pack.taxonomy.taxonomy_version, "digest": pack.taxonomy.digest},
            "wicket_digests": {c.wicket_id: c.definition_digest() for c in pack.constraints},
        }
        decisions = {}
        for case in corpus["cases"]:
            with tempfile.TemporaryDirectory() as tmp, LedgerStore(Path(tmp) / "ledger") as store:
                installed = install_pack(pack, project_dir=Path(tmp) / "project", mode="observe")
                engine = build_engine(installed, ledger=store, signer_provider=lambda: SIGNER)
                decision = engine.check(Action(**case["action"]), dry_run=case["dry_run"],
                                        task_authority_record=corpus["task_authority_record"])
            results = {c.id: c.result for c in decision.constraints}
            decisions[case["name"]] = {
                "outcome": decision.outcome,
                "decision": decision.capsule["disposition"]["decision"],
                "verdict_class": decision.capsule["disposition"].get("verdict_class"),
                "failing_constraints": sorted(c for c, r in results.items() if r == "fail"),
                "results_of_six": {c: results[c] for c in SIX},
                "caps_result": results["caps"],
                "reason": decision.reason,
            }
        out["decisions"][label] = decisions
    sys.stdout.write(json.dumps(out, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main(sys.argv[1])
