# SPDX-License-Identifier: Apache-2.0
"""Constraint-level differential vectors for the seller checks the plugin ports
in S1: caps/1.0.0 over spend.weekly/1.0.0, offer_expiry/1.0.1, price_floor/2.0.1
and seller.single_commitment/1.0.0, under asg/seller/0.1.0.

Every step runs IN ORDER through capsule-engine's own GuardEngine, built from
the seller pack, on ONE ledger, as the pack's acceptance scenario does
(tests/test_pack_seller_acceptance.py): single_commitment and caps read what
earlier steps sealed. Each step names the task-authority record and the
commercial-bounds opening it is checked with (variants built below), and
whether it cites the acceptance step's sealed capsule.

Run it on the Python capsule-engine's CI uses (3.12; 3.13 reads timestamps
the same way): from 3.14, datetime.fromisoformat reads an hour of 24 as the
next midnight, which changes offer_expiry on step oe-hour-24.

Writes corpus.json (the inputs, as data) and decisions.json (per step: the
sealed capsule id, the outcome, and every constraint's result and
evidence_digest, the SHA-256 of the evidence's JCS bytes, as the capsule
digests it). The Go port must reproduce, for each check it runs, the result
and the evidence digest.

    PYTHONPATH=<capsule-engine checkout> python -P generate.py <capsule-engine checkout> <commit>
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path

from agent_action_capsule import json_digest
from capsule_ledger.ledger import LedgerStore

from capsule_engine.guards import Action, LocalSigner
from capsule_engine.guards.checks.price_floor import bounds_commitment
from capsule_engine.guards.checks.task_authority import task_authority_record_digest
from capsule_engine.packs import build_engine, install_pack, load_pack_dir

HERE = Path(__file__).parent
SIGNER = LocalSigner(key_id="seller-s1-differential-key", secret=b"seller-s1-differential-fixed-key")
OP, DEV = "seller-fixture", "seller-assistant@v1"


def _fp(name: str) -> str:
    return "cp-fp:" + hashlib.sha256(name.encode()).hexdigest()


A, B, C = _fp("buyer-a"), _fp("buyer-b"), _fp("buyer-c")
ITEM = "item-ref:" + hashlib.sha256(b"seller-s1/item-1").hexdigest()
OTHER_ITEM = "item-ref:" + hashlib.sha256(b"seller-s1/item-2").hexdigest()
NONCE = hashlib.sha256(b"seller-s1/nonce").hexdigest()
OTHER_NONCE = hashlib.sha256(b"seller-s1/other-nonce").hexdigest()


def _task(body_extra: dict) -> dict:
    return {"type": "task-authority/v0", "body": {"outcome_id": "seller.sell_one_item/1.0.0",
                                                   "allowed_actions": ["offer", "accept", "sell", "tell", "transfer"],
                                                   "preconditions": [], **body_extra}}


def _bounds(document: dict) -> tuple[dict, dict]:
    """A task authority sealing the commitment to document, and its opening."""
    commitment = bounds_commitment(NONCE, document)
    return _task({"bounds_commitment": commitment}), {"document": document, "nonce": NONCE,
                                                      "bounds_commitment": commitment}


FLOOR_DOC = {"type": "commercial-bounds/v0", "min_total_minor": 170000}
TASK, OPENING = _bounds(FLOOR_DOC)
# A floor document the checker refuses although it opens the commitment.
BOOL_TASK, BOOL_OPENING = _bounds({"type": "commercial-bounds/v0", "min_total_minor": True})
NEG_TASK, NEG_OPENING = _bounds({"type": "commercial-bounds/v0", "min_total_minor": -1})
KIND_TASK, KIND_OPENING = _bounds({"type": "commercial-bounds/v1", "min_total_minor": 1})
# A commitment over a document holding U+FFFD, opened by one holding a lone
# surrogate where it sits: a decoder that replaces the surrogate would open it.
FFFD_TASK, _ = _bounds({"type": "commercial-bounds/v0", "min_total_minor": 1, "note": "\ufffd"})
TASKS = {
    "floor": TASK,
    "no-floor": _task({}),
    "malformed-commitment": _task({"bounds_commitment": "ABC"}),
    "upper-hex-commitment": _task({"bounds_commitment": OPENING["bounds_commitment"].upper()}),
    "bool-floor": BOOL_TASK,
    "negative-floor": NEG_TASK,
    "other-kind": KIND_TASK,
    "nested-null-body": {"type": "task-authority/v0", "body": None},
    "fffd": FFFD_TASK,
}
OPENINGS = {
    "floor": OPENING,
    "none": None,
    "wrong-nonce": {**OPENING, "nonce": OTHER_NONCE},
    "upper-nonce": {**OPENING, "nonce": NONCE.upper()},
    "other-commitment": {**OPENING, "bounds_commitment": hashlib.sha256(b"other").hexdigest()},
    "other-floor": {**OPENING, "document": {"type": "commercial-bounds/v0", "min_total_minor": 1}},
    "float-floor": {**OPENING, "document": {"type": "commercial-bounds/v0", "min_total_minor": 1.5}},
    "not-an-object": "opening",
    "document-not-an-object": {**OPENING, "document": [1]},
    "bool-floor": BOOL_OPENING,
    "negative-floor": NEG_OPENING,
    "other-kind": KIND_OPENING,
    "unsafe-floor": {**OPENING, "document": {"type": "commercial-bounds/v0", "min_total_minor": 2**60}},
    "lone-surrogate": {"document": {"type": "commercial-bounds/v0", "min_total_minor": 1, "note": "\ud800"},
                       "nonce": NONCE, "bounds_commitment": FFFD_TASK["body"]["bounds_commitment"]},
}


def ref(task: str) -> str:
    return task_authority_record_digest(TASKS[task])


def act(name: str, verb: str, cls: str, at: str, **fields) -> dict:
    return {"verb": verb, "operator": fields.pop("operator", OP), "developer": fields.pop("developer", DEV),
            "action_class": cls, "action_id": f"{verb}/{name}", "timestamp": at,
            "equivalence_key": f"{verb}/{name}", **fields}


def sale(name, cls, at, amount, target=A, task="floor", item=ITEM, proposal=None, **extra):
    verb = {"marketplace.offer": "offer", "agreement.accept": "accept", "marketplace.sale": "sell"}.get(cls, "commit")
    f = {"amount_minor": amount, "currency": "USD", "target": target, "task_authority_ref": ref(task),
         "item_ref": item, "proposal_at": proposal or at.replace(":30Z", ":00Z"), **extra}
    return act(name, verb, cls, at, **{k: v for k, v in f.items() if v is not None})


def step(name, action, *, real=False, task="floor", opening="floor", cites=False):
    return {"name": name, "dry_run": not real, "cites_acceptance": cites, "task": task, "opening": opening,
            "action": action}


def t(minute: int, second: int = 30, day: int = 9) -> str:
    return f"2026-10-{day:02d}T{10 + minute // 60:02d}:{minute % 60:02d}:{second:02d}Z"


def steps() -> list[dict]:
    s = []
    # Required statements, so a later acceptance is accepted.
    for who, target in (("a", A), ("b", B), ("c", C)):
        s.append(step(f"condition-to-{who}", act(f"condition-{who}", "tell", "communication.send", t(1),
                                                  target=target, representation_class="condition"), real=True))
    # price_floor/2.0.1.
    s += [
        step("pf-at-floor", sale("pf-at-floor", "marketplace.offer", t(2), 170000)),
        step("pf-below-floor", sale("pf-below", "marketplace.offer", t(2), 169999)),
        step("pf-zero", sale("pf-zero", "marketplace.offer", t(2), 0)),
        step("pf-no-amount", sale("pf-no-amount", "marketplace.offer", t(2), None)),
        step("pf-no-task-ref", sale("pf-no-ref", "marketplace.offer", t(2), 180000, task_authority_ref=None)),
        step("pf-record-not-the-ref", sale("pf-not-ref", "marketplace.offer", t(2), 180000, task="no-floor"),
             task="floor"),
        step("pf-no-floor-committed", sale("pf-no-floor", "marketplace.offer", t(2), 1, task="no-floor"),
             task="no-floor", opening="none"),
        step("pf-malformed-commitment", sale("pf-malformed", "marketplace.offer", t(2), 180000,
                                             task="malformed-commitment"), task="malformed-commitment"),
        step("pf-upper-hex-commitment", sale("pf-upper", "marketplace.offer", t(2), 180000,
                                             task="upper-hex-commitment"), task="upper-hex-commitment"),
        step("pf-null-body", sale("pf-null-body", "marketplace.offer", t(2), 180000, task="nested-null-body"),
             task="nested-null-body"),
        step("pf-no-opening", sale("pf-no-opening", "marketplace.offer", t(2), 180000), opening="none"),
        step("pf-wrong-nonce", sale("pf-wrong-nonce", "marketplace.offer", t(2), 180000), opening="wrong-nonce"),
        step("pf-upper-nonce", sale("pf-upper-nonce", "marketplace.offer", t(2), 180000), opening="upper-nonce"),
        step("pf-other-commitment", sale("pf-other-c", "marketplace.offer", t(2), 180000),
             opening="other-commitment"),
        step("pf-other-floor-document", sale("pf-other-f", "marketplace.offer", t(2), 180000),
             opening="other-floor"),
        step("pf-float-floor", sale("pf-float", "marketplace.offer", t(2), 180000), opening="float-floor"),
        step("pf-opening-not-an-object", sale("pf-not-obj", "marketplace.offer", t(2), 180000),
             opening="not-an-object"),
        step("pf-document-not-an-object", sale("pf-doc-list", "marketplace.offer", t(2), 180000),
             opening="document-not-an-object"),
        step("pf-bool-floor", sale("pf-bool", "marketplace.offer", t(2), 180000, task="bool-floor"),
             task="bool-floor", opening="bool-floor"),
        step("pf-negative-floor", sale("pf-neg", "marketplace.offer", t(2), 180000, task="negative-floor"),
             task="negative-floor", opening="negative-floor"),
        step("pf-other-kind", sale("pf-kind", "marketplace.offer", t(2), 180000, task="other-kind"),
             task="other-kind", opening="other-kind"),
        step("pf-unsafe-floor", sale("pf-unsafe", "marketplace.offer", t(2), 180000), opening="unsafe-floor"),
        step("pf-lone-surrogate", sale("pf-surrogate", "marketplace.offer", t(2), 180000, task="fffd"),
             task="fffd", opening="lone-surrogate"),
        step("pf-out-of-class", act("pf-tell", "tell", "communication.send", t(2), target=A,
                                    representation_class="pickup", amount_minor=1,
                                    task_authority_ref=ref("floor"))),
        step("pf-sale-below", sale("pf-sale-below", "marketplace.sale", t(2), 100, rail="card")),
        step("pf-accept-below", sale("pf-accept-below", "agreement.accept", t(2), 100)),
    ]
    # offer_expiry/1.0.1 (86400 seconds).
    s += [
        step("oe-fresh", sale("oe-fresh", "marketplace.offer", t(3), 180000, proposal="2026-10-09T09:03:30Z")),
        step("oe-at-limit", sale("oe-limit", "marketplace.offer", t(3), 180000, proposal="2026-10-08T10:03:30Z")),
        step("oe-past-limit", sale("oe-past", "marketplace.offer", t(3), 180000, proposal="2026-10-08T10:03:29Z")),
        step("oe-fraction", sale("oe-frac", "marketplace.offer", t(3), 180000,
                                 proposal="2026-10-08T10:03:29.5Z")),
        step("oe-fraction-both", sale("oe-frac2", "marketplace.offer", "2026-10-09T10:03:30.25Z", 180000,
                                      proposal="2026-10-09T10:03:29.75Z")),
        step("oe-same-instant", sale("oe-same", "marketplace.offer", t(3), 180000, proposal=t(3))),
        step("oe-later-than-action", sale("oe-later", "marketplace.offer", t(3), 180000,
                                          proposal="2026-10-09T10:03:31Z")),
        step("oe-not-utc", sale("oe-offset", "marketplace.offer", t(3), 180000,
                                proposal="2026-10-09T10:03:00+00:00")),
        step("oe-space", sale("oe-space", "marketplace.offer", t(3), 180000, proposal="2026-10-09 10:03:00Z")),
        step("oe-no-such-day", sale("oe-feb30", "marketplace.offer", t(3), 180000, proposal="2026-02-30T10:00:00Z")),
        step("oe-seven-digits", sale("oe-7d", "marketplace.offer", t(3), 180000,
                                     proposal="2026-10-09T10:03:00.1234567Z")),
        step("oe-lowercase-z", sale("oe-lz", "marketplace.offer", t(3), 180000, proposal="2026-10-09T10:03:00z")),
        step("oe-half-second-later", sale("oe-half", "marketplace.offer", "2026-10-09T10:03:30.25Z", 180000,
                                          proposal="2026-10-09T10:03:30.75Z")),
        step("oe-hour-24", sale("oe-h24", "marketplace.offer", t(3), 180000, proposal="2026-10-08T24:00:00Z")),
        step("oe-leap-second", sale("oe-leap", "marketplace.offer", t(3), 180000, proposal="2026-10-09T09:59:60Z")),
        step("oe-trailing-newline", sale("oe-nl", "marketplace.offer", t(3), 180000,
                                         proposal="2026-10-09T10:03:00Z\n")),
        step("oe-other-digits", sale("oe-digits", "marketplace.offer", t(3), 180000,
                                     proposal="\u0662\u0660\u0662\u0666-10-09T10:03:00Z")),
        step("oe-year-zero", sale("oe-y0", "marketplace.offer", t(3), 180000, proposal="0000-10-09T10:03:00Z")),
        step("oe-out-of-class", act("oe-tell", "tell", "communication.send", t(3), target=A,
                                    representation_class="pickup", proposal_at="garbage")),
    ]
    s.append(step("oe-no-proposal", {k: v for k, v in sale("oe-none", "marketplace.offer", t(3), 180000).items()
                                     if k != "proposal_at"}))
    # seller.single_commitment/1.0.0, then caps/1.0.0.
    s += [
        step("sc-before-acceptance", sale("sc-before", "marketplace.offer", t(4), 180000, target=B)),
        step("sc-no-item", sale("sc-no-item", "marketplace.offer", t(4), 180000, item=None)),
        step("sc-no-task-ref", sale("sc-no-ref", "marketplace.offer", t(4), 180000, task_authority_ref=None)),
        step("sc-acceptance-dry-run", sale("sc-accept-dry", "agreement.accept", t(5), 175000)),
        step("sc-condition-to-c-other-operator", act("sc-condition-c-other", "tell", "communication.send", t(5),
                                                      target=C, representation_class="condition",
                                                      operator="another-seller"), real=True),
        step("sc-acceptance-in-c-other-operator", sale("sc-accept-other-op", "agreement.accept", t(5), 175000,
                                                       target=C, operator="another-seller"), real=True),
        step("sc-acceptance-in-a", sale("sc-accept-a", "agreement.accept", t(6), 175000), real=True),
        step("sc-second-acceptance-in-b", sale("sc-accept-b", "agreement.accept", t(7), 175000, target=B)),
        step("sc-offer-in-b", sale("sc-offer-b", "marketplace.offer", t(7), 180000, target=B)),
        step("sc-sale-to-a-citing", sale("sc-sale-a", "marketplace.sale", t(8), 175000, rail="card"), cites=True),
        step("sc-sale-to-a-not-citing", sale("sc-sale-a-nocite", "marketplace.sale", t(8), 175000, rail="card")),
        step("sc-sale-to-b-citing", sale("sc-sale-b", "marketplace.sale", t(8), 175000, target=B, rail="card"),
             cites=True),
        step("sc-other-item", sale("sc-other-item", "marketplace.offer", t(8), 180000, target=B, item=OTHER_ITEM)),
        step("sc-other-task", sale("sc-other-task", "marketplace.offer", t(8), 1, target=B, task="no-floor"),
             task="no-floor", opening="none"),
        step("sc-external-commitment", sale("sc-ext", "external_commitment.other", t(8), 180000, target=B)),
        step("sc-no-target-citing", {k: v for k, v in sale("sc-no-target", "marketplace.sale", t(8), 175000,
                                                           rail="card").items() if k != "target"}, cites=True),
        step("sc-other-operator-offer-in-b", sale("sc-other-op-b", "marketplace.offer", t(8), 180000, target=B,
                                                  operator="another-seller")),
        step("sc-out-of-class", act("sc-tell", "tell", "communication.send", t(8), target=B,
                                    representation_class="pickup", item_ref=ITEM,
                                    task_authority_ref=ref("floor"))),
    ]
    xfer = lambda name, at, amount, **f: act(name, "transfer", "money.transfer", at, **{"amount_minor": amount,
                                                                                         "currency": "USD",
                                                                                         "target": A, **f})
    s += [
        step("caps-first", xfer("caps-first", t(10), 4000000), real=True),
        step("caps-second", xfer("caps-second", t(11), 5000000), real=True),
        step("caps-at-limit", xfer("caps-at-limit", t(12), 1000000)),
        step("caps-over", xfer("caps-over", t(12), 1000001)),
        step("caps-dry-run-not-counted", xfer("caps-dry", t(13), 1000000)),
        step("caps-other-developer", xfer("caps-other-dev", t(13), 9000000, developer="another-assistant")),
        step("caps-no-amount", {k: v for k, v in xfer("caps-none", t(13), 1).items() if k != "amount_minor"}),
        step("caps-window-passed", xfer("caps-later", "2026-10-16T10:12:00Z", 9000000)),
        step("caps-window-edge", xfer("caps-edge", "2026-10-16T10:10:30Z", 6000001)),
        step("caps-out-of-class", sale("caps-offer", "marketplace.offer", t(14), 99000000)),
    ]
    return s


def main(checkout: Path, commit: str) -> None:
    corpus = steps()
    pack_dir = checkout / "capsule_engine" / "packs" / "catalog" / "seller"
    pack = load_pack_dir(pack_dir)
    decisions: dict[str, dict] = {}
    sealed: dict[str, str] = {}
    with tempfile.TemporaryDirectory() as tmp, LedgerStore(Path(tmp) / "ledger") as store:
        installed = install_pack(pack, project_dir=Path(tmp) / "project", mode="observe")
        engine = build_engine(installed, ledger=store, signer_provider=lambda: SIGNER)
        for st in corpus:
            fields = dict(st["action"])
            if st["cites_acceptance"]:
                fields["cited_mandate_capsule_id"] = sealed["sc-acceptance-in-a"]
            decision = engine.check(Action(**fields), dry_run=st["dry_run"], task_authority_record=TASKS[st["task"]],
                                    commercial_bounds_opening=OPENINGS[st["opening"]])
            sealed[st["name"]] = decision.capsule["capsule_id"]
            decisions[st["name"]] = {
                "capsule_id": decision.capsule["capsule_id"],
                "outcome": decision.outcome,
                "decision": decision.capsule["disposition"]["decision"],
                "constraints": {c.id: {"result": c.result, "evidence_digest": json_digest(c.evidence)}
                                for c in decision.constraints},
            }
    out = {"note": __doc__.split("\n\n")[1].replace("\n", " "), "python": sys.version.split()[0], "pack_id": pack.pack_id,
           "definition_digest": pack.definition_digest(), "acceptance_step": "sc-acceptance-in-a",
           "tasks": TASKS, "openings": OPENINGS, "steps": corpus}
    (HERE / "corpus.json").write_text(json.dumps(out, indent=1) + "\n")
    (HERE / "decisions.json").write_text(json.dumps({"capsule_engine_commit": commit, "decisions": decisions},
                                                    indent=1, sort_keys=True) + "\n")
    print(f"wrote {len(decisions)} decisions under {pack.pack_id} {pack.definition_digest()}")


if __name__ == "__main__":
    main(Path(sys.argv[1]), sys.argv[2])
