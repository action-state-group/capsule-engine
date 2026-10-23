# SPDX-License-Identifier: Apache-2.0
"""Decision-level differential vectors: a typed record's counterparty as its payee target.

Builds a synthetic corpus of deal checks: typed proposed-action/v0 records
sealing (or not) body.counterparty under fp_alg hmac-sha256-chain-key, the
x-deal-v0 equivalent sealing x-deal-v0.counterparty under
hmac-sha256-deal-key, and counterparty_profile companions. Writes it to
corpus.json, replays each case on a fresh ledger under the installed everyday
pack (which configures counterparty_seen_before and dedupe on money.purchase),
and writes what the Python engine decided to python-decisions.json.

The Go port must reproduce, per case and per decided record: `target` (the
action's target: `payee-fp:<fp_alg>:<hex>` or null), `ignored_inputs`,
`r06` (counterparty_seen_before: result, fold_key value, missing_field),
`dedupe` (result, repeat), `outcome`, `verdict`, `decision`,
`failing_constraints` and `failing_rules`. The live cases are
external-check-input/v0 envelopes; only `target` and `ignored_inputs` of the
bridged action are compared there.

Rules the vectors pin:
  - a typed check's target is its body.counterparty payee, under the fp_alg
    the body seals (chain-key); an x-deal-v0 check's is its block's (deal-key)
  - an x-deal-v0 check never reads a counterparty from its body
  - a well-formed profile block (live input or replay companion) replaces it
    with payee-fp:hmac-sha256-profile-key:<hex>; a malformed one is ignored
    and named in ignored_inputs
  - no counterparty sealed -> no target -> r06 n/a, missing_field "target"
  - an identical typed payment repeated in one deal to one payee is a dedupe
    fail (DENY); the same payment to another payee is a different act

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

from capsule_engine.guards.checks import RUNNABLE_CHECKS
from capsule_engine.guards.classes import TAXONOMY_VERSION
from capsule_engine.packs import install_pack, load_pack_dir
from capsule_engine.packs.obligation_results import obligation_results
from capsule_engine.report.replay import action_for_check_input, replay

HERE = Path(__file__).parent
DEAL = "deal-aaaaaaaaaaaaaaaa"
PAYEE = "a" * 64
OTHER_PAYEE = "b" * 64
PROFILE_PAYEE = "d" * 64
DAYS = ["2026-10-07T12:00:00Z", "2026-10-08T12:00:00Z"]


def _body(counterparty: dict | None = None) -> dict:
    body = {"action": "pay", "action_class": "money.purchase", "taxonomy_version": TAXONOMY_VERSION, "currency": "USD",
            "amount_minor": 1_500, "spend_minor": 1_500}
    if counterparty is not None:
        body["counterparty"] = counterparty
    return body


def _cp(alg: str, payee: str) -> dict:
    return {"fp_alg": alg, "ids": {"payee": payee}}


def _capsule(record: dict, seq: int) -> dict:
    return {"capsule_id": f"{seq:064x}", "action_id": f"{DEAL}/{seq}", "action_type": "fyi",
            "operator": "household-a", "developer": "capsulectl-deal", "timestamp": DAYS[(seq - 1) % 2],
            "model_attestation": {"compute_attestation": {"agent_input_digest": json_digest(record)}}}


def _typed(seq: int, payee: str | None) -> dict:
    cp = _cp("hmac-sha256-chain-key", payee) if payee is not None else None
    record = {"type": "proposed-action/v0", "canonicalization": "jcs", "chain_id": DEAL, "seq": seq,
              "at": DAYS[(seq - 1) % 2], "body": _body(cp)}
    return {"capsule": _capsule(record, seq), "agent_input": record}


def _untyped(seq: int, block_payee: str | None, body_payee: str | None = None) -> dict:
    block = {"record_type": "check", "deal_id": DEAL, "seq": seq}
    if block_payee is not None:
        block["counterparty"] = _cp("hmac-sha256-deal-key", block_payee)
    body_cp = _cp("hmac-sha256-chain-key", body_payee) if body_payee is not None else None
    record = {"body": _body(body_cp), "x-deal-v0": block}
    return {"capsule": _capsule(record, seq), "agent_input": record}


def _profile(payee: str, alg: str = "hmac-sha256-profile-key") -> dict:
    return {"fp_alg": alg, "ids": {"payee": payee}}


def _companion(seq: int, about: dict, payee: str) -> dict:
    ref = {"rel": "about", "type": "deal-record", "digest_alg": "SHA-256", "digest": json_digest(about["agent_input"])}
    record = {"body": {}, "x-deal-v0": {"record_type": "counterparty_profile", "deal_id": DEAL, "seq": seq,
                                         "refs": [ref], "counterparty_profile": _profile(payee)}}
    return {"capsule": _capsule(record, seq), "agent_input": record}


def corpus() -> dict:
    typed_one = _typed(1, PAYEE)
    replay_cases = {
        "typed-payee": [typed_one],
        "typed-no-counterparty": [_typed(1, None)],
        "untyped-payee": [_untyped(1, PAYEE)],
        "untyped-body-counterparty-only": [_untyped(1, None, PAYEE)],
        "typed-repeat-same-payee": [_typed(1, PAYEE), _typed(2, PAYEE)],
        "typed-same-payment-two-payees": [_typed(1, PAYEE), _typed(2, OTHER_PAYEE)],
        "typed-companion-profile": [typed_one, _companion(2, typed_one, PROFILE_PAYEE)],
    }

    def envelope(step: dict, profile: dict | None = None) -> dict:
        record = {**step["capsule"], "agent_input": step["agent_input"]}
        if profile is not None:
            record["counterparty_profile"] = profile
        return {"record": record}

    live_cases = {
        "typed-payee": envelope(typed_one),
        "typed-no-counterparty": envelope(_typed(1, None)),
        "typed-profile": envelope(typed_one, _profile(PROFILE_PAYEE)),
        "typed-profile-malformed": envelope(typed_one, _profile(PROFILE_PAYEE, "hmac-sha256-chain-key")),
    }
    return {"engine_taxonomy_version": TAXONOMY_VERSION, "replay_cases": replay_cases, "live_cases": live_cases}


def _constraint(decision, check_id: str):
    return next(c for c in decision.constraints if c.id == check_id)


def _decision_row(decision, action, pack) -> dict:
    r06 = _constraint(decision, "counterparty_seen_before")
    dedupe = _constraint(decision, "dedupe")
    r06_evidence = r06.evidence or {}
    return {
        "target": action.target,
        "ignored_inputs": list(action.ignored_inputs),
        "r06": {"result": r06.result, "fold_key": (r06_evidence.get("fold_key") or {}).get("value"),
                "missing_field": r06_evidence.get("missing_field")},
        "dedupe": {"result": dedupe.result, "repeat": (dedupe.evidence or {}).get("repeat")},
        "outcome": decision.outcome,
        "verdict": decision.verdict,
        "decision": decision.capsule["disposition"]["decision"],
        "failing_constraints": sorted(c.id for c in decision.constraints if c.result == "fail"),
        "failing_rules": sorted(r.obligation_id for r in obligation_results(pack, decision.constraints)
                                if r.result == "fail"),
    }


def _replay(steps: list[dict], pack, tmp: Path):
    records = [s["capsule"] for s in steps]
    disclosed = {s["capsule"]["capsule_id"]: s["agent_input"] for s in steps}
    installed = install_pack(pack, project_dir=tmp / "project", mode="observe")
    resolved = installed.resolved
    return replay(records, caps_fold=resolved.caps_fold(), caps_minor=resolved.caps_minor(),
                  per_action_minor=resolved.per_action_minor(), per_action_reads=resolved.per_action_reads(),
                  manifest_digest=resolved.manifest_digest, disclosed=disclosed,
                  wickets=resolved.configured_wickets(RUNNABLE_CHECKS), pack=installed.pack)


def main(checkout: Path) -> None:
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
        with tempfile.TemporaryDirectory() as tmp:
            result = _replay(steps, pack, Path(tmp))
        rows = [_decision_row(sourced.decision, sourced.action, pack) for sourced in result.decisions]
        out["replay"][name] = rows
    for name, envelope in data["live_cases"].items():
        action = action_for_check_input(envelope["record"])
        out["live"][name] = {"target": action.target, "ignored_inputs": list(action.ignored_inputs)}
    (HERE / "python-decisions.json").write_text(json.dumps(out, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
