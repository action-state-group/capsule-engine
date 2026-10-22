# Dev tool, not shipped: regenerates rollup-parity.json, Python reference
# DECISIONS for capsulectl deal check records that leave out the fields a
# real deal states only when the user states them (upfront_amount_minor,
# the offer count, the channel, task_authority_ref). The engine reads each
# such gap as n/a, missing field, and still decides; the Go port's verdict
# must be that decision. Run from capsule-engine checked out read-only at
# the pack's commit:
#
#   cd <capsule-engine@SHA> && <venv>/bin/python <this file> <SHA> > rollup-parity.json
#
# Each scenario runs on a fresh ledger holding one accepted earlier action
# addressed to the record's payee (an info.query, so the payee is known to
# counterparty_seen_before and nothing else is), then the record, read by
# the engine's own bridge (report/replay.py action_for_record), with the
# sealed task-authority record supplied beside it when the record cites one.
# Both decisions are recorded in full. No model is loaded.
import dataclasses
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

from agent_action_capsule import json_digest
from capsule_ledger.ledger import LedgerStore

sys.path.insert(0, ".")
from tests.test_pack_everyday_acceptance import PACK_DIR, _signer  # noqa: E402
from capsule_engine.guards import Action  # noqa: E402
from capsule_engine.packs import build_engine, install_pack, load_pack_dir  # noqa: E402
from capsule_engine.report.replay import action_for_record  # noqa: E402

NORMALIZED = ("verb", "operator", "developer", "action_class", "action_id", "action_type", "timestamp",
              "amount_minor", "currency", "target")
TAXONOMY = json.loads(Path("capsule_engine/guards/action_taxonomy.json").read_text())["taxonomy_version"]
FP_ALG = "hmac-sha256-deal-key"
GOLDEN = json.loads((Path(__file__).parent / "task-authority-golden.json").read_text())


def jcs_basis(keys):
    """The pinned field list's basis: SHA-256 of the JCS bytes of the list."""
    return hashlib.sha256(json.dumps(keys, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


MATERIAL = ["terms.item", "terms.quantity", "terms.price_minor", "terms.deposit_minor", "terms.currency", "terms.when",
            "terms.place", "terms.conditions", "recourse.rail", "recourse.refundable", "who.payee"]
OFFER = ["terms.item", "terms.quantity", "terms.price_minor", "terms.deposit_minor", "terms.conditions",
         "recourse.refundable"]

# Everything a deal check can state about an ordinary card purchase.
STATED = {"action": "pay", "action_class": "money.purchase", "taxonomy_version": TAXONOMY, "amount_minor": 454,
          "spend_minor": 454, "currency": "USD", "recourse": {"rail": "card", "refundable": True},
          "upfront_amount_minor": 0, "channel": "web", "first_contact_channel": "web",
          "material_fields_changed": 0, "material_fields_basis": jcs_basis(MATERIAL),
          "offer_fields_changed": 0, "offer_fields_basis": jcs_basis(OFFER),
          "task_authority_ref": {"type": "record", "digest_alg": "SHA-256", "digest": json_digest(GOLDEN)}}
OFFER_KEYS = ("offer_fields_changed", "offer_fields_basis")
CHANNEL_KEYS = ("channel", "first_contact_channel")


def payee(name):
    return hashlib.sha256(name.encode()).hexdigest()


def deal_check(name, minute, drop=(), **over):
    """A deal check record as capsulectl seals it: header, the agent_input
    its agent_input_digest binds, and the x-deal-v0 counterparty block."""
    body = {k: v for k, v in {**STATED, **over}.items() if k not in drop}
    agent_input = {"body": body, "x-deal-v0": {"record_type": "check", "seq": 3, "deal_id": f"deal-{name}",
                                               "counterparty": {"fp_alg": FP_ALG, "ids": {"payee": payee(name)}}}}
    return {"operator": "household-rollup", "developer": f"capsulectl-{name}", "action_id": f"deal-{name}/3",
            "action_type": "decide", "timestamp": f"2026-10-08T11:{minute:02d}:00Z",
            "model_attestation": {"compute_attestation": {"agent_input_digest": json_digest(agent_input)}},
            "agent_input": agent_input}


def seed(record, minute):
    """An accepted earlier action to the record's payee (its sealed
    counterparty block, in the bridge's payee-fp form)."""
    cp = record["agent_input"]["x-deal-v0"]["counterparty"]
    name = record["developer"]
    return Action(verb="lookup", operator=record["operator"], developer=f"assistant-{name}", action_class="info.query",
                  action_id=f"lookup/rollup-{name}", timestamp=f"2026-10-08T10:{minute:02d}:00Z",
                  target=f"payee-fp:{cp['fp_alg']}:{cp['ids']['payee']}")


# (name, record, seeded, task-authority record supplied)
SCENARIOS = [
    ("all-stated", deal_check("a1", 1), True, GOLDEN),
    ("no-deposit", deal_check("a2", 2, drop=("upfront_amount_minor",)), True, GOLDEN),
    ("no-offer-field", deal_check("a3", 3, drop=OFFER_KEYS), True, GOLDEN),
    ("no-channel", deal_check("a4", 4, drop=CHANNEL_KEYS), True, GOLDEN),
    ("no-task-authority-untyped", deal_check("a5", 5, drop=("task_authority_ref",)), True, None),
    # As capsulectl emits an ordinary purchase where the user stated none of the four.
    ("as-capsulectl-emits", deal_check("a6", 6, drop=("upfront_amount_minor", "task_authority_ref") + OFFER_KEYS
                                       + CHANNEL_KEYS), True, None),
    # Controls: the engine does not allow these whatever is stated (from
    # taxonomy 3, where money.purchase has an approver, it asks).
    ("unseen-payee", deal_check("a7", 7), False, GOLDEN),
    ("over-limit", deal_check("a8", 8, amount_minor=3000, spend_minor=3000), True, None),
    # A spend that differs from the amount: the engine's bridge takes
    # spend_minor as the action's amount (0 when the money moved in), and
    # decides on it.
    ("spend-under-amount", deal_check("a9", 9, amount_minor=3000, spend_minor=454), True, None),
    ("spend-over-amount", deal_check("a10", 10, amount_minor=454, spend_minor=3000), True, None),
    ("money-in", deal_check("a11", 11, amount_minor=3000, spend_minor=3000, direction="in"), True, None),
]


def constraints(decision):
    return [{"id": c.id, "result": c.result,
             "evidence_digest": json_digest(c.evidence) if c.evidence is not None else None}
            for c in decision.constraints]


def main(sha):
    out = {"capsule_engine_commit": sha, "source": "go/guard/testdata/parity/generate_rollup_parity.py",
           "scenarios": []}
    with tempfile.TemporaryDirectory() as tmp:
        pack_dir = Path(tmp) / "pack"
        shutil.copytree(PACK_DIR, pack_dir)
        signer = _signer()
        installed = install_pack(load_pack_dir(pack_dir), project_dir=Path(tmp) / "project", mode="observe")
        for i, (name, record, seeded, authority) in enumerate(SCENARIOS):
            store = LedgerStore(Path(tmp) / f"ledger-{i}")
            try:
                engine = build_engine(installed, ledger=store, signer_provider=lambda: signer)
                row = {"scenario": name, "record": record}
                if seeded:
                    s = seed(record, i)
                    sd = engine.check(s)
                    fields = dataclasses.asdict(s)
                    row["seed"] = {"action": {k: fields[k] for k in NORMALIZED if fields[k] is not None},
                                   "outcome": sd.outcome, "constraints": constraints(sd)}
                d = engine.check(action_for_record(record, record["agent_input"]), task_authority_record=authority)
                row.update(outcome=d.outcome, constraints=constraints(d))
                if authority is not None:
                    row["task_authority_record"] = authority
                out["scenarios"].append(row)
            finally:
                store.close()
    json.dump(out, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main(sys.argv[1])
