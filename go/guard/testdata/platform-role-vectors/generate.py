# Dev tool, not shipped: regenerates engine-results.json, capsule-engine's
# decisions on disclosures to a platform recipient (capsule-engine #173:
# recipient_role/1.1.0, seller.recipient_role/1.1.0, action_class_gate/1.1.0,
# seller.release_on_acceptance/1.1.0) under both catalog packs, for the Go
# port's differential (platform_role_test.go). Run from capsule-engine checked
# out read-only at the commit:
#
#   cd <capsule-engine@SHA> && <venv>/bin/python -P <this file> <SHA> > engine-results.json
#
# Two kinds of scenario per pack, each on a fresh ledger:
#   action: an Action built directly (the normalized --action shape);
#   record: a capsulectl deal check record (a capsule header with its
#           disclosed agent_input, bound by agent_input_digest), read by the
#           engine's own bridge (report/replay.py action_for_record), so the
#           body's disclosing is bridged as disclosed_classes as the engine
#           bridges it.
# For each, the outcome, every constraint's result, evidence digest and
# reason, and every obligation's result. No model is loaded.
import dataclasses
import json
import shutil
import sys
import tempfile
from pathlib import Path

from agent_action_capsule import json_digest
from capsule_ledger.ledger import LedgerStore

sys.path.insert(0, ".")
from tests.test_pack_everyday_acceptance import _signer  # noqa: E402
from capsule_engine.guards import Action  # noqa: E402
from capsule_engine.packs import build_engine, install_pack, load_pack_dir  # noqa: E402
from capsule_engine.packs.obligation_results import obligation_results  # noqa: E402
from capsule_engine.report.replay import action_for_record  # noqa: E402

CATALOG = Path("capsule_engine/packs/catalog")
TAXONOMY = json.loads(Path("capsule_engine/guards/action_taxonomy.json").read_text())["taxonomy_version"]
NORMALIZED = ("verb", "operator", "developer", "action_class", "action_id", "action_type", "timestamp",
              "amount_minor", "currency", "target", "taxonomy_version", "recipient_role", "disclosed_classes")


def share(name, minute, role=None, classes=None, cls="disclosure.personal"):
    """A share of personal data to a recipient of role, giving classes."""
    return Action(verb="share", operator=f"op-{name}", developer="assistant-a@v1", action_class=cls,
                  action_id=f"share/platform-{name}", timestamp=f"2026-10-21T10:{minute:02d}:00Z",
                  target=f"recipient/{name}", taxonomy_version=TAXONOMY, recipient_role=role,
                  disclosed_classes=classes)


def deal_check(name, minute, body):
    """A deal check record of a share: its header and the agent_input it seals."""
    full = {"action": "share_contact", "action_class": "disclosure.personal", "taxonomy_version": TAXONOMY}
    full.update(body)
    agent_input = {"body": full, "x-deal-v0": {"record_type": "check", "seq": 3, "deal_id": f"deal-{name}"}}
    return {"operator": f"deal-{name}", "developer": "capsulectl", "action_id": f"deal-{name}/3",
            "action_type": "decide", "timestamp": f"2026-10-21T11:{minute:02d}:00Z",
            "model_attestation": {"compute_attestation": {"agent_input_digest": json_digest(agent_input)}},
            "agent_input": agent_input}


def scenarios(allowed):
    """The scenarios for a pack whose counterparty role is allowed
    (fulfilling_merchant for everyday, buyer for seller)."""
    actions = [
        ("platform-email", share("a1", 1, "platform", ("email",))),
        ("platform-phone", share("a2", 2, "platform", ("phone",))),
        ("platform-home-address", share("a3", 3, "platform", ("home_address",))),
        ("platform-email-and-phone", share("a4", 4, "platform", ("email", "phone"))),
        ("platform-address-email-name", share("a5", 5, "platform", ("address", "email", "name"))),
        ("platform-no-class", share("a6", 6, "platform")),
        ("counterparty-phone", share("a7", 7, allowed, ("phone",))),
        ("counterparty-no-class", share("a8", 8, allowed)),
        ("self-email", share("a9", 9, "self", ("email",))),
        ("third-party-email", share("a10", 10, "third_party", ("email",))),
        ("unknown-role-email", share("a11", 11, "courier", ("email",))),
        ("unknown-role-with-a-quote", share("a12", 12, "o'brien", ("email",))),
        ("no-role-email", share("a13", 13, None, ("email",))),
        ("platform-email-on-a-message", share("a14", 14, "platform", ("email",), cls="communication.send")),
    ]
    records = [
        ("record-platform-email", deal_check("r1", 1, {"recipient_role": "platform", "disclosing": ["email"]})),
        ("record-platform-email-twice", deal_check("r2", 2, {"recipient_role": "platform", "disclosing": ["email", "email"]})),
        ("record-platform-phone-email", deal_check("r3", 3, {"recipient_role": "platform", "disclosing": ["phone", "email"]})),
        ("record-platform-unknown-class", deal_check("r4", 4, {"recipient_role": "platform", "disclosing": ["email", "ssn"]})),
        ("record-platform-empty-list", deal_check("r5", 5, {"recipient_role": "platform", "disclosing": []})),
        ("record-platform-string", deal_check("r6", 6, {"recipient_role": "platform", "disclosing": "email"})),
        ("record-platform-number", deal_check("r7", 7, {"recipient_role": "platform", "disclosing": [1]})),
        ("record-platform-no-disclosing", deal_check("r8", 8, {"recipient_role": "platform"})),
        ("record-third-party-email", deal_check("r9", 9, {"recipient_role": "third_party", "disclosing": ["email"]})),
        ("record-counterparty-email", deal_check("r10", 10, {"recipient_role": allowed, "disclosing": ["email"]})),
        ("record-unknown-role", deal_check("r11", 11, {"recipient_role": "marketplace", "disclosing": ["email"]})),
        ("record-no-role", deal_check("r12", 12, {"disclosing": ["email"]})),
    ]
    return actions, records


def decided(definition, d):
    return {
        "outcome": d.outcome,
        "constraints": [{"id": c.id, "result": c.result, "reason": c.reason,
                         "evidence_digest": json_digest(c.evidence) if c.evidence is not None else None}
                        for c in d.constraints],
        "obligations": [{"id": r.obligation_id, "result": r.result}
                        for r in obligation_results(definition, d.constraints)],
    }


def run(pack_name, allowed, sha, tmp):
    pack_dir = Path(tmp) / pack_name
    shutil.copytree(CATALOG / pack_name, pack_dir)
    definition = load_pack_dir(pack_dir)
    installed = install_pack(definition, project_dir=Path(tmp) / f"project-{pack_name}", mode="observe")
    signer = _signer()
    out = {"pack_id": definition.pack_id, "definition_digest": definition.definition_digest(), "scenarios": [],
           "records": []}
    actions, records = scenarios(allowed)

    def check(i, kind, action):
        store = LedgerStore(Path(tmp) / f"ledger-{pack_name}-{kind}-{i}")
        try:
            engine = build_engine(installed, ledger=store, signer_provider=lambda: signer)
            return engine.check(action)
        finally:
            store.close()

    for i, (name, action) in enumerate(actions):
        fields = dataclasses.asdict(action)
        row = {"scenario": name, "action": {k: (list(fields[k]) if isinstance(fields[k], tuple) else fields[k])
                                            for k in NORMALIZED if fields[k] is not None}}
        row.update(decided(definition, check(i, "action", action)))
        out["scenarios"].append(row)
    for i, (name, record) in enumerate(records):
        action = action_for_record(record, record["agent_input"])
        row = {"scenario": name, "record": record,
               "disclosed_classes": list(action.disclosed_classes) if action.disclosed_classes is not None else None}
        row.update(decided(definition, check(i, "record", action)))
        out["records"].append(row)
    return out


def main(sha):
    out = {"capsule_engine_commit": sha, "source": "internal/guard/testdata/platform-role-vectors/generate.py",
           "packs": {}}
    with tempfile.TemporaryDirectory() as tmp:
        out["packs"]["everyday"] = run("everyday", "fulfilling_merchant", sha, tmp)
        out["packs"]["seller"] = run("seller", "buyer", sha, tmp)
    json.dump(out, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main(sys.argv[1])
