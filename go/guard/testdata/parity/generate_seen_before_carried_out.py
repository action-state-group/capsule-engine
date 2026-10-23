# Dev tool, not shipped: regenerates seen-before-carried-out-parity.json, the
# engine's side of the counterparty.seen_before/3.0.0 differential
# (capsule-engine #121, asg/everyday/0.3.4). Run from capsule-engine checked
# out read-only at the commit named in the output:
#   cd <capsule-engine@SHA> && <venv>/bin/python <this file> <SHA> > seen-before-carried-out-parity.json
"""Two halves.

fold: the cases of tests/test_counterparty_seen_before_carried_out.py, each a
list of records, counted by every vendored fold that could read them:
counterparty.seen_before/2.0.0 and /3.0.0 per target, spend.weekly/1.0.0 and
/3.0.0 per operator. A carried_out record counts under seen_before/3.0.0 and
nowhere else; an accepted one under both seen_before folds; a dry run, a hold
and a refusal under none.

chain: the real two-deal bundle (tests/fixtures/real-two-deal), deal 1's
records changed as tests/test_real_two_deal_bundle.py changes them (and two
more: the act approved by the agent's own card, and by the user), replayed
under the catalog pack. Per case: deal 1's records as replayed (each capsule
with its disclosed agent_input, in order), and deal 2's check as the replay
decides it: counterparty_seen_before's result and prior_count, r02 and r06,
and the outcome. The Go side builds deal 2's live envelope from the same
records as capsulectl would, and compares.
"""
from __future__ import annotations

import json
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

from agent_action_capsule import json_digest

sys.path.insert(0, ".")
import capsule_engine  # noqa: E402
from capsule_engine.folds.engine import evaluate_one  # noqa: E402
from capsule_engine.folds.loader import load_definition_file as load_fold  # noqa: E402
from capsule_engine.guards.checks import RUNNABLE_CHECKS  # noqa: E402
from capsule_engine.packs import install_pack, load_pack_dir  # noqa: E402
from capsule_engine.packs.obligation_results import obligation_results  # noqa: E402
from capsule_engine.report.replay import load_disclosed, load_records, load_withheld, replay  # noqa: E402

FOLDS = Path("capsule_engine/folds/catalog_defs")
FIXTURE = Path("tests/fixtures/real-two-deal")
OWN = (FIXTURE / "deal-1.bundle.json", FIXTURE / "deal-2.bundle.json")
PACK = load_pack_dir(Path(capsule_engine.__file__).parent / "packs" / "catalog" / "everyday")
TARGET = "merchant/ref-1"
OPERATOR = "household-a"
AT = "2026-10-09T12:00:00Z"
AS_OF = "2026-10-09T12:00:01Z"
SEEN = {"counterparty.seen_before/2.0.0": "counterparty.seen_before.v2.yaml",
        "counterparty.seen_before/3.0.0": "counterparty.seen_before.v3.yaml"}
SPEND = {"spend.weekly/1.0.0": "spend.weekly.yaml", "spend.weekly/3.0.0": "spend.weekly.v3.yaml"}
R02, R06 = "r02-ordinary-purchase", "r06-new-merchant"


def _record(decision: str, *, dry_run: bool = False) -> dict:
    checkpoint = {"dry_run": True} if dry_run else {}
    return {"operator": OPERATOR, "timestamp": AT, "disposition": {"decision": decision},
            "asg_payload": {"target": TARGET, "checkpoint": checkpoint, "amount_minor": 2_000}}


FOLD_CASES = {
    "carried-out": [_record("carried_out")],
    "accepted": [_record("accept")],
    "accepted-dry-run": [_record("accept", dry_run=True)],
    "carried-out-dry-run": [_record("carried_out", dry_run=True)],
    "needs-input": [_record("needs_input")],
    "rejected": [_record("reject")],
    "deny": [_record("deny")],
    "mixed": [_record("accept"), _record("accept", dry_run=True), _record("needs_input"), _record("deny"),
              _record("carried_out")],
}


def _counts(records: list[dict]) -> dict[str, int]:
    out = {}
    for fold_id, name in SEEN.items():
        out[fold_id] = evaluate_one(load_fold(FOLDS / name), records, key_value=TARGET).result or 0
    for fold_id, name in SPEND.items():
        out[fold_id] = evaluate_one(load_fold(FOLDS / name), records, key_value=OPERATOR, as_of=AS_OF).result or 0
    return out


Change = Callable[[dict], "dict | None"]


def _with_body(**fields: object) -> Change:
    return lambda record: {**record, "body": {**record["body"], **fields}}


def _deal_1_changed(changes: dict[str, Change]) -> tuple[list[dict], dict[str, dict]]:
    """tests/test_real_two_deal_bundle.py _deal_1_changed, returning the
    records and disclosures instead of replaying them."""
    disclosed = dict(load_disclosed(OWN))
    deal_1 = {r["capsule_id"] for r in json.loads(OWN[0].read_text())["records"]}
    moved: dict[str, str] = {}
    records = []
    for capsule in load_records(OWN):
        capsule_id = capsule["capsule_id"]
        sealed = disclosed.get(capsule_id)
        if sealed is None or "x-deal-v0" not in sealed or capsule_id not in deal_1:
            records.append(capsule)
            continue
        text = json.dumps(sealed)
        for old, new in moved.items():
            text = text.replace(old, new)
        changed = json.loads(text)
        change = changes.get(changed["x-deal-v0"]["record_type"])
        if change is not None:
            changed = change(changed)
        if changed is None:
            disclosed.pop(capsule_id)
            continue
        if changed != sealed:
            moved[json_digest(sealed)] = json_digest(changed)
            capsule = {**capsule, "model_attestation": {"compute_attestation": {"agent_input_digest": json_digest(changed)}}}
            disclosed[capsule_id] = changed
        records.append(capsule)
    return records, disclosed


def _unbound(records: list[dict], disclosed: dict[str, dict]) -> tuple[list[dict], dict[str, dict]]:
    """The action step's disclosure changed under its capsule, which still
    seals the old one (test_an_action_step_its_capsule_did_not_seal_carries_nothing_out)."""
    disclosed = dict(disclosed)
    for r in records:
        shown = disclosed.get(r["capsule_id"])
        if shown is not None and shown.get("x-deal-v0", {}).get("record_type") == "action":
            disclosed[r["capsule_id"]] = {**shown, "note": "not what the capsule sealed"}
    return records, disclosed


CHAIN_CASES: dict[str, Callable[[], tuple[list[dict], dict[str, dict]]]] = {
    "unchanged": lambda: (list(load_records(OWN)), dict(load_disclosed(OWN))),
    "rewritten-unchanged": lambda: _deal_1_changed({}),
    "approval-declines": lambda: _deal_1_changed({"approval": _with_body(choice="decline", proceed=False)}),
    "never-executed": lambda: _deal_1_changed({"action": lambda record: None}),
    "guard-refused": lambda: _deal_1_changed({"check": _with_body(action_class="money.unlisted")}),
    "action-unbound": lambda: _unbound(list(load_records(OWN)), dict(load_disclosed(OWN))),
    "approved-by-agent-card": lambda: _deal_1_changed({"approval": _with_body(approver="agent_card")}),
    "approved-by-user": lambda: _deal_1_changed({"approval": _with_body(approver="user")}),
}


def _replay(records: list[dict], disclosed: dict[str, dict]):
    with tempfile.TemporaryDirectory() as tmp:
        installed = install_pack(PACK, project_dir=Path(tmp) / "replay-project", mode="observe")
        resolved = installed.resolved
        return replay(records, caps_fold=resolved.caps_fold(), caps_minor=resolved.caps_minor(),
                      per_action_minor=resolved.per_action_minor(), per_action_reads=resolved.per_action_reads(),
                      manifest_digest=resolved.manifest_digest, disclosed=disclosed, withheld=load_withheld(OWN),
                      wickets=resolved.configured_wickets(RUNNABLE_CHECKS), pack=installed.pack)


def _chain_case(build) -> dict:
    records, disclosed = build()
    result = _replay(records, disclosed)
    deal_1 = {r["capsule_id"] for r in json.loads(OWN[0].read_text())["records"]}
    deal_2_checks = [r["capsule_id"] for r in json.loads(OWN[1].read_text())["records"]
                     if disclosed.get(r["capsule_id"], {}).get("x-deal-v0", {}).get("record_type") == "check"]
    (sourced,) = [s for s in result.decisions if s.record["capsule_id"] in deal_2_checks]
    (seen,) = [c for c in sourced.decision.constraints if c.id == "counterparty_seen_before"]
    rules = {r.obligation_id: r.result for r in obligation_results(PACK, sourced.decision.constraints)}
    deal_1_check = [s for s in result.decisions if s.record["capsule_id"] in deal_1]
    return {
        "deal_1": [{**r, "agent_input": disclosed[r["capsule_id"]]} if r["capsule_id"] in disclosed else r
                   for r in records if r["capsule_id"] in deal_1],
        "deal_1_check_outcome": deal_1_check[0].decision.outcome if deal_1_check else None,
        "deal_2_check": sourced.record["capsule_id"],
        "seen_before_result": seen.result,
        "prior_count": seen.evidence["prior_count"],
        "r02": rules[R02],
        "r06": rules[R06],
        "outcome": sourced.decision.outcome,
    }


def main(sha: str) -> None:
    out = {
        "capsule_engine_commit": sha,
        "source": "go/guard/testdata/parity/generate_seen_before_carried_out.py",
        "pack": {"pack_id": PACK.pack_id, "definition_digest": PACK.definition_digest()},
        "fold_digests": {fid: load_fold(FOLDS / name).definition_digest() for fid, name in {**SEEN, **SPEND}.items()},
        "fold": {name: {"records": records, "counts": _counts(records)} for name, records in FOLD_CASES.items()},
        "chain": {name: _chain_case(build) for name, build in CHAIN_CASES.items()},
    }
    json.dump(out, sys.stdout, indent=1, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main(sys.argv[1])
