# SPDX-License-Identifier: Apache-2.0
"""Decision-level differential vectors for four asg/seller/0.1.0 checks.

The engine's seller acceptance scenario (testdata/seller-vectors) runs the
seller's rail only on steps that cite a mandate, which the Go port refuses,
and reaches few of the promise and disclosure branches. These corpora run each
of seller.destination_rail/1.0.0, promise_never/1.0.0,
promise_requires_approval/1.0.0 and required_disclosure/1.0.0 (with its fold
disclosure.class_seen/1.0.0) through every branch, on the seller pack, with no
step citing a mandate.

Each corpus is run IN ORDER on ONE ledger. A step is checked with its own
task_authority_record (else none), and its authorization_record when it names
one. An authorized_by or task_authority_ref of the form "digest-of:<name>" is
the engine's own JSON digest of the corpus's records[<name>] (written into the
corpus by --build-corpus, so the corpus is data the Go test reads as is).

Writes <name>-decisions.json per corpus: per step the outcome, the decision,
every constraint's result and every constraint's evidence_digest (null for an
n/a the engine seals with no evidence).

    python -P generate_s2_decisions.py <capsule-engine checkout> --build-corpus   # writes the corpora, then the decisions
    python -P generate_s2_decisions.py <capsule-engine checkout>                  # writes the decisions
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from agent_action_capsule import json_digest
from capsule_ledger.ledger import LedgerStore

from capsule_engine.guards import Action, LocalSigner
from capsule_engine.packs import build_engine, install_pack, load_pack_dir

HERE = Path(__file__).parent
SIGNER = LocalSigner(key_id="seller-s2-differential-key", secret=b"seller-s2-differential-fixed-key")
NAMES = ("rail", "promise-never", "promise-requires-approval", "required-disclosure")

OP, DEV = "seller-s2", "seller-assistant@v1"
T1, T2, T3, T4, T5 = (f"cp-fp:{c * 64}" for c in "12345")


def _act(n: int, verb: str, cls: str, **fields) -> dict:
    a = {"verb": verb, "operator": OP, "developer": DEV, "action_class": cls, "action_id": f"{verb}/s2-{n}",
         "timestamp": f"2026-10-09T11:{n:02d}:00Z", "equivalence_key": f"{verb}/s2-{n}"}
    a.update(fields)
    return a


def _step(name: str, action: dict, *, dry_run: bool = True, **extra) -> dict:
    return {"name": name, "dry_run": dry_run, "action": action, **extra}


def _plan(**more) -> dict:
    return {"type": "task-authority/v0",
            "body": {"outcome_id": "seller.sell_one_item/1.0.0", "allowed_actions": ["offer", "accept", "sell", "tell"],
                     "preconditions": [], **more}}


def _rail() -> dict:
    sale = lambda n, **f: _act(n, "sell", "marketplace.sale", target=T1, amount_minor=180000, currency="USD", **f)  # noqa: E731
    return {"records": {}, "steps": [
        _step("sale-on-card", sale(1, rail="card")),
        _step("sale-on-p2p", sale(2, rail="p2p")),
        _step("sale-on-cheque", sale(3, rail="cheque")),
        _step("sale-on-an-empty-rail", sale(4, rail="")),
        _step("sale-with-no-rail", sale(5)),
        _step("offer-on-cheque", _act(6, "offer", "marketplace.offer", target=T1, amount_minor=180000, currency="USD",
                                      rail="cheque")),
    ]}


def _promise_never() -> dict:
    tell = lambda n, **f: _act(n, "tell", "communication.send", target=T1, **f)  # noqa: E731
    records = {"authenticity-approval": {"type": "approval/v0", "body": {"representation_class": "authenticity"}},
               "allows-authenticity": _plan(authorized_representation_classes=["authenticity"])}
    return {"records": records, "steps": [
        _step("authenticity", tell(1, representation_class="authenticity")),
        _step("authenticity-with-a-bound-approval", tell(2, representation_class="authenticity",
                                                         authorized_by="digest-of:authenticity-approval"),
              authorization_record="authenticity-approval"),
        _step("authenticity-under-a-task-authority-allowing-it",
              tell(3, representation_class="authenticity", task_authority_ref="digest-of:allows-authenticity"),
              task_authority_record="allows-authenticity"),
        _step("price", tell(4, representation_class="price")),
        _step("a-class-outside-the-closed-set", tell(5, representation_class="Authenticity")),
        _step("no-class", tell(6)),
        _step("a-class-on-a-query", _act(7, "read", "info.query", target="listing/own", representation_class="authenticity")),
        _step("authenticity-on-a-sale", _act(8, "sell", "marketplace.sale", target=T1, amount_minor=180000,
                                             currency="USD", rail="card", representation_class="authenticity")),
    ]}


def _promise_requires_approval() -> dict:
    tell = lambda n, **f: _act(n, "tell", "communication.send", target=T1, **f)  # noqa: E731
    w = "delivery_date"
    records = {
        "delivery-date-approval": {"type": "approval/v0", "body": {"representation_class": w}},
        "condition-approval": {"type": "approval/v0", "body": {"representation_class": "condition"}},
        "listed-class-approval": {"type": "approval/v0", "body": {"representation_class": [w]}},
        "bodiless-approval": {"type": "approval/v0", "representation_class": w},
        "classless-approval": {"type": "approval/v0", "body": {}},
        "allows-delivery-date": _plan(authorized_representation_classes=["price", w]),
        "allows-price-only": _plan(authorized_representation_classes=["price"]),
        "allows-delivery-date-as-text": _plan(authorized_representation_classes=w),
        "no-allowance": _plan(),
        # No JSON digest: JCS refuses a float, and an integer past 2^53.
        "approval-with-a-float": {"type": "approval/v0", "body": {"representation_class": w, "weight": 1.5}},
        "approval-with-an-unsafe-integer": {"type": "approval/v0", "body": {"representation_class": w, "n": 2**60}},
    }
    return {"records": records, "steps": [
        _step("delivery-date-citing-nothing", tell(1, representation_class=w)),
        _step("delivery-date-citing-an-approval-not-supplied", tell(2, representation_class=w,
                                                               authorized_by="digest-of:delivery-date-approval")),
        _step("delivery-date-with-another-record-supplied", tell(3, representation_class=w,
                                                            authorized_by="digest-of:delivery-date-approval"),
              authorization_record="condition-approval"),
        _step("delivery-date-with-an-approval-for-another-class", tell(4, representation_class=w,
                                                                  authorized_by="digest-of:condition-approval"),
              authorization_record="condition-approval"),
        _step("delivery-date-with-a-bound-approval", tell(5, representation_class=w, authorized_by="digest-of:delivery-date-approval"),
              authorization_record="delivery-date-approval"),
        _step("delivery-date-approval-naming-a-list", tell(6, representation_class=w,
                                                      authorized_by="digest-of:listed-class-approval"),
              authorization_record="listed-class-approval"),
        _step("delivery-date-approval-with-no-body", tell(7, representation_class=w, authorized_by="digest-of:bodiless-approval"),
              authorization_record="bodiless-approval"),
        _step("delivery-date-approval-naming-no-class", tell(8, representation_class=w,
                                                        authorized_by="digest-of:classless-approval"),
              authorization_record="classless-approval"),
        _step("delivery-date-approval-with-a-float", tell(21, representation_class=w, authorized_by="f" * 64),
              authorization_record="approval-with-a-float"),
        _step("delivery-date-approval-with-an-unsafe-integer", tell(22, representation_class=w, authorized_by="f" * 64),
              authorization_record="approval-with-an-unsafe-integer"),
        _step("delivery-date-inside-the-task-authority", tell(9, representation_class=w, task_authority_ref="digest-of:allows-delivery-date"),
              task_authority_record="allows-delivery-date"),
        _step("delivery-date-outside-the-task-authority",
              tell(11, representation_class=w, task_authority_ref="digest-of:allows-price-only"),
              task_authority_record="allows-price-only"),
        _step("delivery-date-outside-the-task-authority-with-a-bound-approval",
              tell(12, representation_class=w, task_authority_ref="digest-of:allows-price-only",
                   authorized_by="digest-of:delivery-date-approval"),
              task_authority_record="allows-price-only", authorization_record="delivery-date-approval"),
        _step("delivery-date-task-authority-listing-text",
              tell(13, representation_class=w, task_authority_ref="digest-of:allows-delivery-date-as-text"),
              task_authority_record="allows-delivery-date-as-text"),
        _step("delivery-date-task-authority-with-no-allowance",
              tell(14, representation_class=w, task_authority_ref="digest-of:no-allowance"),
              task_authority_record="no-allowance"),
        _step("delivery-date-task-authority-not-bound",
              tell(15, representation_class=w, task_authority_ref="digest-of:allows-price-only"),
              task_authority_record="allows-delivery-date"),
        _step("delivery-date-task-authority-not-supplied",
              tell(16, representation_class=w, task_authority_ref="digest-of:allows-delivery-date")),
        _step("condition", tell(17, representation_class="condition")),
        _step("a-class-outside-the-closed-set", tell(18, representation_class="guarantee")),
        _step("no-class", tell(19)),
        _step("delivery-date-shared-as-personal-data", _act(20, "share", "disclosure.personal", target=T1,
                                                      recipient_role="buyer", representation_class=w)),
    ]}


def _required_disclosure() -> dict:
    offer = lambda n, target, **f: _act(n, "offer", "marketplace.offer", target=target, amount_minor=180000,  # noqa: E731
                                        currency="USD", **f)
    tell = lambda n, target, cls="condition", **f: _act(n, "tell", "communication.send", target=target,  # noqa: E731
                                                        representation_class=cls, **f)
    return {"records": {}, "steps": [
        _step("offer-to-t1-before-any-statement", offer(1, T1)),
        _step("condition-told-to-t1", tell(2, T1), dry_run=False),
        _step("offer-to-t1-after-the-statement", offer(3, T1)),
        _step("accept-in-t1", _act(4, "accept", "agreement.accept", target=T1, amount_minor=180000, currency="USD")),
        _step("condition-told-to-t2-as-a-dry-run", tell(5, T2)),
        _step("offer-to-t2-after-a-dry-run-statement", offer(6, T2)),
        _step("condition-told-to-t3-by-another-operator", tell(7, T3) | {"operator": "another-seller"}, dry_run=False),
        _step("offer-to-t3-told-by-another-operator", offer(8, T3)),
        # No equivalence_key: dedupe keys the act on what it states, so the
        # second statement repeats the first and is refused.
        _step("condition-told-to-t4-as-a-dry-run", tell(9, T4, equivalence_key=None)),
        _step("condition-told-to-t4-again-refused", tell(10, T4, equivalence_key=None), dry_run=False),
        _step("offer-to-t4-after-a-refused-statement", offer(11, T4)),
        _step("price-told-to-t5", tell(12, T5, "price"), dry_run=False),
        _step("offer-to-t5-told-only-a-price", offer(13, T5)),
        _step("offer-to-no-one", offer(14, None)),
        _step("condition-told-to-t1-again", tell(15, T1), dry_run=False),
        _step("offer-to-t1-told-twice", offer(16, T1)),
        _step("a-statement-is-out-of-scope", tell(17, T2, "price")),
    ]}


def _resolve(value: object, records: dict) -> object:
    if isinstance(value, str) and value.startswith("digest-of:"):
        return json_digest(records[value.removeprefix("digest-of:")])
    return value


def build_corpus() -> None:
    for name, scenario in zip(NAMES, (_rail(), _promise_never(), _promise_requires_approval(),
                                      _required_disclosure()), strict=True):
        records = scenario["records"]
        for step in scenario["steps"]:
            step["action"] = {k: _resolve(v, records) for k, v in step["action"].items() if v is not None}
        corpus = {"note": __doc__.split("\n\n")[1].replace("\n", " "),
                  "pack": "capsule_engine/packs/catalog/seller (asg/seller/0.1.0)", **scenario}
        (HERE / f"{name}-corpus.json").write_text(json.dumps(corpus, indent=1) + "\n")


def main(checkout: Path) -> None:
    pack_dir = checkout / "capsule_engine" / "packs" / "catalog" / "seller"
    pack = load_pack_dir(pack_dir)
    commit = subprocess.check_output(["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True).strip()
    for name in NAMES:
        corpus = json.loads((HERE / f"{name}-corpus.json").read_text())
        records = corpus["records"]
        out: dict = {"capsule_engine_commit": commit, "pack": {"pack_id": pack.pack_id,
                                                                "definition_digest": pack.definition_digest()},
                     "decisions": {}}
        with tempfile.TemporaryDirectory() as tmp, LedgerStore(Path(tmp) / "ledger") as store:
            installed = install_pack(pack, project_dir=Path(tmp) / "project", mode="observe")
            engine = build_engine(installed, ledger=store, signer_provider=lambda: SIGNER)
            for step in corpus["steps"]:
                ta, auth = step.get("task_authority_record"), step.get("authorization_record")
                decision = engine.check(
                    Action(**step["action"]),
                    dry_run=step["dry_run"],
                    task_authority_record=records[ta] if ta else None,
                    authorization_record=records[auth] if auth else None,
                )
                out["decisions"][step["name"]] = {
                    "outcome": decision.outcome,
                    "decision": decision.capsule["disposition"]["decision"],
                    "results": {c.id: c.result for c in decision.constraints},
                    "evidence_digests": {c.id: json_digest(c.evidence) if c.evidence is not None else None
                                         for c in decision.constraints},
                }
        (HERE / f"{name}-decisions.json").write_text(json.dumps(out, indent=2, sort_keys=True) + "\n")
        print(f"{name}: wrote {len(out['decisions'])} decisions under {pack.pack_id} {pack.definition_digest()}")


if __name__ == "__main__":
    if "--build-corpus" in sys.argv:
        build_corpus()
    main(Path(sys.argv[1]))
