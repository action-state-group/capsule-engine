# SPDX-License-Identifier: Apache-2.0
"""Write, or check, the shared referee vectors the Python referee and the Go
``go/referee`` package both decide (``conformance-vectors/referee/v0/cases.json``).

A case is the inputs of one check and its ``expected`` outcome:

* ``verify``: ``capsule`` (a referee's sealed Result), ``doc`` (the Result's JSON
  text), both halves and both parties' terms; decided by
  ``capsule_engine.referee.verify_referee_result`` (Go: ``referee.VerifyResult``).
  The doc is text because a receiver holds bytes, and a float or non-JSON text is
  one of the cases.
* ``bind``: both parties' ``half``, ``action`` and ``terms``; the opening check a
  receiver runs first, which is ``_open_parties`` of evidencebook's Goose node
  (``goose_node.py``) without its ``not_a_party``, which needs the node's key
  (Go: ``referee.BindOpenings``). Python has no function for it, so
  ``bind_openings`` below is that check, in terms of ``capsule_engine.referee``.

``expected`` is ``{"ok": true}`` or ``{"code": "<RefereeError code>"}``. Python
exceptions other than ``RefereeError`` that a malformed input raises are
``not_a_result``, as the Goose node maps them.

The inputs are sealed live (a fresh key and timestamp each time), so they are
sealed once, by ``--generate``, and committed. ``--write`` recomputes only the
``expected`` of the committed inputs, so a rerun is byte-stable; ``--check`` fails
when any ``expected`` is not what this engine gives, or when a code is not covered.
Python is frozen: a change here is never a change to ``capsule_engine``.
"""
from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CASES = ROOT / "conformance-vectors" / "referee" / "v0" / "cases.json"

os.environ["CAPSULE_WITNESS"] = "off"
os.environ.pop("CAPSULE_LEDGER", None)
os.environ.pop("CAPSULE_SIGNING_KEY_PATH", None)

from agent_action_capsule.canonical import jcs
from capsule_emit import seal
from capsule_emit.bilateral import seal_action, seal_request
from capsule_engine import referee
from capsule_engine.referee import RefereeError

#: Every RefereeError code the receive path can raise and the Go package returns.
CODES = (
    "half_unverified", "opening_mismatch", "halves_unpaired", "result_unverified",
    "not_named_referee", "not_a_result", "other_deal", "result_mismatch", "result_invalid",
)


def _load_deal_class():
    spec = importlib.util.spec_from_file_location("referee_fixture", ROOT / "python" / "tests" / "test_referee.py")
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(ROOT / "python"))
    spec.loader.exec_module(module)
    return module


def bind_openings(buyer: dict, seller: dict) -> None:
    """evidencebook's ``_open_parties`` binding, less ``not_a_party``."""
    for p in (buyer, seller):
        if not referee._authentic(p["half"]):
            raise RefereeError("half_unverified", "half does not verify")
        committed = referee._compute(p["half"]).get("action_digest")
        if committed != referee.action_digest(p["action"], p["terms"]):
            raise RefereeError("opening_mismatch", "opening does not recompute")
    if buyer["half"].get("key_id") == seller["half"].get("key_id"):
        raise RefereeError("halves_unpaired", "both halves are signed by one key")


def decide(case: dict) -> dict:
    try:
        if case["kind"] == "bind":
            bind_openings(case["buyer"], case["seller"])
        else:
            referee.verify_referee_result(
                case["capsule"], json.loads(case["doc"]), buyer_half=case["buyer_half"],
                seller_half=case["seller_half"], buyer_terms=case["buyer_terms"], seller_terms=case["seller_terms"])
    except RefereeError as exc:
        return {"code": exc.code}
    except (ValueError, TypeError, AttributeError, KeyError):
        return {"code": "not_a_result"}
    return {"ok": True}


def _generate() -> list[dict]:
    fixture = _load_deal_class()
    tmp = Path(tempfile.mkdtemp(prefix="referee-vectors-"))
    deal = fixture.Deal(tmp)
    doc = deal.judge()
    text = jcs(doc).decode()
    halves = {"buyer_half": deal.buyer.half, "seller_half": deal.seller.half}
    terms = {"buyer_terms": deal.terms, "seller_terms": deal.seller_terms}

    def sealed(d: dict, book: str | None = None, **kw) -> dict:
        return referee.seal_result(d, ledger=book or deal.referee_book, operator="referee-co",
                                   **{**halves, **kw}).capsule

    good = sealed(doc)
    cases: list[dict] = []

    def verify(name: str, *, capsule=None, doc_text=None, **over) -> None:
        case = {"name": name, "kind": "verify", "capsule": copy.deepcopy(capsule if capsule is not None else good),
                "doc": doc_text if doc_text is not None else text,
                **copy.deepcopy({**halves, **terms}), **copy.deepcopy(over)}
        cases.append(case)

    def with_doc(edit) -> tuple[dict, str]:
        d = copy.deepcopy(doc)
        edit(d)
        return sealed(d), jcs(d).decode()

    verify("ok")
    tampered = copy.deepcopy(good)
    tampered["operator"] = "someone-else"
    verify("capsule-tampered", capsule=tampered)
    unsigned = {k: v for k, v in good.items() if k not in ("signature", "key_id")}
    verify("capsule-unsigned", capsule=unsigned)
    upper = {**good, "key_id": good["key_id"].upper()}
    verify("key-id-upper-case", capsule=upper)
    verify("signed-by-another-key", capsule=sealed(doc, deal.other_book))
    other_terms = copy.deepcopy(deal.seller_terms)
    other_terms["referee"] = {**other_terms["referee"], "key_id": "ab" * 32}
    verify("terms-name-different-referees", seller_terms=other_terms)
    verify("terms-referee-not-an-object", seller_terms={**deal.seller_terms, "referee": "x"})
    verify("terms-referee-key-unhashable", seller_terms={**deal.seller_terms, "referee": {"key_id": [1]}},
           buyer_terms={**deal.terms, "referee": {"key_id": [1]}})
    verify("terms-not-an-object", seller_terms=[])
    plain = seal(doc, action="referee.result", operator="referee-co", verdict="assessed", ledger=deal.referee_book)
    verify("no-result-block", capsule=plain.capsule)
    wrong = seal(doc, action="referee.result", operator="referee-co", verdict="assessed", ledger=deal.referee_book,
                 extra_compute={referee.RESULT_BLOCK: {"schema": "other/v1"}})
    verify("result-block-other-schema", capsule=wrong.capsule)
    verify("result-names-swapped-halves", capsule=sealed(doc, buyer_half=deal.seller.half, seller_half=deal.buyer.half))
    verify("buyer-half-without-capsule-id", buyer_half={k: v for k, v in deal.buyer.half.items() if k != "capsule_id"})
    altered = copy.deepcopy(doc)
    next(c for c in altered["claims"] if c["id"] == "deal-terms/item-agreed")["verdict"] = "not_met"
    verify("document-altered", doc_text=jcs(altered).decode())
    verify("document-not-json", doc_text="not json")
    verify("document-trailing-data", doc_text=text + " x")
    verify("document-with-a-float", doc_text=text[:-1] + ',"x":1.5}')
    for name, edit in (
        ("duplicate-claim-id", lambda d: d["claims"].append(copy.deepcopy(d["claims"][0]))),
        ("claim-in-no-bucket", lambda d: d["aggregate"]["buckets"]["met"].pop()),
        ("bucket-names-no-claim", lambda d: d["aggregate"]["buckets"]["met"].append("no-such-claim")),
        ("coverage-count-wrong", lambda d: d["aggregate"]["coverage"].update(evaluated_population=0)),
        ("schema-member-missing", lambda d: d.pop("generated_at")),
        ("verdict-out-of-scope", lambda d: d["claims"][0].update(verdict="out_of_scope")),
        ("claim-with-empty-evidence", lambda d: d["claims"][0].update(evidence=[])),
    ):
        capsule, doc_text = with_doc(edit)
        verify(name, capsule=capsule, doc_text=doc_text)

    # bind: the openings against the signed halves.
    def side(party) -> dict:
        return {"half": party.half, "action": party.action, "terms": party.terms}

    def bind(name: str, buyer: dict, seller: dict) -> None:
        cases.append({"name": name, "kind": "bind", "buyer": copy.deepcopy(buyer), "seller": copy.deepcopy(seller)})

    buyer, seller = side(deal.buyer), side(deal.seller)
    bind("bind-ok", buyer, seller)
    bind("bind-half-tampered", {**buyer, "half": {**buyer["half"], "operator": "x"}}, seller)
    bind("bind-opening-edited", {**buyer, "terms": {**buyer["terms"], "item": "other"}}, seller)
    bind("bind-action-edited", buyer, {**seller, "action": "sell"})
    req = seal_request("buyer-co", "goose@test", fixture.ACTION, referee.action_digest(fixture.ACTION, deal.terms),
                       ledger=deal.buyer_book, extra_compute={"peer": "seller-co"})
    resp = seal_action("seller-co", "goose@test", fixture.ACTION, referee.action_digest(fixture.ACTION, deal.seller_terms),
                       req.capsule_id, ledger=deal.buyer_book, extra_compute={"peer": "buyer-co"})
    bind("bind-halves-one-key", {**buyer, "half": req.capsule}, {**seller, "half": resp.capsule})
    for case in cases:
        case["expected"] = decide(case)
    return cases


def _dump(cases: list[dict]) -> str:
    return json.dumps({"cases": cases}, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--generate", action="store_true", help="seal new inputs and write every case (rare)")
    mode.add_argument("--write", action="store_true", help="recompute every expected outcome from the committed inputs")
    mode.add_argument("--check", action="store_true", help="fail if an expected outcome is not what Python gives")
    args = ap.parse_args(argv)
    if args.generate:
        CASES.parent.mkdir(parents=True, exist_ok=True)
        CASES.write_text(_dump(_generate()))
        return 0
    cases = json.loads(CASES.read_text())["cases"]
    changed = [c["name"] for c in cases if c["expected"] != decide(c)]
    seen = {c["expected"].get("code") for c in cases}
    missing = [c for c in CODES if c not in seen]
    if args.write:
        for c in cases:
            c["expected"] = decide(c)
        CASES.write_text(_dump(cases))
    if (changed and args.check) or missing or "ok" not in {k for c in cases for k in c["expected"]}:
        print(f"referee vectors: expected differs for {changed}; codes not covered: {missing}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
