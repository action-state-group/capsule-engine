# Dev tool, not shipped: the engine's expected_decisions for a vendored real
# two-deal fixture, replayed under the catalog pack of the capsule-engine
# commit it runs at, exactly as tests/test_real_two_deal_bundle.py builds
# expected_decisions.json (decisions_document + canonical) there. Used for
# the taxonomy-5 fixture (capsule-engine #120's rc13 bundle), whose own
# expected_decisions.json is the 0.3.3 replay, so the Go differential reads
# both sides under one pack. Run from capsule-engine checked out read-only
# at the commit named in the output:
#   cd <capsule-engine@SHA> && <venv>/bin/python <this file> <SHA> <fixture dir> > <fixture dir>/expected_decisions-<pack version>.json
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, ".")
import capsule_engine  # noqa: E402
from capsule_engine.guards.checks import RUNNABLE_CHECKS  # noqa: E402
from capsule_engine.packs import install_pack, load_pack_dir  # noqa: E402
from capsule_engine.packs.obligation_results import obligation_results  # noqa: E402
from capsule_engine.report.replay import load_disclosed, load_records, load_withheld, replay  # noqa: E402

PACK = load_pack_dir(Path(capsule_engine.__file__).parent / "packs" / "catalog" / "everyday")


def main(sha: str, fixture: Path) -> None:
    own = (fixture / "deal-1.bundle.json", fixture / "deal-2.bundle.json")
    disclosed = load_disclosed(own)
    with tempfile.TemporaryDirectory() as tmp:
        installed = install_pack(PACK, project_dir=Path(tmp) / "replay-project", mode="observe")
        resolved = installed.resolved
        result = replay(list(load_records(own)), caps_fold=resolved.caps_fold(), caps_minor=resolved.caps_minor(),
                        per_action_minor=resolved.per_action_minor(), per_action_reads=resolved.per_action_reads(),
                        manifest_digest=resolved.manifest_digest, disclosed=disclosed, withheld=load_withheld(own),
                        wickets=resolved.configured_wickets(RUNNABLE_CHECKS), pack=installed.pack)
    producer = None
    decisions = []
    for sourced in result.decisions:
        shown = disclosed[sourced.record["capsule_id"]]
        producer = shown["x-deal-v0"].get("producer", producer)
        decisions.append({
            "capsule_id": sourced.record["capsule_id"],
            "record_type": shown["x-deal-v0"].get("record_type"),
            "verb": sourced.action.verb,
            "action_class": sourced.action.action_class,
            "target": sourced.action.target,
            "ignored_inputs": list(sourced.action.ignored_inputs),
            "outcome": sourced.decision.outcome,
            "failing_checks": sorted(c.id for c in sourced.decision.constraints if c.result == "fail"),
            "rules": {r.obligation_id: r.result for r in obligation_results(PACK, sourced.decision.constraints)},
        })
    document = {
        "capsule_engine_commit": sha,
        "pack": {"pack_id": PACK.pack_id, "definition_digest": PACK.definition_digest()},
        "producer": producer,
        "replayed": [p.name for p in own],
        "decisions": decisions,
    }
    sys.stdout.write(json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main(sys.argv[1], Path(sys.argv[2]))
