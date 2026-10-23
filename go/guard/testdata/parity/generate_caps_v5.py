# Dev tool, not shipped: regenerates caps-v5-parity.json, Python reference
# decisions for caps/5.0.0 (the per-action limit reads the authorised
# maximum, spend_authorized_minor) over spend.weekly/3.0.0. Run from
# capsule-engine checked out read-only at the commit named in the output:
#
#   cd <capsule-engine@SHA> && <venv>/bin/python <this file> <SHA> > caps-v5-parity.json
#
# Four configurations:
#   pack: asg/everyday/0.3.5 (caps/5.0.0 over spend.weekly/3.0.0, a 2500
#         per-action limit), the released pack.yaml the engine keeps under
#         tests/fixtures/packs/everyday-0.3.5 from a710636c on (the catalog
#         pack before), built and installed as the acceptance test does. From
#         0.3.1 its counterparty_seen_before denies a first purchase from a
#         merchant, so a run of purchases never fills the window here;
#   pack_036: the same actions under the catalog pack from a710636c,
#         asg/everyday/0.3.6 (caps/5.1.0 over spend.weekly/3.1.0, the same
#         limits), on its own ledger;
#   bare_2500: a GuardEngine with caps/5.0.0's own limits for money.purchase
#         (2500 per action, 10000 per 7 days, per_action_reads) and no other
#         check, where the window does fill;
#   per_action_500: the engine's own caps/5.0.0 test configuration
#         (tests/test_caps_authorised_maximum.py _engine): money.purchase
#         with a 500 per-action limit and a 10000 window, per_action_reads
#         spend_authorized_minor.
# Each configuration has its own ledger; accepted decisions count toward the
# operator's total. No model is loaded.
import dataclasses
import json
import shutil
import sys
import tempfile
from pathlib import Path

from agent_action_capsule import json_digest
from capsule_ledger.ledger import LedgerStore

sys.path.insert(0, ".")
from tests.test_pack_everyday_acceptance import PACK_DIR as CATALOG_PACK_DIR, _signer  # noqa: E402
from capsule_engine.folds.loader import load_definition_file  # noqa: E402
from capsule_engine.guards import Action, GuardEngine  # noqa: E402
from capsule_engine.packs import build_engine, install_pack, load_pack_dir, obligation_results  # noqa: E402

PACK_DIR = Path("tests/fixtures/packs/everyday-0.3.5")
NORMALIZED = ("verb", "operator", "developer", "action_class", "action_id", "action_type", "timestamp",
              "amount_minor", "spend_authorized_minor", "currency", "target", "cited_mandate_capsule_id",
              "equivalence_key", "rail", "counterparty_account_ref", "outgoing_content", "recurrence")


def act(name, minute, cls, capture, authorized, verb="buy", operator="household-v5"):
    return Action(verb=verb, operator=operator, developer="assistant-a@v1", action_class=cls,
                  action_id=f"{verb}/caps-v5-{name}", timestamp=f"2026-10-07T09:{minute:02d}:00Z",
                  amount_minor=capture, spend_authorized_minor=authorized, currency="USD",
                  target=f"merchant/{name}", rail="card", counterparty_account_ref=f"acct-ref-{name}",
                  recurrence="one_time")


PACK = [
    # Authorised above the capture: the per-action limit reads 954.
    ("authorised-above-capture", act("a1", 1, "money.purchase", 454, 954)),
    # Authorised below the capture is not a maximum: falls back to 454.
    ("authorised-below-capture", act("a2", 2, "money.purchase", 454, 300)),
    # None declared: falls back to the capture.
    ("authorised-absent", act("a3", 3, "money.purchase", 454, None)),
    # 3000 authorised against 2500: the per-action limit trips on it, while
    # the capture, 454, is under it.
    ("authorised-over-limit", act("a4", 4, "money.purchase", 454, 3000)),
    ("booking-over-limit", act("booking-a", 5, "booking.create", 55880, None, verb="create_booking")),
]

# The window sums captures, never the authorised maximum: 9500 captured,
# then 954 authorised on a 454 capture. The total is 9954, under 10000;
# summing the authorised maximum would make it 10454 and trip the window.
WINDOW = [
    ("window-fill-1", act("w1", 31, "money.purchase", 2500, None, operator="household-window")),
    ("window-fill-2", act("w2", 32, "money.purchase", 2500, None, operator="household-window")),
    ("window-fill-3", act("w3", 33, "money.purchase", 2500, None, operator="household-window")),
    ("window-fill-4", act("w4", 34, "money.purchase", 2000, None, operator="household-window")),
    ("window-authorised-under-total", act("w5", 35, "money.purchase", 454, 954, operator="household-window")),
]

# caps/4.0.0's parity scenarios (generate_caps_v4.py at c0f45cb, removed when
# the shipped pack moved to caps/5.0.0), replayed under caps/5.0.0 with no
# authorised maximum declared: each falls back to the capture, so the
# decisions are caps/4.0.0's and the evidence adds per_action_basis.
V4 = [
    ("v4-purchase-1", act("p1", 21, "money.purchase", 2000, None, operator="household-v4")),
    ("v4-purchase-2", act("p2", 22, "money.purchase", 2500, None, operator="household-v4")),
    ("v4-cancel-unlinked", act("c1", 23, "booking.cancel", 2500, None, verb="cancel_booking", operator="household-v4")),
    ("v4-purchase-3", act("p3", 24, "money.purchase", 2500, None, operator="household-v4")),
    ("v4-purchase-4", act("p4", 25, "money.purchase", 2500, None, operator="household-v4")),
    ("v4-purchase-over-window", act("p5", 26, "money.purchase", 600, None, operator="household-v4")),
]

PER_ACTION_500 = [
    # The acceptance case: 954 authorised against a 500 per-action limit.
    ("authorised-954-limit-500", act("b1", 11, "money.purchase", 454, 954, operator="household-500")),
    ("absent-limit-500", act("b2", 12, "money.purchase", 454, None, operator="household-500")),
    ("below-capture-limit-500", act("b3", 13, "money.purchase", 454, 300, operator="household-500")),
]


def record(config, name, action, decision, definition=None):
    fields = dataclasses.asdict(action)
    # The pack configuration also records each rule's own result
    # (packs.obligation_results, capsule-engine a5e9e30 on).
    obligations = {} if definition is None else {"obligations": [
        {"id": r.obligation_id, "result": r.result} for r in obligation_results(definition, decision.constraints)]}
    return {
        **obligations,
        "config": config,
        "scenario": name,
        "action": {k: fields[k] for k in NORMALIZED if fields[k] is not None},
        "outcome": decision.outcome,
        "constraints": [
            {"id": c.id, "result": c.result, "evidence_digest": json_digest(c.evidence) if c.evidence is not None else None}
            for c in decision.constraints
        ],
        "caps_evidence": [c.evidence for c in decision.constraints if c.id == "caps"][0],
    }


def main(sha):
    out = {"capsule_engine_commit": sha, "source": "go/guard/testdata/parity/generate_caps_v5.py", "scenarios": []}
    with tempfile.TemporaryDirectory() as tmp:
        pack_dir = Path(tmp) / "pack"
        shutil.copytree(PACK_DIR, pack_dir)
        assert "wicket_ref: caps/5.0.0" in (pack_dir / "pack.yaml").read_text(), "this pack does not cite caps/5.0.0"
        signer = _signer()
        store = LedgerStore(Path(tmp) / "ledger")
        try:
            definition = load_pack_dir(pack_dir)
            installed = install_pack(definition, project_dir=Path(tmp) / "project", mode="observe")
            engine = build_engine(installed, ledger=store, signer_provider=lambda: signer)
            for name, action in PACK:
                out["scenarios"].append(record("pack", name, action, engine.check(action), definition))
        finally:
            store.close()
        store = LedgerStore(Path(tmp) / "ledger-036")
        try:
            definition = load_pack_dir(CATALOG_PACK_DIR)
            assert definition.pack_id == "asg/everyday/0.3.6", definition.pack_id
            installed = install_pack(definition, project_dir=Path(tmp) / "project-036", mode="observe")
            engine = build_engine(installed, ledger=store, signer_provider=lambda: signer)
            for name, action in PACK:
                out["scenarios"].append(record("pack_036", name, action, engine.check(action), definition))
        finally:
            store.close()
        store = LedgerStore(Path(tmp) / "ledger-2500")
        try:
            engine = GuardEngine(
                ledger=store,
                caps_fold=load_definition_file(Path("capsule_engine/folds/catalog_defs/spend.weekly.v3.yaml")),
                signer_provider=lambda: signer,
                caps_minor={"money.purchase": 10_000},
                per_action_minor={"money.purchase": 2_500},
                per_action_reads="spend_authorized_minor",
            )
            for name, action in WINDOW + V4:
                out["scenarios"].append(record("bare_2500", name, action, engine.check(action)))
        finally:
            store.close()
        store = LedgerStore(Path(tmp) / "ledger-500")
        try:
            engine = GuardEngine(
                ledger=store,
                caps_fold=load_definition_file(Path("capsule_engine/folds/catalog_defs/spend.weekly.v3.yaml")),
                signer_provider=lambda: signer,
                caps_minor={"money.purchase": 10_000},
                per_action_minor={"money.purchase": 500},
                per_action_reads="spend_authorized_minor",
            )
            for name, action in PER_ACTION_500:
                out["scenarios"].append(record("per_action_500", name, action, engine.check(action)))
        finally:
            store.close()
    json.dump(out, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main(sys.argv[1])
