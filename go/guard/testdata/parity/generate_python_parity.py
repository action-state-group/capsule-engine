# Dev tool, not shipped: regenerates python-parity.json, the Python reference
# verdicts the Go port is compared against (leg 3). Run it with capsule-engine
# checked out read-only at the commit named in the output (no edits to it):
#
#   cd <capsule-engine@SHA> && <venv>/bin/python <this file> <SHA> > python-parity.json
#
# It replays the everyday acceptance scenarios exactly as
# tests/test_pack_everyday_acceptance.py does (same _scenarios(), same pack,
# same engine), and records each scenario's input Action and the engine's
# per-constraint result and evidence digest. No model is loaded.
import dataclasses
import json
import sys
import tempfile
from pathlib import Path

from agent_action_capsule import json_digest
try:  # capsule-engine #170 on: cll owns the ledger (capsule-ledger was archived)
    from cll.ledger import LedgerStore
except ImportError:
    from capsule_ledger.ledger import LedgerStore

sys.path.insert(0, ".")
from tests.test_pack_everyday_acceptance import PACK_DIR, _scenarios, _signer  # noqa: E402

try:  # 0.3.1 on (capsule-engine 365eb92): two scenarios are real runs, two carry a task-authority record
    from tests.test_pack_everyday_acceptance import REAL_RUN, TASK_RECORDS  # noqa: E402
except ImportError:
    REAL_RUN, TASK_RECORDS = frozenset(), {}
from capsule_engine.packs import build_engine, install_pack, load_pack_dir, record_pack_activation  # noqa: E402

try:  # 0.3.2 on (capsule-engine a5e9e30): each rule's own result, per action_class_gate selector
    from capsule_engine.packs import obligation_results  # noqa: E402
except ImportError:
    obligation_results = None

NORMALIZED = ("verb", "operator", "developer", "action_class", "action_id", "action_type", "timestamp",
              "amount_minor", "currency", "target", "cited_mandate_capsule_id", "equivalence_key", "rail",
              "counterparty_account_ref", "outgoing_content", "recurrence",
              # The change scalars each ported check reads (one per port).
              "channel", "first_contact_channel", "material_fields_changed", "material_fields_basis",
              "offer_fields_changed", "offer_fields_basis", "refundable", "upfront_amount_minor", "recipient_role", "task_authority_ref",
              # The classes a disclosure gives (capsule-engine #173).
              "disclosed_classes",
              # What the producer reports as told before (action_class_gate/1.2.0, 0c93a58).
              "repeat_disclosures", "recipient_told_before")


def main(sha: str) -> None:
    out = {"capsule_engine_commit": sha, "source": "tests/test_pack_everyday_acceptance.py::_scenarios", "scenarios": []}
    with tempfile.TemporaryDirectory() as tmp:
        store = LedgerStore(Path(tmp) / "ledger")
        try:
            definition = load_pack_dir(PACK_DIR)
            installed = install_pack(definition, project_dir=Path(tmp) / "project", mode="observe")
            signer = _signer()
            engine = build_engine(installed, ledger=store, signer_provider=lambda: signer)
            record_pack_activation(installed, ledger=store, operator="household-fixture", developer="capsule-init-tool",
                                   signer=signer, timestamp="2026-08-10T10:00:00Z",
                                   action_id="policy_manifest_activated/everyday-fixture-install")
            for name, action, expected in _scenarios():
                kwargs = {"task_authority_record": TASK_RECORDS[name]} if name in TASK_RECORDS else {}
                decision = engine.check(action, dry_run=name not in REAL_RUN, **kwargs)
                fields = dataclasses.asdict(action)
                obligations = {} if obligation_results is None else {"obligations": [
                    {"id": r.obligation_id, "result": r.result} for r in obligation_results(definition, decision.constraints)]}
                if name in TASK_RECORDS:
                    # The record supplied beside the action, digested as is.
                    obligations["task_authority_record"] = TASK_RECORDS[name]
                out["scenarios"].append({
                    **obligations,
                    "scenario": name,
                    "action": {k: fields[k] for k in NORMALIZED if fields.get(k) is not None},
                    "outcome": decision.outcome,
                    "expected": expected,
                    "capsule_id": decision.capsule["capsule_id"],
                    "constraints": [
                        {"id": c.id, "result": c.result,
                         "evidence_digest": json_digest(c.evidence) if c.evidence is not None else None}
                        for c in decision.constraints
                    ],
                })
        finally:
            store.close()
    json.dump(out, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main(sys.argv[1])
