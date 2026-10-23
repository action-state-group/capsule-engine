# SPDX-License-Identifier: Apache-2.0
"""Writes the window-edge inputs: check inputs whose history holds one
accepted 9000 purchase at the edge of the caps window, a rolling 7d over
[check time - 7d, check time], both ends included. Counted, the 1299
purchase checked goes over the window limit (9000 + 2000 + 1299 > 10000) and
the check asks; left out, it passes.

The engine reads a timestamp as a Python datetime, to the microsecond, and
drops any digit after the sixth (datetime.fromisoformat); the Go guard reads
nanoseconds. RFC 3339, which a capsule's timestamp follows, allows any
number of fractional digits, though no capsulectl or capsule-emit release
writes more than six. So the inputs pin the edge to the microsecond, at
either end, and with digits past the sixth:

* ``start-exact``, ``start-1us-before``, ``start-1us-after``: the act at the
  window's start, 1 µs before it and 1 µs after it;
* ``start-sub-us``: check at .0000009, act 7d earlier at .0000005, the same
  microsecond as the window's start once read to the microsecond;
* ``end-exact``, ``end-1us-after``: the act at the check time and 1 µs after;
* ``end-sub-us``: check at .0000001, act at .0000005, the same microsecond.

Each is ``go/guard/testdata/emit/pay-history-under.json`` (accepted acts on
10-06, 10-03 and, outside the window, 09-20; a 1299 purchase checked on
10-07) with its newest act's amount and spend raised to 9000 and its time
moved, and the checked record's time set. Run from the repository root:

    python conformance-vectors/guard/v0/inputs/window-edge/generate.py
"""
import copy
import json
from pathlib import Path

from agent_action_capsule import json_digest

ROOT = Path(__file__).resolve().parents[5]
HERE = Path(__file__).resolve().parent
BASE = json.loads((ROOT / "go" / "guard" / "testdata" / "emit" / "pay-history-under.json").read_text())


def edge(checked_at: str, act_at: str) -> dict:
    out = copy.deepcopy(BASE)
    out["record"]["timestamp"] = checked_at
    entry = out["history"][0]
    entry["timestamp"] = act_at
    entry["agent_input"]["body"]["amount_minor"] = entry["agent_input"]["body"]["spend_minor"] = 9000
    entry["model_attestation"]["compute_attestation"]["agent_input_digest"] = json_digest(entry["agent_input"])
    return out


CASES = {
    "start-exact": edge("2026-10-07T21:40:00Z", "2026-09-30T21:40:00Z"),
    "start-1us-before": edge("2026-10-07T21:40:00Z", "2026-09-30T21:39:59.999999Z"),
    "start-1us-after": edge("2026-10-07T21:40:00Z", "2026-09-30T21:40:00.000001Z"),
    "start-sub-us": edge("2026-10-07T21:40:00.0000009Z", "2026-09-30T21:40:00.0000005Z"),
    "end-exact": edge("2026-10-07T21:40:00Z", "2026-10-07T21:40:00Z"),
    "end-1us-after": edge("2026-10-07T21:40:00Z", "2026-10-07T21:40:00.000001Z"),
    "end-sub-us": edge("2026-10-07T21:40:00.0000001Z", "2026-10-07T21:40:00.0000005Z"),
}

for name, envelope in CASES.items():
    (HERE / f"{name}.json").write_text(json.dumps(envelope, indent=1, sort_keys=True) + "\n")
