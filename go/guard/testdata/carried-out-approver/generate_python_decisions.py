# SPDX-License-Identifier: Apache-2.0
"""Replay differential vectors: a carried-out act needs the user's approval.

For each case in corpus.json, replays the real two-deal fixture with deal 1's
approval approver changed (tests/test_real_two_deal_bundle.py's
_deals_changed), and writes the decisions document (expected_decisions.json
shape and encoding) plus deal 2's counterparty_seen_before result to
python-decisions.json. Fails if any case's seen/unseen differs from its expect.

Run with a python whose capsule_engine is the commit under test:
    python -P generate_python_decisions.py <capsule-engine checkout>
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent


def main(checkout: Path) -> None:
    sys.path.insert(0, str(checkout / "tests"))
    import test_real_two_deal_bundle as t

    corpus = json.loads((HERE / "corpus.json").read_text())
    out: dict = {
        "capsule_engine_commit": subprocess.check_output(["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True).strip(),
        "pack_id": t.PACK.pack_id,
        "pack_definition_digest": t.PACK.definition_digest(),
        "cases": {},
    }
    for case in corpus["cases"]:
        if case.get("approver_absent"):
            def change(record: dict) -> dict:
                return {**record, "body": {k: v for k, v in record["body"].items() if k != "approver"}}
        else:
            change = t._with_body(approver=case["approver"])
        result = t._deals_changed({"approval": change})
        seen = t._seen_on_deal_2(result)
        got = "seen" if (seen.result, seen.evidence["prior_count"]) == ("pass", 1) else "unseen"
        assert got == case["expect"], (case["name"], got)
        out["cases"][case["name"]] = {
            "deal2_seen": {"result": seen.result, "prior_count": seen.evidence["prior_count"]},
            "decisions_document": t.canonical(t.decisions_document(result)),
        }
    (HERE / "python-decisions.json").write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve())
