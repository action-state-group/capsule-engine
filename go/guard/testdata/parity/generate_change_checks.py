# Dev tool, not shipped: regenerates change-checks-parity.json, Python
# reference decisions for the checks that read a producer's change scalars:
# channel_change/1.0.0 (channel, first_contact_channel),
# material_fields_changed/1.0.0 (material_fields_changed,
# material_fields_basis) and offer_fields_changed/1.0.0
# (offer_fields_changed, offer_fields_basis). Run from
# capsule-engine checked out read-only at the commit named in the output:
#
#   cd <capsule-engine@SHA> && <venv>/bin/python <this file> <SHA> > change-checks-parity.json
#
# One configuration: capsule-engine's own everyday pack, built and installed
# as the acceptance test does, over one ledger. Each scenario's purchase is to
# its own merchant, so dedupe never matches and counterparty_seen_before
# fails each (a first-time merchant), in Python as in Go. No model is loaded.
import dataclasses
import json
import shutil
import sys
import tempfile
from pathlib import Path

import yaml

from agent_action_capsule import json_digest
from capsule_ledger.ledger import LedgerStore

sys.path.insert(0, ".")
from tests.test_pack_everyday_acceptance import PACK_DIR, _signer  # noqa: E402
from capsule_engine.guards import Action  # noqa: E402
from capsule_engine.guards.checks.field_change_count import fields_basis  # noqa: E402
from capsule_engine.packs import build_engine, install_pack, load_pack_dir  # noqa: E402

NORMALIZED = ("verb", "operator", "developer", "action_class", "action_id", "action_type", "timestamp",
              "amount_minor", "spend_authorized_minor", "currency", "target", "cited_mandate_capsule_id",
              "equivalence_key", "rail", "counterparty_account_ref", "outgoing_content", "recurrence",
              "channel", "first_contact_channel", "material_fields_changed", "material_fields_basis",
              "offer_fields_changed", "offer_fields_basis")


def act(name, minute, cls="money.purchase", **fields):
    return Action(verb="buy", operator="household-change", developer="assistant-a@v1", action_class=cls,
                  action_id=f"buy/change-{name}", timestamp=f"2026-10-08T09:{minute:02d}:00Z",
                  amount_minor=454, currency="USD", target=f"merchant/{name}", rail="card",
                  counterparty_account_ref=f"acct-ref-{name}", recurrence="one_time", **fields)


CHANNEL = [
    ("channel-same", act("c1", 1, channel="marketplace", first_contact_channel="marketplace")),
    ("channel-moved", act("c2", 2, channel="whatsapp", first_contact_channel="marketplace")),
    # A namespaced (unseeded) kind is compared the same way.
    ("channel-namespaced-same", act("c3", 3, channel="x.example/forum", first_contact_channel="x.example/forum")),
    ("channel-namespaced-moved", act("c4", 4, channel="x.example/forum", first_contact_channel="email")),
    ("channel-absent", act("c5", 5, first_contact_channel="email")),
    ("first-contact-absent", act("c6", 6, channel="email")),
    ("channel-both-absent", act("c7", 7)),
    # An empty kind is a value, not an absence, on a normalized action.
    ("channel-empty", act("c8", 8, channel="", first_contact_channel="web")),
    # Out of the wicket's classes: n/a, out of scope, whatever the channels.
    ("channel-out-of-scope", act("c9", 9, cls="info.query", channel="sms", first_contact_channel="web")),
    ("channel-transfer-moved", act("c10", 10, cls="money.transfer", channel="sms", first_contact_channel="phone_call")),
]


def counted(wicket):
    return yaml.safe_load(Path(f"capsule_engine/guards/wickets/catalog_defs/{wicket}.yaml").read_text())["config"]["counted_fields"]


MATERIAL_BASIS = fields_basis(counted("material_fields_changed"))
OFFER_BASIS = fields_basis(counted("offer_fields_changed"))

MATERIAL = [
    ("material-unchanged", act("m1", 21, material_fields_changed=0, material_fields_basis=MATERIAL_BASIS)),
    ("material-one-changed", act("m2", 22, material_fields_changed=1, material_fields_basis=MATERIAL_BASIS)),
    ("material-all-changed", act("m3", 23, cls="booking.create", material_fields_changed=11, material_fields_basis=MATERIAL_BASIS)),
    ("material-count-absent", act("m4", 24, material_fields_basis=MATERIAL_BASIS)),
    ("material-basis-absent", act("m5", 25, material_fields_changed=2)),
    # The offer list's basis is not the material list's: the count is not read.
    ("material-basis-other-list", act("m6", 26, material_fields_changed=0, material_fields_basis=OFFER_BASIS)),
    ("material-basis-upper-case", act("m7", 27, material_fields_changed=0, material_fields_basis=MATERIAL_BASIS.upper())),
    # The engine reads any integer; a negative count is within max_changed.
    ("material-count-negative", act("m8", 28, material_fields_changed=-1, material_fields_basis=MATERIAL_BASIS)),
    ("material-out-of-scope", act("m9", 29, cls="info.query", material_fields_changed=3, material_fields_basis=MATERIAL_BASIS)),
]

OFFER = [
    ("offer-matches", act("o1", 41, cls="booking.create", offer_fields_changed=0, offer_fields_basis=OFFER_BASIS)),
    ("offer-differs", act("o2", 42, offer_fields_changed=2, offer_fields_basis=OFFER_BASIS)),
    # The intent states no offer key: the producer sends neither (AMENDMENT 1).
    ("offer-nothing-stated", act("o3", 43)),
    ("offer-basis-absent", act("o4", 44, offer_fields_changed=1)),
    ("offer-count-absent", act("o5", 45, offer_fields_basis=OFFER_BASIS)),
    # The material list's basis is not the offer list's.
    ("offer-basis-other-list", act("o6", 46, offer_fields_changed=0, offer_fields_basis=MATERIAL_BASIS)),
    ("offer-out-of-scope", act("o7", 47, cls="info.query", offer_fields_changed=4, offer_fields_basis=OFFER_BASIS)),
    # Both counts on one action, each read over its own list.
    ("offer-and-material", act("o8", 48, material_fields_changed=0, material_fields_basis=MATERIAL_BASIS,
                                offer_fields_changed=1, offer_fields_basis=OFFER_BASIS)),
]

SCENARIOS = CHANNEL + MATERIAL + OFFER


def record(name, action, decision):
    fields = dataclasses.asdict(action)
    return {
        "scenario": name,
        "action": {k: fields[k] for k in NORMALIZED if fields[k] is not None},
        "outcome": decision.outcome,
        "constraints": [
            {"id": c.id, "result": c.result, "evidence_digest": json_digest(c.evidence) if c.evidence is not None else None}
            for c in decision.constraints
        ],
    }


def main(sha):
    out = {"capsule_engine_commit": sha, "source": "go/guard/testdata/parity/generate_change_checks.py", "scenarios": []}
    with tempfile.TemporaryDirectory() as tmp:
        pack_dir = Path(tmp) / "pack"
        shutil.copytree(PACK_DIR, pack_dir)
        signer = _signer()
        store = LedgerStore(Path(tmp) / "ledger")
        try:
            installed = install_pack(load_pack_dir(pack_dir), project_dir=Path(tmp) / "project", mode="observe")
            engine = build_engine(installed, ledger=store, signer_provider=lambda: signer)
            for name, action in SCENARIOS:
                out["scenarios"].append(record(name, action, engine.check(action)))
        finally:
            store.close()
    json.dump(out, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main(sys.argv[1])
