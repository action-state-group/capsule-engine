# Dev tool, not shipped: the engine's replay of each real unchecked-act bundle (build.sh), under the
# catalog everyday pack of the capsule-engine commit it runs at, as generate_real_two_deal_expected.py
# replays one: every record that gets a decision, with its caps result and caps evidence, so the Go
# test can name what the engine counts in the spend window. Run from capsule-engine checked out
# read-only at the commit named in the output:
#   cd <capsule-engine@SHA> && <venv>/bin/python <this file> <SHA> <fixture dir> > <fixture dir>/engine-replay.json
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, ".")
import capsule_engine  # noqa: E402
from capsule_engine.guards.checks import RUNNABLE_CHECKS  # noqa: E402
from capsule_engine.packs import install_pack, load_pack_dir  # noqa: E402
from capsule_engine.report.replay import load_disclosed, load_records, load_withheld, replay  # noqa: E402

PACK = load_pack_dir(Path(capsule_engine.__file__).parent / "packs" / "catalog" / "everyday")


def main(sha: str, fixture: Path) -> None:
    out = {"capsule_engine_commit": sha, "pack": {"pack_id": PACK.pack_id, "definition_digest": PACK.definition_digest()},
           "bundles": {}}
    for name in ("x-deal-v0.bundle.json", "typed.bundle.json"):
        own = (fixture / name,)
        disclosed = load_disclosed(own)
        with tempfile.TemporaryDirectory() as tmp:
            installed = install_pack(PACK, project_dir=Path(tmp) / "replay-project", mode="observe")
            resolved = installed.resolved
            result = replay(list(load_records(own)), caps_fold=resolved.caps_fold(), caps_minor=resolved.caps_minor(),
                            per_action_minor=resolved.per_action_minor(), per_action_reads=resolved.per_action_reads(),
                            manifest_digest=resolved.manifest_digest, disclosed=disclosed, withheld=load_withheld(own),
                            wickets=resolved.configured_wickets(RUNNABLE_CHECKS), pack=installed.pack)
        decisions = []
        for sourced in result.decisions:
            (caps,) = [c for c in sourced.decision.constraints if c.id == "caps"]
            decisions.append({"capsule_id": sourced.record["capsule_id"], "verb": sourced.action.verb,
                              "amount_minor": sourced.action.amount_minor, "outcome": sourced.decision.outcome,
                              "caps_result": caps.result, "caps_evidence": caps.evidence})
        out["bundles"][name] = {"decisions": decisions, "undecided": [r["capsule_id"] for r in result.undecided]}
    sys.stdout.write(json.dumps(out, sort_keys=True, indent=1, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main(sys.argv[1], Path(sys.argv[2]))
