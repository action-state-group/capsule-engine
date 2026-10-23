"""Generates counterparty_seen_before/4.0.0 ("no payee named") vectors from capsule-engine.

Run from a capsule-engine checkout's venv: python -P gen.py > vectors.json
Every scenario runs under everyday 0.3.5 (cites 4.0.0) and the frozen 0.3.4 (cites 3.0.0).
"""
import json, sys, tempfile
from pathlib import Path

from agent_action_capsule import json_digest
from capsule_ledger.ledger import LedgerStore

import capsule_engine
from capsule_engine.guards import Action, LocalSigner
from capsule_engine.guards.checks import RUNNABLE_CHECKS
from capsule_engine.guards.classes import TAXONOMY_VERSION
from capsule_engine.guards.wickets import load_definition_file
from capsule_engine.packs import build_engine, install_pack, load_pack_dir
from capsule_engine.packs.obligation_results import obligation_results
from capsule_engine.report.replay import replay

PKG = Path(capsule_engine.__file__).parent
REPO = PKG.parent
WICKETS = PKG / "guards" / "wickets" / "catalog_defs"
PACKS = {"0.3.5": load_pack_dir(PKG / "packs" / "catalog" / "everyday"),
         "0.3.4": load_pack_dir(REPO / "tests" / "fixtures" / "packs" / "everyday-0.3.4")}
SIGNER = LocalSigner(key_id="r06-no-payee", secret=b"r06-no-payee-fixed-key")
OP = "r06-no-payee"
R = ("r02-ordinary-purchase", "r06-new-merchant")


def act(n, target, cls="money.purchase", verb="make_purchase", **f):
    return Action(verb=verb, operator=OP, developer="assistant@v1", action_class=cls, amount_minor=900,
                  currency="EUR", rail="card", target=target, action_id=f"{verb}/r06-no-payee-{n}",
                  timestamp=f"2026-10-09T12:{n:02d}:00Z", **f)


def known(n, target):
    """A real accepted transfer to target: makes it seen."""
    return act(n, target, cls="money.transfer", verb="make_payment", recurrence="one_time",
               counterparty_account_ref=f"acct-ref-{n}")


# (setup actions decided for real, then the probed action decided as a dry run)
LIVE = {
    "no_target": ([], act(10, None)),
    "empty_target": ([], act(10, "")),
    "whitespace_target": ([], act(10, "   ")),
    "new_payee": ([], act(10, "shop/new")),
    "seen_payee": ([known(1, "shop/known")], act(10, "shop/known")),
    "blank_after_blank_accepted": ([known(1, "")], act(10, "")),
    "out_of_scope_class_no_target": ([], act(10, None, cls="comms.external", verb="send_message")),
}


def constraint_view(decision, capsule):
    (c,) = [c for c in decision.constraints if c.id == "counterparty_seen_before"]
    (sealed,) = [s for s in capsule["constraints"] if s["id"] == "counterparty_seen_before"]
    rules = {r.obligation_id: r.result for r in obligation_results(PACK, decision.constraints) if r.obligation_id in R}
    return {"result": c.result, "reason": c.reason, "method": c.method, "evidence": c.evidence,
            "sealed_evidence_digest": sealed.get("evidence_digest"),
            "failing_checks": [x.id for x in decision.constraints if x.result == "fail"],
            "outcome": decision.outcome, "rules": rules}


out = {"wickets": {}, "packs": {}, "rule": (
    "counterparty_seen_before/4.0.0 = 3.0.0 + config missing_target: 'unseen' (absent => 'n/a', versions 1-3). "
    "Order: class not in action_classes -> n/a (in_scope false); then, under 'unseen', target None OR target.strip()=='' "
    "-> fail, reason starts 'no payee named', evidence {missing_field:'target', operator, seen_before:false} "
    "(+ ignored_inputs when non-empty), NO fold run, no fold envelope; under 'n/a', target None -> n/a missing_field target "
    "(a blank string still runs the fold, unchanged); else the v3 count. Failing csb escalates when the class names an approver."),
    "live": {}, "replay_typed": {}}
for f in ("counterparty_seen_before.v3.yaml", "counterparty_seen_before.v4.yaml"):
    w = load_definition_file(WICKETS / f)
    out["wickets"][w.wicket_id] = {"digest": w.definition_digest(), "config": w.config, "semantics": w.semantics,
                                   "canonical_jcs_sha256": json_digest(w.canonical_dict())}
for v, p in PACKS.items():
    out["packs"][v] = {"pack_id": p.pack_id, "digest": p.definition_digest()}

for v, PACK in PACKS.items():
    out["live"][v] = {}
    for name, (setup, probe) in LIVE.items():
        with tempfile.TemporaryDirectory() as d:
            store = LedgerStore(Path(d) / "ledger")
            installed = install_pack(PACK, project_dir=Path(d) / "project", mode="observe")
            eng = build_engine(installed, ledger=store, signer_provider=lambda: SIGNER)
            setup_out = [eng.check(a).outcome for a in setup]
            dec = eng.check(probe, dry_run=True)
            out["live"][v][name] = {"setup_outcomes": setup_out, "probe_target": probe.target,
                                    "probe_action_class": probe.action_class, **constraint_view(dec, dec.capsule)}
            store.close()

    for name, payee in (("typed_no_counterparty", None), ("typed_new_payee", "a" * 64)):
        body = {"action": "pay", "action_class": "money.purchase", "taxonomy_version": TAXONOMY_VERSION,
                "currency": "USD", "amount_minor": 4_500, "spend_minor": 4_500}
        if payee:
            body["counterparty"] = {"fp_alg": "hmac-sha256-chain-key", "ids": {"payee": payee}}
        record = {"type": "proposed-action/v0", "canonicalization": "jcs", "chain_id": "deal-cccccccccccccccc",
                  "seq": 1, "at": "2026-10-09T12:00:00Z", "body": body}
        capsule = {"capsule_id": f"{1:064x}", "action_id": "deal-cccccccccccccccc/1", "action_type": "fyi",
                   "operator": OP, "developer": "capsulectl-deal", "timestamp": "2026-10-09T12:00:00Z",
                   "model_attestation": {"compute_attestation": {"agent_input_digest": json_digest(record)}}}
        with tempfile.TemporaryDirectory() as d:
            installed = install_pack(PACK, project_dir=Path(d) / "p", mode="observe")
            rs = installed.resolved
            res = replay([capsule], caps_fold=rs.caps_fold(), caps_minor=rs.caps_minor(),
                         manifest_digest=rs.manifest_digest, disclosed={capsule["capsule_id"]: record},
                         wickets=rs.configured_wickets(RUNNABLE_CHECKS), pack=installed.pack)
        (s,) = res.decisions
        out["replay_typed"].setdefault(v, {})[name] = {"record": record, **constraint_view(s.decision, s.decision.capsule)}

json.dump(out, sys.stdout, indent=1, sort_keys=True)
