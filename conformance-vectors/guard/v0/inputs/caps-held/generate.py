# SPDX-License-Identifier: Apache-2.0
"""Writes the caps-held inputs: check inputs whose caps window the history
cannot decide, with the purchase over the per-purchase limit. A limit the
check can evaluate still fails under a held window, on both engines
(capsule-engine#214, groups 3 and 4); the held window alone, with nothing
failing, is ``emit.json``'s ``pay-history-incomplete`` and
``pay-history-5-days``. The amounts match no act in their history, so
dedupe passes. One more holds the window through an act alone: an
unchecked act that names a foreign taxonomy in its ``unchecked`` member,
which both engines read before the body's own version.

Each is an existing check input with the checked purchase's amount raised:
the record's ``agent_input`` body and its digest in the attestation are
rewritten together, nothing else. Run from the repository root:

    python conformance-vectors/guard/v0/inputs/caps-held/generate.py
"""
import json
from pathlib import Path

from agent_action_capsule import json_digest

ROOT = Path(__file__).resolve().parents[5]
HERE = Path(__file__).resolve().parent
EMIT = ROOT / "go" / "guard" / "testdata" / "emit"

# (written name, source input, new amount in minor units)
CASES = [
    ("short-history-over-limit", "pay-history-5-days.json", 6000),
    ("incomplete-history-over-limit", "pay-history-incomplete.json", 8000),
]


def raised(envelope: dict, amount: int) -> dict:
    record = envelope["record"]
    body = record["agent_input"]["body"]
    for key in ("amount_minor", "spend_minor"):
        if key in body:
            body[key] = amount
    if "terms" in body and "price_minor" in body["terms"]:
        body["terms"]["price_minor"] = amount
    record["model_attestation"]["compute_attestation"]["agent_input_digest"] = json_digest(record["agent_input"])
    return envelope


def foreign_unchecked(envelope: dict) -> dict:
    entry = envelope["history"][0]
    entry["agent_input"]["body"]["unchecked"]["taxonomy_version"] = "1"
    entry["model_attestation"]["compute_attestation"]["agent_input_digest"] = json_digest(entry["agent_input"])
    return envelope


for name, source, amount in CASES:
    envelope = raised(json.loads((EMIT / source).read_text()), amount)
    (HERE / f"{name}.json").write_text(json.dumps(envelope, indent=1, sort_keys=True) + "\n")
envelope = foreign_unchecked(json.loads((EMIT / "pay-history-unchecked.json").read_text()))
(HERE / "unchecked-act-foreign-taxonomy.json").write_text(json.dumps(envelope, indent=1, sort_keys=True) + "\n")
