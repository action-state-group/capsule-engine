# Dev tool, not shipped: regenerates recipient-seen-before-parity.json, Python
# reference verdicts for recipient_seen_before/1.0.0 over
# counterparty.seen_before/2.0.0 (rule r13), which the everyday decision table
# exercises only once each way. It covers the check's four outcomes:
#   - out of scope: a class the wicket does not list (a deal's money.purchase);
#   - in scope with no target: n/a, missing_field target;
#   - a first-time recipient: fail;
#   - a repeat recipient: pass, for both listed classes;
# and what the fold counts: an accepted earlier action of ANY class to the
# target makes it known; a dry run, another operator's action and a refused
# message do not.
#
# Run it like generate_python_parity.py, from capsule-engine checked out
# read-only at the commit named in the output:
#
#   cd <capsule-engine@SHA> && <venv>/bin/python <this file> <SHA> > recipient-seen-before-parity.json
#
# Same engine, same pack, same install record as the acceptance replay. No
# model is loaded. Each action has its own developer, so dedupe (keyed on
# operator, developer, action_type, verb and target) never matches; the fold
# keys on the operator alone.
import dataclasses
import json
import sys
import tempfile
from pathlib import Path

from agent_action_capsule import json_digest
from capsule_ledger.ledger import LedgerStore

sys.path.insert(0, ".")
from tests.test_pack_everyday_acceptance import PACK_DIR, _signer  # noqa: E402
from capsule_engine.guards import Action  # noqa: E402
from capsule_engine.packs import build_engine, install_pack, load_pack_dir, record_pack_activation  # noqa: E402

NORMALIZED = ("verb", "operator", "developer", "action_class", "action_id", "action_type", "timestamp",
              "amount_minor", "currency", "target", "cited_mandate_capsule_id", "equivalence_key", "rail",
              "counterparty_account_ref", "outgoing_content", "recurrence")

OP = "household-recipient"


def act(name, minute, action_class, target, verb="send_message", operator=OP):
    return Action(verb=verb, operator=operator, developer=f"assistant-{name}@v1", action_class=action_class,
                  action_id=f"{verb}/recipient-{name}", timestamp=f"2026-08-12T09:{minute:02d}:00Z",
                  target=target, outgoing_content="Running ten minutes late.")


def scenarios():
    # (name, action, dry_run)
    return [
        # Earlier actions that are not messages: the fold has no class filter.
        ("prior-query-landlord", act("prior-query-landlord", 1, "info.query", "contact/landlord", verb="lookup"), False),
        ("prior-query-plumber-dry-run", act("prior-query-plumber", 2, "info.query", "contact/plumber", verb="lookup"), True),
        ("prior-query-other-operator", act("prior-query-other", 3, "info.query", "contact/neighbour", verb="lookup",
                                           operator="household-other"), False),
        # Out of scope: a deal's class.
        ("out-of-scope-purchase", act("purchase", 4, "money.purchase", "contact/landlord", verb="buy"), False),
        # In scope, no target.
        ("message-no-target", act("no-target", 5, "comms.external", None), False),
        # First time: nothing accepted to this recipient.
        ("message-first-time", act("first-time", 6, "comms.external", "contact/stranger"), False),
        # The refused first message does not make the recipient known.
        ("message-after-refused", act("after-refused", 7, "comms.external", "contact/stranger"), False),
        # Repeat: the accepted info.query to the landlord counts.
        ("message-repeat", act("repeat", 8, "comms.external", "contact/landlord"), False),
        ("message-repeat-communication-send", act("repeat-send", 9, "communication.send", "contact/landlord"), False),
        # A dry run does not count (counterparty.seen_before/2.0.0).
        ("message-after-dry-run", act("after-dry-run", 10, "comms.external", "contact/plumber"), False),
        # Another operator's accepted action does not count.
        ("message-other-operators-recipient", act("other-operator", 11, "comms.external", "contact/neighbour"), False),
    ]


def main(sha):
    out = {"capsule_engine_commit": sha, "source": "go/guard/testdata/parity/generate_recipient_seen_before.py",
           "scenarios": []}
    with tempfile.TemporaryDirectory() as tmp:
        store = LedgerStore(Path(tmp) / "ledger")
        try:
            installed = install_pack(load_pack_dir(PACK_DIR), project_dir=Path(tmp) / "project", mode="observe")
            signer = _signer()
            engine = build_engine(installed, ledger=store, signer_provider=lambda: signer)
            record_pack_activation(installed, ledger=store, operator="household-fixture", developer="capsule-init-tool",
                                   signer=signer, timestamp="2026-08-10T10:00:00Z",
                                   action_id="policy_manifest_activated/everyday-fixture-install")
            for name, action, dry_run in scenarios():
                decision = engine.check(action, dry_run=dry_run)
                fields = dataclasses.asdict(action)
                out["scenarios"].append({
                    "scenario": name,
                    "action": {k: fields[k] for k in NORMALIZED if fields[k] is not None},
                    "dry_run": dry_run,
                    "outcome": decision.outcome,
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
