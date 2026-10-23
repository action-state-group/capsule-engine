#!/usr/bin/env python3
"""Build the statement-in-the-window envelopes: python3 build.py <this dir> rewrites inputs/ byte for byte.

Each step k is ../g3-executed-acts-vectors/inputs/fyi-commit/step-<k>.input.json with one claim the
user's agent made (the fyi statement record of
../seller-013-vectors/go-vectors/check-inputs/a-commit-1900.claims-in-history.json) prepended to its
history, re-dated before the check and moved to the check's operator. Only the capsule header is
edited: the claim's agent_input, and so the digest its capsule seals, is unchanged.
"""
import json, os, shutil, sys

D = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.abspath(__file__))
src = json.load(open(os.path.join(D, "..", "seller-013-vectors", "go-vectors", "check-inputs", "a-commit-1900.claims-in-history.json")))
claim = [h for h in src["history"] if h.get("action_type") == "fyi" and not h.get("disposition")][0]
out = os.path.join(D, "inputs", "statement-in-history")
shutil.rmtree(os.path.join(D, "inputs"), ignore_errors=True)
os.makedirs(out)
for k in range(3):
    env = json.load(open(os.path.join(D, "..", "g3-executed-acts-vectors", "inputs", "fyi-commit", f"step-{k}.input.json")))
    stated = json.loads(json.dumps(claim))
    stated["operator"], stated["timestamp"] = env["record"]["operator"], "2026-10-08T00:00:00Z"
    env["history"] = [stated] + env["history"]
    with open(os.path.join(out, f"step-{k}.input.json"), "wb") as f:
        f.write(json.dumps(env, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode() + b"\n")
