# SPDX-License-Identifier: Apache-2.0
"""Differential vectors for the engine half of the payee fingerprint profile (AMENDMENT 3).

Inputs are capsulectl's own two-deal vectors (capsule-cli
internal/cli/testdata/payee-fp-profile/vectors.json at
705819215be60dbf0c9a7f95aa8de4eac693b27d, vendored into capsule-engine as
tests/fixtures/payee-fp-profile/vectors.json): each deal's pay check and its
counterparty_profile companion, wrapped in a capsule that binds the record by
agent_input_digest.

Three sections; the Go port must reproduce every field:
- replay: a deal bundle (records + disclosures) through
  capsule_engine.report.replay.replay (spend.weekly caps fold, no caps, fresh
  ledger). Per decision: record_type, states_act, target, ignored_inputs,
  outcome, dedupe_result, dedupe_evidence, summary, decision_capsule_id, and
  the share refusal over all decisions (local_only_refusal, null when none).
- live_target: one external-check-input/v0 record entry through
  action_for_check_input: target and ignored_inputs.
- live_seen_before: deal 1 accepted for real (not a dry run) by a plain
  engine, then deal 2 checked by an engine running
  counterparty_seen_before/3.0.0 (2.0.0 before capsule-engine 97a2aff; an
  accepted act counts the same under both): r06 result + evidence, dedupe result +
  evidence, outcome.

Run with a python whose capsule_engine is the commit under test:
    python -P generate.py <capsule-engine checkout> <out.json>

Vendored into the Go port from the engine side's go-vectors (sha256
0038a6d8f2fca195e1f77bbca5b58f0c76a8101ace31fb37b9b41dcda5f7d3fc at
capsule-engine 3a8eb6b) with one addition: live_seen_before "bad-profile",
deal 2 checked with a counterparty_profile of another fp_alg, so the r06
evidence's ignored_inputs marker is pinned too; and from 97a2aff, the
shipped counterparty_seen_before/3.0.0 in place of /2.0.0.
"""
from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from agent_action_capsule import json_digest
from capsule_ledger.ledger import LedgerStore

import capsule_engine
from capsule_engine.folds.loader import load_definition_file as load_fold
from capsule_engine.guards import GuardEngine, LocalSigner
from capsule_engine.guards.capsule import local_only_refusal
from capsule_engine.guards.wickets import load_definition_file as load_wicket
from capsule_engine.report.replay import action_for_check_input, replay

D1, D2 = "2026-10-07T12:00:00Z", "2026-10-08T12:00:00Z"
GOOD = "6c55aadf729943d4ef73ad242b6a4fa8b471d8b4077947fc36d415e2fa354342"
BAD_BLOCKS = {
    "another-fp_alg": {"fp_alg": "hmac-sha256-deal-key", "ids": {"payee": GOOD}},
    "uppercase-hex": {"fp_alg": "hmac-sha256-profile-key", "ids": {"payee": GOOD.upper()}},
    "short-hex": {"fp_alg": "hmac-sha256-profile-key", "ids": {"payee": GOOD[:63]}},
    "trailing-newline": {"fp_alg": "hmac-sha256-profile-key", "ids": {"payee": GOOD + "\n"}},
    "no-payee": {"fp_alg": "hmac-sha256-profile-key", "ids": {}},
    "not-an-object": "hmac-sha256-profile-key:" + GOOD,
}


def _capsule(record, seq, at):
    return {"capsule_id": f"{seq:064x}", "action_id": f"deal-0000000000000000/{seq}", "action_type": "fyi",
            "operator": "household-a", "developer": "capsulectl-deal", "timestamp": at,
            "model_attestation": {"compute_attestation": {"agent_input_digest": json_digest(record)}}}


def _deals(checkout: Path):
    cases = json.loads((checkout / "tests" / "fixtures" / "payee-fp-profile" / "vectors.json").read_text())["cases"]
    return [(json.loads(c["check_jcs"]), json.loads(c["companion_jcs"])) for c in cases]


def _with(companion, mutate):
    out = copy.deepcopy(companion)
    mutate(out["x-deal-v0"])
    return out


def _steps(*records_at):
    return [{"capsule": _capsule(r, seq, at), "record": r} for seq, (r, at) in enumerate(records_at, start=1)]


def _replay_case(fold, steps):
    replayed = replay([s["capsule"] for s in steps], caps_fold=fold,
                      disclosed={s["capsule"]["capsule_id"]: s["record"] for s in steps})
    by_id = {s["capsule"]["capsule_id"]: s["record"] for s in steps}
    decisions = []
    for sourced in replayed.decisions:
        d = sourced.decision
        (dedupe,) = [c for c in d.constraints if c.id == "dedupe"]
        decisions.append({
            "action_id": sourced.record["action_id"],
            "record_type": by_id[sourced.record["capsule_id"]]["x-deal-v0"]["record_type"],
            "states_act": sourced.action.states_act,
            "target": sourced.action.target,
            "ignored_inputs": list(sourced.action.ignored_inputs),
            "outcome": d.outcome,
            "dedupe_result": dedupe.result,
            "dedupe_evidence": dedupe.evidence,
            "decision_capsule_id": d.capsule["capsule_id"],
            "summary": d.reason,
        })
    refusal = local_only_refusal([s.decision.capsule for s in replayed.decisions])
    # From capsule-engine 97a2aff a record that states no act (a companion)
    # gets no decision: it is listed here, by action_id, in input order.
    undecided = [r["action_id"] for r in replayed.undecided]
    return {"inputs": steps, "decisions": decisions, "undecided": undecided, "share_refusal": refusal}


def _entry(record, seq, at, block=None):
    entry = {**_capsule(record, seq, at), "agent_input": record}
    if block is not None:
        entry["counterparty_profile"] = block
    return entry


def _live_seen_before(fold, wicket, deals, with_profile, deal_2_block=None):
    signer = LocalSigner(key_id="go-vectors", secret=b"asg-go-vectors")
    entries = [_entry(deals[n][0], n + 1, (D1, D2)[n],
                      deals[n][1]["x-deal-v0"]["counterparty_profile"] if with_profile else None) for n in (0, 1)]
    if deal_2_block is not None:
        entries[1]["counterparty_profile"] = deal_2_block
    with tempfile.TemporaryDirectory() as tmp, LedgerStore(tmp) as store:
        plain = GuardEngine(ledger=store, caps_fold=fold, signer_provider=lambda: signer)
        first = plain.check(action_for_check_input(entries[0]))
        engine = GuardEngine(ledger=store, caps_fold=fold, signer_provider=lambda: signer, wickets=(wicket,))
        d = engine.check(action_for_check_input(entries[1]))
    by_id = {c.id: c for c in d.constraints}
    return {
        "entries": entries,
        "deal_1_decision": first.capsule["disposition"]["decision"],
        "outcome": d.outcome,
        "seen_before_result": by_id["counterparty_seen_before"].result,
        "seen_before_evidence": by_id["counterparty_seen_before"].evidence,
        "dedupe_result": by_id["dedupe"].result,
        "dedupe_evidence": by_id["dedupe"].evidence,
    }


def main(checkout: Path, out: Path) -> None:
    root = Path(capsule_engine.__file__).parent
    fold = load_fold(root / "folds" / "catalog_defs" / "spend.weekly.yaml")
    wicket = load_wicket(root / "guards" / "wickets" / "catalog_defs" / "counterparty_seen_before.v3.yaml")
    (check_1, comp_1), (check_2, comp_2) = _deals(checkout)
    replay_cases = {
        "two-deals-with-companions": _steps((check_1, D1), (comp_1, D1), (check_2, D2), (comp_2, D2)),
        "two-deals-without-companions": _steps((check_1, D1), (check_2, D2)),
        "transition-per-deal-then-profile": _steps((check_1, D1), (check_2, D2), (comp_2, D2)),
        "companion-wrong-rel": _steps((check_1, D1), (_with(comp_1, lambda b: b["refs"][0].update(rel="checks")), D1)),
        "companion-names-another-record": _steps(
            (check_1, D1), (_with(comp_1, lambda b: b["refs"][0].update(digest="0" * 64)), D1)),
        "two-companions-about-one-check": _steps(
            (check_1, D1), (comp_1, D1),
            (_with(comp_1, lambda b: b.update(counterparty_profile={"fp_alg": "hmac-sha256-profile-key",
                                                                    "ids": {"payee": "b" * 64}})), D1)),
        "companion-without-block": _steps((check_1, D1), (_with(comp_1, lambda b: b.pop("counterparty_profile")), D1)),
    }
    for name, block in BAD_BLOCKS.items():
        replay_cases[f"companion-bad-{name}"] = _steps(
            (check_1, D1), (_with(comp_1, lambda b, v=block: b.update(counterparty_profile=v)), D1))
    live_cases = {"absent": _entry(check_1, 1, D1), "good": _entry(check_1, 1, D1, comp_1["x-deal-v0"]["counterparty_profile"])}
    for name, block in BAD_BLOCKS.items():
        live_cases[f"bad-{name}"] = _entry(check_1, 1, D1, block)
    result = {
        "capsule_engine_commit": subprocess.check_output(["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True).strip(),
        "capsule_engine_path": capsule_engine.__file__,
        "caps_fold": "spend.weekly",
        "replay": {name: _replay_case(fold, steps) for name, steps in replay_cases.items()},
        "live_target": {
            name: {"entry": e, "target": (a := action_for_check_input(e)).target, "ignored_inputs": list(a.ignored_inputs)}
            for name, e in live_cases.items()
        },
        "live_seen_before": {
            "with-profile": _live_seen_before(fold, wicket, [(check_1, comp_1), (check_2, comp_2)], True),
            "without-profile": _live_seen_before(fold, wicket, [(check_1, comp_1), (check_2, comp_2)], False),
            "bad-profile": _live_seen_before(fold, wicket, [(check_1, comp_1), (check_2, comp_2)], True,
                                             BAD_BLOCKS["another-fp_alg"]),
        },
    }
    out.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
