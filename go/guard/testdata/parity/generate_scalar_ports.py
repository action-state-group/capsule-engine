# Dev tool, not shipped: regenerates <check>-parity.json, Python reference
# decisions for the scalar checks asg/everyday/0.3.1 cites, for the Go port's
# differential. Run from capsule-engine checked out read-only at the pack's
# commit:
#
#   cd <capsule-engine@SHA> && <venv>/bin/python <this file> <check> <SHA> > <check>-parity.json
#
# Two kinds of scenario, both through capsule-engine's own everyday pack,
# built and installed as its acceptance test does:
#   action: an Action built directly (the normalized --action shape), the
#           whole decision recorded; one ledger for the run, accepted
#           decisions appended as the engine appends them;
#   record: a capsulectl deal check record (a capsule header with its
#           disclosed agent_input, bound by agent_input_digest), read by the
#           engine's own bridge (report/replay.py action_for_record), each on
#           a fresh ledger. The Go side reads the same file with
#           DecodeRecord; the constraints named in "compare" are the ones the
#           record path is checked on.
# A scenario may carry a third member: the sealed task-authority record
# supplied beside the action (GuardEngine.check task_authority_record),
# written into the output as "task_authority_record". task-authority-golden.json
# beside this file is capsule-cli's golden task-authority/v0 record (its
# producer side), byte for byte.
# No model is loaded.
import dataclasses
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
              "amount_minor", "spend_authorized_minor", "currency", "target", "rail", "counterparty_account_ref",
              "outgoing_content", "recurrence", "refundable", "upfront_amount_minor", "recipient_role",
              "task_authority_ref")
TAXONOMY = json.loads(Path("capsule_engine/guards/action_taxonomy.json").read_text())["taxonomy_version"]


def act(name, minute, cls, verb="buy", amount=454, **fields):
    base = dict(verb=verb, operator=f"household-{name.split('-')[0]}", developer="assistant-a@v1", action_class=cls,
                action_id=f"{verb}/scalar-{name}", timestamp=f"2026-10-08T10:{minute:02d}:00Z",
                amount_minor=amount, currency="USD", target=f"merchant/{name}", rail="card",
                counterparty_account_ref=f"acct-ref-{name}", recurrence="one_time")
    base.update(fields)
    return Action(**base)


def deal_check(name, minute, body, counterparty=None):
    """A deal check record: its header and the agent_input it seals, with
    the x-deal-v0 counterparty block when one is given."""
    full = {"action": "pay", "action_class": "money.purchase", "taxonomy_version": TAXONOMY, "amount_minor": 454,
            "spend_minor": 454, "currency": "USD"}
    full.update(body)
    agent_input = {"body": {k: v for k, v in full.items() if v is not None},
                   "x-deal-v0": {"record_type": "check", "seq": 3, "deal_id": f"deal-{name}"}}
    if counterparty is not None:
        agent_input["x-deal-v0"]["counterparty"] = counterparty
    return {"operator": f"deal-{name}", "developer": "capsulectl", "action_id": f"deal-{name}/3",
            "action_type": "decide", "timestamp": f"2026-10-08T11:{minute:02d}:00Z",
            "model_attestation": {"compute_attestation": {"agent_input_digest": json_digest(agent_input)}},
            "agent_input": agent_input}


# A share check: no money moves.
SHARE = {"action": "share", "action_class": "disclosure.personal", "amount_minor": None, "spend_minor": None,
         "currency": None}

GOLDEN = json.loads((Path(__file__).parent / "task-authority-golden.json").read_text())
GOLDEN_REF = json_digest(GOLDEN)


def authority(body, **extra):
    """A task-authority record with this plan at body, and its digest."""
    record = {"type": "task-authority/v0", "seq": 2, "chain_id": "deal-ta", "body": body, **extra}
    return record, json_digest(record)


PLAN = {"outcome_id": "capsulectl.deal.purchase/1.0.0", "allowed_actions": ["pay"], "preconditions": []}
SUBJECT, SUBJECT_REF = authority(dict(PLAN, binding={"subject": "merchant/t9"}))
PRECONDITION, PRECONDITION_REF = authority(dict(PLAN, allowed_actions=["pay", "sign"],
                                                preconditions=[{"action": "pay", "citing": "the user's approval"}]))
WINDOW, WINDOW_REF = authority(dict(PLAN, window="P7D", binding={"window": "P7D"}))
EXTRA_KEYS, EXTRA_KEYS_REF = authority(dict(PLAN, preconditions=[{"action": "pay", "citing": "x", "note": "y"}],
                                            allowed=["pay"]))
NO_PLAN, NO_PLAN_REF = authority(None)
NO_PLAN.pop("body")
NO_PLAN_REF = json_digest(NO_PLAN)
BAD_OUTCOME, BAD_OUTCOME_REF = authority(dict(PLAN, outcome_id="purchase"))
DUPLICATE, DUPLICATE_REF = authority(dict(PLAN, allowed_actions=["pay", "pay"]))
UNGATED, UNGATED_REF = authority(dict(PLAN, preconditions=[{"action": "sign", "citing": "x"}]))
BAD_BINDING, BAD_BINDING_REF = authority(dict(PLAN, binding=["subject"]))
BAD_WINDOW, BAD_WINDOW_REF = authority(dict(PLAN, window=7))
NUMERIC_SUBJECT, NUMERIC_SUBJECT_REF = authority(dict(PLAN, binding={"subject": 9}))
NULL_SUBJECT, NULL_SUBJECT_REF = authority(dict(PLAN, binding={"subject": None}))
FLOAT = {"type": "task-authority/v0", "body": dict(PLAN), "max_total": 1.5}
UNSAFE = {"type": "task-authority/v0", "body": dict(PLAN), "max_total_minor": 2 ** 60}


def ta(name, minute, ref, verb="pay", cls="money.purchase", **fields):
    return act(name, minute, cls, verb=verb, task_authority_ref=ref, **fields)


def typed(digest, **over):
    return {"type": "record", "digest_alg": "SHA-256", "digest": digest, **over}


SCENARIOS = {
    "refundability": {
        "action": [
            ("refundable-true", act("r1", 1, "money.purchase", refundable=True)),
            ("refundable-false", act("r2", 2, "money.purchase", refundable=False)),
            ("refundable-absent", act("r3", 3, "money.purchase")),
            ("refundable-false-no-rail", act("r4", 4, "money.purchase", refundable=False, rail=None)),
            ("refundable-false-booking", act("r5", 5, "booking.create", verb="create_booking", amount=2000,
                                             refundable=False)),
            ("refundable-false-out-of-scope", act("r6", 6, "disclosure.personal", verb="share", amount=None,
                                                  refundable=False)),
        ],
        "compare": ["refundability", "destination_rail"],
        "record": [
            ("recourse-refundable", deal_check("d1", 1, {"recourse": {"rail": "card", "refundable": True}})),
            ("recourse-not-refundable", deal_check("d2", 2, {"recourse": {"rail": "card", "refundable": False}})),
            ("recourse-no-refundable", deal_check("d3", 3, {"recourse": {"rail": "card"}})),
            ("recourse-refundable-not-boolean", deal_check("d4", 4, {"recourse": {"rail": "card", "refundable": "yes"}})),
            ("recourse-watched-rail", deal_check("d5", 5, {"action": "send", "action_class": "money.transfer", "recourse": {"rail": "p2p", "refundable": True}})),
            ("recourse-rail-over-top-level", deal_check("d6", 6, {"action": "send", "action_class": "money.transfer", "rail": "card", "recourse": {"rail": "p2p"}})),
            ("top-level-rail-only", deal_check("d7", 7, {"action": "send", "action_class": "money.transfer", "rail": "p2p"})),
            ("recourse-empty-rail", deal_check("d8", 8, {"action": "send", "action_class": "money.transfer", "rail": "p2p", "recourse": {"rail": "", "refundable": False}})),
            ("recourse-not-an-object", deal_check("d9", 9, {"action": "send", "action_class": "money.transfer", "rail": "p2p", "recourse": "card"})),
            ("no-recourse", deal_check("d10", 10, {})),
            ("no-recourse-transfer", deal_check("d11", 11, {"action": "send", "action_class": "money.transfer", "rail": "card"})),
        ],
    },
    "upfront_amount": {
        # Defaults: 10000 absolute, 2500 bps (25%) of the total.
        "action": [
            ("upfront-within-both", act("u1", 11, "money.purchase", amount=2000, upfront_amount_minor=500)),
            ("upfront-over-share", act("u2", 12, "money.purchase", amount=2000, upfront_amount_minor=501)),
            ("upfront-over-absolute", act("u3", 13, "booking.create", verb="create_booking", amount=100000,
                                          upfront_amount_minor=10001)),
            ("upfront-over-both", act("u4", 14, "booking.create", verb="create_booking", amount=20000,
                                      upfront_amount_minor=15000)),
            ("upfront-at-absolute", act("u5", 15, "booking.create", verb="create_booking", amount=100000,
                                        upfront_amount_minor=10000)),
            ("upfront-zero", act("u6", 16, "money.purchase", amount=2000, upfront_amount_minor=0)),
            ("upfront-no-total-within", act("u7", 17, "money.purchase", amount=None, upfront_amount_minor=9000)),
            ("upfront-no-total-over", act("u8", 18, "money.purchase", amount=None, upfront_amount_minor=10500)),
            ("upfront-absent", act("u9", 19, "money.purchase", amount=2000)),
            ("upfront-out-of-scope", act("u10", 20, "info.query", verb="look_up", amount=None,
                                         upfront_amount_minor=99999)),
        ],
        "compare": ["upfront_amount"],
        "record": [
            ("record-upfront-within", deal_check("e1", 21, {"amount_minor": 2000, "spend_minor": 2000,
                                                            "upfront_amount_minor": 500})),
            ("record-upfront-over-share", deal_check("e2", 22, {"amount_minor": 2000, "spend_minor": 2000,
                                                                "upfront_amount_minor": 600})),
            # The bridge's total is spend_minor, never amount_minor.
            ("record-total-is-spend", deal_check("e3", 23, {"amount_minor": 4000, "spend_minor": 1600,
                                                            "upfront_amount_minor": 500})),
            # Money moving in is no spend: the total is 0.
            ("record-direction-in", deal_check("e4", 24, {"direction": "in", "upfront_amount_minor": 100})),
            ("record-no-spend", deal_check("e5", 25, {"spend_minor": None, "upfront_amount_minor": 500})),
            ("record-upfront-string", deal_check("e6", 26, {"upfront_amount_minor": "500"})),
            ("record-upfront-boolean", deal_check("e7", 27, {"upfront_amount_minor": True})),
            ("record-upfront-absent", deal_check("e9", 29, {})),
        ],
    },
    "recipient_role": {
        # Closed set fulfilling_merchant | third_party | self; allowed:
        # fulfilling_merchant, self; scope disclosure.personal.
        "action": [
            ("role-fulfilling-merchant", act("p1", 31, "disclosure.personal", verb="share", amount=None,
                                             recipient_role="fulfilling_merchant")),
            ("role-self", act("p2", 32, "disclosure.personal", verb="share", amount=None, recipient_role="self")),
            ("role-third-party", act("p3", 33, "disclosure.personal", verb="share", amount=None,
                                     recipient_role="third_party")),
            ("role-not-in-the-set", act("p4", 34, "disclosure.personal", verb="share", amount=None,
                                        recipient_role="courier")),
            ("role-absent", act("p5", 35, "disclosure.personal", verb="share", amount=None)),
            ("role-out-of-scope", act("p6", 36, "money.purchase", recipient_role="third_party")),
        ],
        "compare": ["recipient_role"],
        "record": [
            ("record-role-merchant", deal_check("f1", 31, dict(SHARE, recipient_role="fulfilling_merchant"))),
            ("record-role-third-party", deal_check("f2", 32, dict(SHARE, recipient_role="third_party"))),
            ("record-role-unknown", deal_check("f3", 33, dict(SHARE, recipient_role="neighbour"))),
            ("record-role-empty", deal_check("f4", 34, dict(SHARE, recipient_role=""))),
            ("record-role-not-a-string", deal_check("f5", 35, dict(SHARE, recipient_role=3))),
            ("record-role-absent", deal_check("f6", 36, dict(SHARE))),
            ("record-role-on-a-purchase", deal_check("f7", 37, {"recipient_role": "third_party"})),
        ],
    },
    "task_authority": {
        "action": [
            ("ta-golden-pay", ta("t1", 41, GOLDEN_REF), GOLDEN),
            ("ta-verb-outside-plan", ta("t2", 42, GOLDEN_REF, verb="refund"), GOLDEN),
            ("ta-no-record", ta("t3", 43, GOLDEN_REF), None),
            ("ta-ref-mismatch", ta("t4", 44, "0" * 64), GOLDEN),
            ("ta-no-ref", act("t5", 45, "money.purchase", verb="pay"), GOLDEN),
            ("ta-out-of-scope", ta("t6", 46, GOLDEN_REF, verb="look_up", cls="info.query", amount=None), GOLDEN),
            ("ta-subject-bound", ta("t9", 47, SUBJECT_REF), SUBJECT),
            ("ta-subject-mismatch", ta("t10", 48, SUBJECT_REF), SUBJECT),
            ("ta-subject-numeric", ta("t9-n", 49, NUMERIC_SUBJECT_REF), NUMERIC_SUBJECT),
            ("ta-subject-null", ta("t11", 50, NULL_SUBJECT_REF), NULL_SUBJECT),
            ("ta-precondition-uncited", ta("t12", 51, PRECONDITION_REF), PRECONDITION),
            ("ta-ungated-verb", ta("t13", 52, PRECONDITION_REF, verb="sign"), PRECONDITION),
            ("ta-window", ta("t14", 53, WINDOW_REF), WINDOW),
            ("ta-extra-keys", ta("t15", 54, EXTRA_KEYS_REF), EXTRA_KEYS),
            ("ta-no-plan", ta("t16", 55, NO_PLAN_REF), NO_PLAN),
            ("ta-bad-outcome-id", ta("t17", 56, BAD_OUTCOME_REF), BAD_OUTCOME),
            ("ta-duplicate-action", ta("t18", 57, DUPLICATE_REF), DUPLICATE),
            ("ta-precondition-not-allowed", ta("t19", 58, UNGATED_REF), UNGATED),
            ("ta-binding-not-a-mapping", ta("t20", 59, BAD_BINDING_REF), BAD_BINDING),
            ("ta-window-not-a-string", ta("t21", 0, BAD_WINDOW_REF), BAD_WINDOW),
            ("ta-record-with-a-float", ta("t22", 1, GOLDEN_REF), FLOAT),
            ("ta-record-with-an-unsafe-integer", ta("t23", 2, GOLDEN_REF), UNSAFE),
        ],
        "compare": ["task_authority"],
        "record": [
            ("record-ta-golden", deal_check("g1", 41, {"task_authority_ref": typed(GOLDEN_REF)}), GOLDEN),
            ("record-ta-no-record", deal_check("g2", 42, {"task_authority_ref": typed(GOLDEN_REF)}), None),
            ("record-ta-alg-not-sha256", deal_check("g3", 43, {"task_authority_ref": typed(GOLDEN_REF, digest_alg="sha256")}),
             GOLDEN),
            ("record-ta-no-type", deal_check("g4", 44, {"task_authority_ref": typed(GOLDEN_REF, type="")}), GOLDEN),
            ("record-ta-digest-not-hex", deal_check("g5", 45, {"task_authority_ref": typed(GOLDEN_REF.upper())}), GOLDEN),
            ("record-ta-plain-string", deal_check("g6", 46, {"task_authority_ref": GOLDEN_REF}), GOLDEN),
            ("record-ta-absent", deal_check("g7", 47, {}), GOLDEN),
            ("record-ta-verb-outside", deal_check("g8", 48, {"action": "refund", "task_authority_ref": typed(GOLDEN_REF)}),
             GOLDEN),
        ],
    },
    "target": {
        # The deal flow's target (r02, r06): the bridge's payee-fp:<fp_alg>:<payee>
        # from the sealed x-deal-v0 counterparty block, read by
        # counterparty_seen_before and counterparty_identity_change.
        "action": [
            ("target-payee-fp", act("s1", 1, "money.purchase", verb="pay",
                                    target="payee-fp:hmac-sha256-deal-key:" + "9" * 64)),
            ("target-absent", act("s2", 2, "money.purchase", verb="pay", target=None)),
        ],
        "compare": ["counterparty_seen_before", "counterparty_identity_change"],
        "record": [
            ("record-payee", deal_check("h1", 1, {}, {"fp_alg": "hmac-sha256-deal-key",
                                                      "ids": {"payee": "9" * 64, "name": "8" * 64}})),
            ("record-name-only", deal_check("h2", 2, {}, {"fp_alg": "hmac-sha256-deal-key", "ids": {"name": "8" * 64}})),
            ("record-no-fp-alg", deal_check("h3", 3, {}, {"ids": {"payee": "9" * 64}})),
            ("record-empty-payee", deal_check("h4", 4, {}, {"fp_alg": "hmac-sha256-deal-key", "ids": {"payee": ""}})),
            ("record-ids-not-a-mapping", deal_check("h5", 5, {}, {"fp_alg": "hmac-sha256-deal-key", "ids": ["9" * 64]})),
            ("record-other-fp-alg", deal_check("h6", 6, {}, {"fp_alg": "hmac-sha256-other", "ids": {"payee": "9" * 64}})),
            ("record-no-counterparty", deal_check("h7", 7, {})),
            # The body's own counterparty member is not the target.
            ("record-body-counterparty-only", deal_check("h8", 8, {"counterparty": "Example Stickers"})),
            # Members are read by their exact names: a block whose fp_alg and
            # ids are upper case, or whose payee kind is, seals no payee
            # fingerprint (the Go port's exact-member-name fix).
            ("record-upper-case-members", deal_check("h9", 9, {}, {"FP_ALG": "hmac-sha256-deal-key",
                                                                   "IDS": {"payee": "9" * 64}})),
            ("record-mixed-case-members", deal_check("h10", 10, {}, {"Fp_Alg": "hmac-sha256-deal-key",
                                                                     "Ids": {"payee": "9" * 64}})),
            ("record-upper-case-keys", deal_check("h11", 11, {}, {"FP_ALG": "hmac-sha256-deal-key",
                                                                "IDS": {"PAYEE": "9" * 64}})),
            ("record-upper-case-payee", deal_check("h12", 12, {}, {"fp_alg": "hmac-sha256-deal-key",
                                                                   "ids": {"PAYEE": "9" * 64}})),
        ],
    },
}


def constraints(decision):
    return [{"id": c.id, "result": c.result,
             "evidence_digest": json_digest(c.evidence) if c.evidence is not None else None}
            for c in decision.constraints]


def main(check, sha):
    table = SCENARIOS[check]
    out = {"capsule_engine_commit": sha, "check": check, "compare": table["compare"],
           "source": "internal/guard/testdata/parity/generate_scalar_ports.py", "scenarios": [], "records": []}
    with tempfile.TemporaryDirectory() as tmp:
        pack_dir = Path(tmp) / "pack"
        shutil.copytree(PACK_DIR, pack_dir)
        signer = _signer()
        installed = install_pack(load_pack_dir(pack_dir), project_dir=Path(tmp) / "project", mode="observe")
        store = LedgerStore(Path(tmp) / "ledger")
        try:
            engine = build_engine(installed, ledger=store, signer_provider=lambda: signer)
            for name, action, *supplied in table["action"]:
                fields = dataclasses.asdict(action)
                record = supplied[0] if supplied else None
                d = engine.check(action, task_authority_record=record)
                row = {"scenario": name, "outcome": d.outcome, "constraints": constraints(d),
                       "action": {k: fields[k] for k in NORMALIZED if fields[k] is not None}}
                if record is not None:
                    row["task_authority_record"] = record
                out["scenarios"].append(row)
        finally:
            store.close()
        for i, (name, record, *supplied) in enumerate(table["record"]):
            authority_record = supplied[0] if supplied else None
            store = LedgerStore(Path(tmp) / f"ledger-record-{i}")
            try:
                engine = build_engine(installed, ledger=store, signer_provider=lambda: signer)
                action = action_for_record(record, record["agent_input"])
                d = engine.check(action, task_authority_record=authority_record)
                row = {"scenario": name, "record": record, "outcome": d.outcome, "constraints": constraints(d)}
                if authority_record is not None:
                    row["task_authority_record"] = authority_record
                out["records"].append(row)
            finally:
                store.close()
    json.dump(out, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
