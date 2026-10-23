# SPDX-License-Identifier: Apache-2.0
"""Decision-level differential vectors: a deal id from a chain id, and its conflict.

Builds a synthetic corpus of deal checks (x-deal-v0 records, typed
proposed-action/v0 records, and records stating both x-deal-v0.deal_id and
chain_id), writes it to corpus.json, replays each case on a fresh ledger under
two configurations, and writes what the Python engine decided to
python-decisions.json:

  bare      GuardEngine with the spend.weekly fold and no wickets, so a
            conflicted check that would otherwise be allowed is visible as the
            refusal (reject, verdict not_evaluable).
  everyday  the installed everyday pack, as the Go checker runs it.

The Go port must reproduce, per case and config and per decided record:
`deal_id` (the deal sealed on the decision, or null), `deal_id_conflict` (the
two field names, or null), `outcome`, `verdict`, `decision`,
`failing_constraints`, `dedupe_repeat` (null | "other_deal") and, for
everyday, `failing_rules`. A conflict never seals a deal and never names a
value. The `party-role-*` cases are live: the external-check-input/v0 envelope
is in the corpus, and the engine reads only `record` and `item_ref` of it, so
with and without `party_role` the sealed decision capsule is identical
(`capsule_identical`).

Run with a python whose capsule_engine is the commit under test:
    python -P generate_python_decisions.py <capsule-engine checkout>
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from agent_action_capsule import json_digest
from capsule_ledger.ledger import LedgerStore

from capsule_engine.folds.loader import load_definition_file as load_fold
from capsule_engine.guards import GuardEngine, LocalSigner
from capsule_engine.guards.checks import RUNNABLE_CHECKS
from capsule_engine.guards.classes import TAXONOMY_VERSION
from capsule_engine.packs import install_pack, load_pack_dir
from capsule_engine.packs.obligation_results import obligation_results
from capsule_engine.report.replay import action_for_check_input, replay

HERE = Path(__file__).parent
SIGNER = LocalSigner(key_id="deal-id-differential-key", secret=b"deal-id-differential-fixed-key")
DEAL_A = "deal-aaaaaaaaaaaaaaaa"
DEAL_B = "deal-bbbbbbbbbbbbbbbb"
PAYEE = "a" * 64
ITEM = "c" * 64
DAYS = ["2026-10-07T12:00:00Z", "2026-10-08T12:00:00Z"]
ABSENT = object()


def _body() -> dict:
    return {"action": "pay", "action_class": "money.purchase", "taxonomy_version": TAXONOMY_VERSION, "currency": "USD",
            "amount_minor": 1_500, "spend_minor": 1_500, "direction": "out"}


def _capsule(record: dict, seq: int) -> dict:
    return {"capsule_id": f"{seq:064x}", "action_id": f"deal-0000000000000000/{seq}", "action_type": "fyi",
            "operator": "household-a", "developer": "capsulectl-deal", "timestamp": DAYS[(seq - 1) % 2],
            "model_attestation": {"compute_attestation": {"agent_input_digest": json_digest(record)}}}


def _untyped(seq: int, deal: object, chain_id: object = ABSENT) -> dict:
    block = {"record_type": "check", "seq": seq, "counterparty": {"fp_alg": "hmac-sha256-deal-key", "ids": {"payee": PAYEE}}}
    if deal is not ABSENT:
        block["deal_id"] = deal
    record = {"body": _body(), "x-deal-v0": block}
    if chain_id is not ABSENT:
        record["chain_id"] = chain_id
    return {"capsule": _capsule(record, seq), "agent_input": record}


def _typed(seq: int, chain: object) -> dict:
    record = {"type": "proposed-action/v0", "canonicalization": "jcs", "chain_id": chain, "seq": seq,
              "at": DAYS[(seq - 1) % 2], "body": _body()}
    return {"capsule": _capsule(record, seq), "agent_input": record}


def corpus() -> dict:
    replay_cases = {
        "deal-id-only": [_untyped(1, DEAL_A)],
        "chain-id-only": [_typed(1, DEAL_A)],
        "both-equal": [_untyped(1, DEAL_A, DEAL_A)],
        "both-different": [_untyped(1, DEAL_A, DEAL_B)],
        "both-different-empty-deal-id": [_untyped(1, "", DEAL_A)],
        "both-different-null-chain-id": [_untyped(1, DEAL_A, None)],
        "typed-repeat-same-deal": [_typed(1, DEAL_A), _typed(2, DEAL_A)],
        "typed-repeat-other-deal": [_typed(1, DEAL_A), _typed(2, DEAL_B)],
        "conflicted-repeat": [_untyped(1, DEAL_A), _untyped(2, DEAL_A, DEAL_B)],
    }
    entry = _untyped(1, DEAL_A)
    record = {**entry["capsule"], "agent_input": entry["agent_input"]}
    live_cases = {f"party-role-{role}": {"record": record, "item_ref": ITEM, "party_role": role}
                  for role in ("buyer", "seller", "neither")}
    live_cases["party-role-absent"] = {"record": record, "item_ref": ITEM}
    return {"engine_taxonomy_version": TAXONOMY_VERSION, "replay_cases": replay_cases, "live_cases": live_cases}


def _decision_row(decision, pack) -> dict:
    dedupe = next(c for c in decision.constraints if c.id == "dedupe")
    row = {
        "deal_id": decision.capsule["asg_payload"].get("deal_id"),
        "deal_id_conflict": decision.deal_id_conflict,
        "outcome": decision.outcome,
        "verdict": decision.verdict,
        "decision": decision.capsule["disposition"]["decision"],
        "failing_constraints": sorted(c.id for c in decision.constraints if c.result == "fail"),
        "dedupe_repeat": (dedupe.evidence or {}).get("repeat"),
        "reason": decision.reason,
    }
    if pack is not None:
        row["failing_rules"] = sorted(r.obligation_id for r in obligation_results(pack, decision.constraints)
                                      if r.result == "fail")
    return row


def _replay(steps: list[dict], pack, tmp: Path):
    records = [s["capsule"] for s in steps]
    disclosed = {s["capsule"]["capsule_id"]: s["agent_input"] for s in steps}
    if pack is None:
        return replay(records, caps_fold=load_fold(FOLD), disclosed=disclosed)
    installed = install_pack(pack, project_dir=tmp / "project", mode="observe")
    resolved = installed.resolved
    return replay(records, caps_fold=resolved.caps_fold(), caps_minor=resolved.caps_minor(),
                  per_action_minor=resolved.per_action_minor(), per_action_reads=resolved.per_action_reads(),
                  manifest_digest=resolved.manifest_digest, disclosed=disclosed,
                  wickets=resolved.configured_wickets(RUNNABLE_CHECKS), pack=installed.pack)


def main(checkout: Path) -> None:
    global FOLD
    FOLD = checkout / "capsule_engine" / "folds" / "catalog_defs" / "spend.weekly.yaml"
    pack = load_pack_dir(checkout / "capsule_engine" / "packs" / "catalog" / "everyday")
    data = corpus()
    (HERE / "corpus.json").write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    out: dict = {
        "capsule_engine_commit": subprocess.check_output(["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True).strip(),
        "pack_id": pack.pack_id,
        "pack_definition_digest": pack.definition_digest(),
        "engine_taxonomy_version": TAXONOMY_VERSION,
        "corpus_sha256": hashlib.sha256((HERE / "corpus.json").read_bytes()).hexdigest(),
        "replay": {},
        "live": {},
    }
    for name, steps in data["replay_cases"].items():
        out["replay"][name] = {}
        for config, p in (("bare", None), ("everyday", pack)):
            with tempfile.TemporaryDirectory() as tmp:
                result = _replay(steps, p, Path(tmp))
            out["replay"][name][config] = [_decision_row(s.decision, p) for s in result.decisions]
    capsules = {}
    for name, envelope in data["live_cases"].items():
        with tempfile.TemporaryDirectory() as tmp, LedgerStore(Path(tmp) / "ledger") as store:
            engine = GuardEngine(ledger=store, caps_fold=load_fold(FOLD), signer_provider=lambda: SIGNER)
            action = action_for_check_input(envelope["record"], item_ref=envelope.get("item_ref"))
            decision = engine.check(action, dry_run=True)
        capsules[name] = decision.capsule
        out["live"][name] = _decision_row(decision, None)
    for name in out["live"]:
        out["live"][name]["capsule_identical"] = capsules[name] == capsules["party-role-absent"]
    (HERE / "python-decisions.json").write_text(json.dumps(out, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
