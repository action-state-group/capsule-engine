# Dev tool, not shipped: regenerates supplementary-parity.json, Python
# reference verdicts for what the everyday decision table does not exercise:
# the caps window limit, the operator key of the spend fold (two developers
# of one operator share one total), spend pooled across money classes,
# a booking.create over the per-action limit, and a booking.cancel with
# an amount and no chain link. Under caps/5.0.0 over spend.weekly/3.0.0 (the
# shipped 0.2.1 from capsule-engine #93's merge; caps/4.0.0's class set) that cancel is out of the
# cap's scope and, unlinked, neither adds to nor takes from the total.
#
# The decisions are real, not dry runs: spend.weekly/3.0.0 leaves dry runs
# out of the total, so a dry-run replay would never fill the window. Run it like generate_python_parity.py, from
# capsule-engine checked out read-only at the commit named in the output:
#
#   cd <capsule-engine@SHA> && <venv>/bin/python <this file> <SHA> > supplementary-parity.json
#
# Same engine, same pack, same install record as the acceptance replay. No
# model is loaded.
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
              "counterparty_account_ref", "outgoing_content", "recurrence",
              # the inputs of each scalar check the Go port runs, as it ports them
              "refundable", "upfront_amount_minor", "recipient_role", "task_authority_ref")


def act(name, minute, operator, developer, action_class, amount, verb="make_payment"):
    return Action(verb=verb, operator=operator, developer=developer, action_class=action_class,
                  action_id=f"{verb}/supplementary-{name}", timestamp=f"2026-08-11T09:{minute:02d}:00Z",
                  amount_minor=amount, currency="USD", target=f"merchant/{name}", rail="card",
                  counterparty_account_ref=f"acct-ref-{name}", recurrence="one_time")


def scenarios():
    w = "household-window"
    c = "household-cancel"
    return [
        # One operator, two developers: four at the per-action limit fill the
        # 10,000 window exactly (<= passes), then a second developer's 100
        # goes over: the total is the operator's, not the developer's.
        ("window-fill-1", act("window-fill-1", 1, w, "assistant-a@v1", "money.transfer", 2500)),
        ("window-fill-2", act("window-fill-2", 2, w, "assistant-a@v1", "money.transfer", 2500)),
        ("window-fill-3", act("window-fill-3", 3, w, "assistant-a@v1", "money.transfer", 2500)),
        ("window-fill-4-at-limit", act("window-fill-4", 4, w, "assistant-a@v1", "money.transfer", 2500)),
        ("window-over-other-developer", act("window-over", 5, w, "assistant-b@v2", "money.transfer", 100)),
        # The same operator's purchase is pooled with its transfers.
        ("window-over-pooled-purchase", act("window-purchase", 6, w, "assistant-a@v1", "money.purchase", 100, verb="buy")),
        # A 558.80 booking against the 25.00 per-action limit.
        ("booking-over-per-action", act("booking-a", 7, "household-booking", "assistant-a@v1", "booking.create", 55880, verb="create_booking")),
        # A cancellation that carries an amount and cites no charge.
        ("cancel-fill-1", act("cancel-fill-1", 8, c, "assistant-a@v1", "money.purchase", 2500, verb="buy")),
        ("cancel-fill-2", act("cancel-fill-2", 9, c, "assistant-a@v1", "money.purchase", 2500, verb="buy")),
        ("cancel-fill-3", act("cancel-fill-3", 10, c, "assistant-a@v1", "money.purchase", 2500, verb="buy")),
        ("cancel-with-amount", act("cancel-booking", 11, c, "assistant-a@v1", "booking.cancel", 2500, verb="cancel_booking")),
        ("after-unlinked-cancel", act("after-cancel", 12, c, "assistant-a@v1", "money.purchase", 100, verb="buy")),
    ]


def main(sha):
    out = {"capsule_engine_commit": sha, "source": "go/guard/testdata/parity/generate_supplementary.py", "scenarios": []}
    with tempfile.TemporaryDirectory() as tmp:
        store = LedgerStore(Path(tmp) / "ledger")
        try:
            installed = install_pack(load_pack_dir(PACK_DIR), project_dir=Path(tmp) / "project", mode="observe")
            signer = _signer()
            engine = build_engine(installed, ledger=store, signer_provider=lambda: signer)
            record_pack_activation(installed, ledger=store, operator="household-fixture", developer="capsule-init-tool",
                                   signer=signer, timestamp="2026-08-10T10:00:00Z",
                                   action_id="policy_manifest_activated/everyday-fixture-install")
            for name, action in scenarios():
                decision = engine.check(action)
                fields = dataclasses.asdict(action)
                caps = [c for c in decision.constraints if c.id == "caps"][0]
                out["scenarios"].append({
                    "scenario": name,
                    "action": {k: fields[k] for k in NORMALIZED if fields[k] is not None},
                    "outcome": decision.outcome,
                    "constraints": [
                        {"id": c.id, "result": c.result,
                         "evidence_digest": json_digest(c.evidence) if c.evidence is not None else None}
                        for c in decision.constraints
                    ],
                    "caps_evidence": caps.evidence if caps.result in ("pass", "fail") else None,
                })
        finally:
            store.close()
    json.dump(out, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main(sys.argv[1])
