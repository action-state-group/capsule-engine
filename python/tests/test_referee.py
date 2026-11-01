# SPDX-License-Identifier: Apache-2.0
"""The contract-named referee (``capsule_engine.referee``), on a three-node rehearsal.

The buyer, the seller and the referee each have their own book (a capsule-emit
ledger with its own TEST key, made fresh per test). The buyer and the seller seal
their halves of one deal and the step-06 settlement legs there; each party's own
deal records are the capsulectl fixtures this repository already holds (the
buyer's purchase in ``real-two-deal``, judged under everyday, and the seller's
sale thread in ``real-seller``, judged under seller), named in the terms by the
key they are signed with.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from agent_action_capsule.canonical import jcs
from agent_action_capsule.contracts import ReferenceEntry
from capsule_emit import read_ledger, settlement
from capsule_emit.bilateral import seal_action, seal_request
from capsule_emit.signing import resolve_signer

from capsule_engine import referee
from capsule_engine.cli.main import main
from capsule_engine.packs import load_pack_dir
from capsule_engine.referee import Party, RefereeError
from capsule_engine.report.replay import load_disclosed, load_records, load_withheld

FIXTURES = Path(__file__).parent / "fixtures"
BUYER_RECORDS = (FIXTURES / "real-two-deal" / "deal-1.bundle.json",)
SELLER_RECORDS = (FIXTURES / "real-seller" / "buyer-a.bundle.json",)
CATALOG = Path(referee.__file__).parent / "packs" / "catalog"
EVERYDAY = load_pack_dir(CATALOG / "everyday")
SELLER = load_pack_dir(CATALOG / "seller")
AS_OF = "2026-11-01T12:00:00Z"
PRICE = {"value": "2000", "assetCode": "USD", "assetScale": 2}
ACTION = "buy"


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setenv("CAPSULE_WITNESS", "off")
    monkeypatch.delenv("CAPSULE_LEDGER", raising=False)
    monkeypatch.delenv("CAPSULE_SIGNING_KEY_PATH", raising=False)


def _book(tmp_path: Path, name: str) -> str:
    return str(tmp_path / name / "book")


def _key(ledger: str) -> str:
    return resolve_signer(ledger).key_id


def _pack_ref(pack) -> dict:
    return {"pack_id": pack.pack_id, "definition_digest": pack.definition_digest()}


class Deal:
    """One deal sealed across three books; the knobs are the lab faults."""

    def __init__(self, tmp_path: Path, *, seller_price=PRICE, received=PRICE, sent_at="2026-11-01T10:05:00Z",
                 received_at="2026-11-01T10:30:00Z", seller_referee=None, rail=True) -> None:
        self.buyer_book, self.seller_book = _book(tmp_path, "buyer"), _book(tmp_path, "seller")
        self.referee_book, self.other_book = _book(tmp_path, "referee"), _book(tmp_path, "other")
        self.referee_key = _key(self.referee_book)
        named = {"key_id": self.referee_key, "mailbox": "nostr:referee-test"}
        buyer_recs, seller_recs = load_records(BUYER_RECORDS), load_records(SELLER_RECORDS)
        self.terms = {
            "item": "synthetic widget box",
            "price": PRICE,
            "expires_at": "2026-11-01T11:00:00Z",
            "buyer": {"operator": "buyer-co", "book_key_id": buyer_recs[0]["key_id"], "pack": _pack_ref(EVERYDAY)},
            "seller": {"operator": "seller-co", "book_key_id": seller_recs[0]["key_id"], "pack": _pack_ref(SELLER)},
            "referee": named,
        }
        self.seller_terms = copy.deepcopy(self.terms)
        self.seller_terms["price"] = seller_price
        if seller_referee is not None:
            self.seller_terms["referee"] = seller_referee
        req = seal_request("buyer-co", "goose@test", ACTION, referee.action_digest(ACTION, self.terms),
                           ledger=self.buyer_book, extra_compute={"peer": "seller-co"})
        resp = seal_action("seller-co", "goose@test", ACTION, referee.action_digest(ACTION, self.seller_terms),
                           req.capsule_id, ledger=self.seller_book,
                           extra_compute={"peer": "buyer-co", "request_capsule_id": req.capsule_id})
        self.buyer = Party(half=req.capsule, action=ACTION, terms=self.terms, records=buyer_recs,
                           disclosed=load_disclosed(BUYER_RECORDS), withheld=load_withheld(BUYER_RECORDS))
        self.seller = Party(half=resp.capsule, action=ACTION, terms=self.seller_terms, records=seller_recs,
                            disclosed=load_disclosed(SELLER_RECORDS), withheld=load_withheld(SELLER_RECORDS))
        self.legs = self._rail(received, sent_at, received_at) if rail else []

    def _rail(self, received: dict, sent_at: str, received_at: str) -> list[dict]:
        ref = {"type": "acp.order_id", "value": "order-test-1"}
        cite = ReferenceEntry(type="agent-action-capsule", digest_alg="SHA-256",
                              digest=self.seller.half["capsule_id"], citation_purpose="responds_to")

        def leg(book, operator, kind, role, **members):
            member = settlement.build_leg(kind, role, **members)
            return settlement.seal_leg(member, operator=operator, ledger=book, **(
                {"references": [cite]} if kind == "terms" else {})).capsule

        def amt(a):
            return settlement.amount(a["value"], a["assetCode"], a["assetScale"])

        terms = leg(self.seller_book, "seller-co", "terms", "payee", amount=amt(PRICE))
        tref = terms["capsule_id"]
        delivery = {"content_digest": "ab" * 32}
        return [
            terms,
            leg(self.buyer_book, "buyer-co", "payer_observed", "payer", terms_ref=tref, amount=amt(PRICE),
                payment_ref=ref, status="settled", observed_at="2026-11-01T10:00:00Z"),
            leg(self.seller_book, "seller-co", "payee_observed", "payee", terms_ref=tref, received=amt(received),
                receive_fee=amt({**PRICE, "value": "0"}), payment_ref=ref, status="settled", observed_at="2026-11-01T10:01:00Z"),
            leg(self.seller_book, "seller-co", "delivered", "payee", terms_ref=tref, observed_at=sent_at,
                delivery={**delivery, "direction": "sent"}),
            leg(self.buyer_book, "buyer-co", "delivered", "payer", terms_ref=tref, observed_at=received_at,
                delivery={**delivery, "direction": "received"}),
        ]

    def judge(self, **kw) -> dict:
        return referee.judge(self.buyer, self.seller, self.legs, as_of=AS_OF,
                             referee_key_id=kw.get("referee_key_id", self.referee_key))


def _claims(doc: dict) -> dict[str, dict]:
    return {c["id"]: c for c in doc["claims"]}


def _cited(claim: dict) -> set[str]:
    return {e["digest"] for e in claim["evidence"]}


def test_an_honest_deal_meets_every_shared_term_and_judges_both_packs(tmp_path):
    deal = Deal(tmp_path)
    doc = deal.judge()
    claims = _claims(doc)
    for req in referee.DEAL_TERMS_REQUIREMENTS:
        claim = claims[f"deal-terms/{req}"]
        assert claim["verdict"] == "met", claim
        assert claim["contract_ref"] == "ec:deal-terms@1"
        assert {deal.buyer.half["capsule_id"], deal.seller.half["capsule_id"]} <= _cited(claim)
    assert any(c["contract_ref"] == "asg/everyday@0.3.9" and c["verdict"] in ("met", "not_met") for c in doc["claims"])
    assert any(c["contract_ref"] == "asg/seller@0.1.6" and c["verdict"] in ("met", "not_met") for c in doc["claims"])
    assert doc["aggregate"]["coverage"]["evaluated_population"] == len(doc["claims"])


def test_rerunning_gives_identical_bytes(tmp_path):
    deal = Deal(tmp_path)
    assert jcs(deal.judge()) == jcs(deal.judge())


def test_a_half_whose_price_disagrees_with_the_rail_is_not_met_citing_both_halves(tmp_path):
    deal = Deal(tmp_path, seller_price={**PRICE, "value": "2500"})
    claim = _claims(deal.judge())["deal-terms/price-agreed"]
    assert claim["verdict"] == "not_met"
    assert claim["presentation"]["status"] == "CONTRADICTED"
    assert {deal.buyer.half["capsule_id"], deal.seller.half["capsule_id"]} <= _cited(claim)


def test_a_received_amount_that_disagrees_with_the_terms_is_not_met_citing_the_leg(tmp_path):
    deal = Deal(tmp_path, received={**PRICE, "value": "1500"})
    claim = _claims(deal.judge())["deal-terms/price-agreed"]
    assert claim["verdict"] == "not_met"
    payee = next(c for c in deal.legs if c["settlement"]["leg"] == "payee_observed")
    assert payee["capsule_id"] in _cited(claim)


def test_release_before_payment_is_not_met_on_paid_before_release(tmp_path):
    deal = Deal(tmp_path, sent_at="2026-11-01T09:59:00Z")
    claims = _claims(deal.judge())
    assert claims["deal-terms/paid-before-release"]["verdict"] == "not_met"
    sent = next(c for c in deal.legs if c["settlement"]["leg"] == "delivered"
                and c["settlement"]["delivery"]["direction"] == "sent")
    assert sent["capsule_id"] in _cited(claims["deal-terms/paid-before-release"])
    assert claims["deal-terms/price-agreed"]["verdict"] == "met"


def test_a_delivery_after_the_expiry_is_not_met(tmp_path):
    claims = _claims(Deal(tmp_path, received_at="2026-11-01T11:00:01Z").judge())
    assert claims["deal-terms/delivered-before-expiry"]["verdict"] == "not_met"
    assert claims["deal-terms/paid-before-release"]["verdict"] == "met"


def test_without_a_rail_proof_the_rail_terms_are_not_evaluable_never_not_met(tmp_path):
    claims = _claims(Deal(tmp_path, rail=False).judge())
    for req in ("price-agreed", "delivered-before-expiry", "paid-before-release"):
        assert claims[f"deal-terms/{req}"]["verdict"] == "not_evaluable"
        assert claims[f"deal-terms/{req}"]["sufficiency"] == "INSUFFICIENT"
    assert claims["deal-terms/item-agreed"]["verdict"] == "met"


def test_no_party_is_judged_under_the_other_partys_pack(tmp_path):
    doc = Deal(tmp_path).judge()
    for c in doc["claims"]:
        if c["id"].startswith("buyer/"):
            assert c["contract_ref"] == "asg/everyday@0.3.9", c["id"]
        elif c["id"].startswith("seller/"):
            assert c["contract_ref"] == "asg/seller@0.1.6", c["id"]
        else:
            assert c["contract_ref"] == "ec:deal-terms@1", c["id"]
    buyer_records = {r["capsule_id"] for r in load_records(BUYER_RECORDS)}
    seller_records = {r["capsule_id"] for r in load_records(SELLER_RECORDS)}
    for c in doc["claims"]:
        if c["id"].startswith("buyer/") and c["verdict"] != "not_evaluable":
            assert c["evidence"][0]["digest"] in buyer_records
        if c["id"].startswith("seller/") and c["verdict"] != "not_evaluable":
            assert c["evidence"][0]["digest"] in seller_records


def test_declared_not_measured_rules_are_not_evaluable_and_name_their_field(tmp_path):
    claims = _claims(Deal(tmp_path).judge())
    declared = {o.id: o for o in EVERYDAY.obligations if o.measurability == "declared_not_measured"}
    assert {"r14-message-that-commits-the-user", "r24-outside-instructions-are-not-the-users",
            "r25-no-bypassing-safeguards"} <= set(declared)
    for rule, obligation in declared.items():
        claim = claims[f"buyer/{rule}"]
        assert (claim["sufficiency"], claim["verdict"]) == ("GAP", "not_evaluable")
        assert obligation.evidence_instrument.field in claim["presentation"]["summary"]


def test_a_referee_the_terms_do_not_name_is_refused(tmp_path):
    deal = Deal(tmp_path)
    with pytest.raises(RefereeError) as exc:
        deal.judge(referee_key_id=_key(deal.other_book))
    assert exc.value.code == "not_named_referee"


def test_halves_naming_different_referees_are_refused(tmp_path):
    other = {"key_id": "cd" * 32, "mailbox": "nostr:someone-else"}
    with pytest.raises(RefereeError) as exc:
        Deal(tmp_path, seller_referee=other).judge()
    assert exc.value.code == "referee_disagrees"


def test_records_not_signed_by_the_partys_book_key_are_refused(tmp_path):
    deal = Deal(tmp_path)
    swapped = Party(half=deal.buyer.half, action=ACTION, terms=deal.terms, records=deal.seller.records,
                    disclosed=deal.seller.disclosed, withheld=deal.seller.withheld)
    with pytest.raises(RefereeError) as exc:
        referee.judge(swapped, deal.seller, deal.legs, as_of=AS_OF, referee_key_id=deal.referee_key)
    assert exc.value.code == "records_foreign"


def test_an_opening_that_does_not_open_its_half_is_refused(tmp_path):
    deal = Deal(tmp_path)
    lying = Party(half=deal.buyer.half, action=ACTION, terms={**deal.terms, "item": "something else"},
                  records=deal.buyer.records, disclosed=deal.buyer.disclosed, withheld=deal.buyer.withheld)
    with pytest.raises(RefereeError) as exc:
        referee.judge(lying, deal.seller, deal.legs, as_of=AS_OF, referee_key_id=deal.referee_key)
    assert exc.value.code == "opening_mismatch"


def test_a_pack_the_catalog_does_not_hold_at_that_digest_is_refused(tmp_path):
    deal = Deal(tmp_path)
    terms = copy.deepcopy(deal.terms)
    terms["buyer"]["pack"]["definition_digest"] = "00" * 32
    half = seal_request("buyer-co", "goose@test", ACTION, referee.action_digest(ACTION, terms),
                        ledger=deal.buyer_book).capsule
    seller_half = seal_action("seller-co", "goose@test", ACTION, referee.action_digest(ACTION, deal.seller_terms),
                              half["capsule_id"], ledger=deal.seller_book,
                              extra_compute={"request_capsule_id": half["capsule_id"]}).capsule
    buyer = Party(half=half, action=ACTION, terms=terms, records=deal.buyer.records,
                  disclosed=deal.buyer.disclosed, withheld=deal.buyer.withheld)
    seller = Party(half=seller_half, action=ACTION, terms=deal.seller_terms, records=deal.seller.records,
                   disclosed=deal.seller.disclosed, withheld=deal.seller.withheld)
    with pytest.raises(RefereeError) as exc:
        referee.judge(buyer, seller, [], as_of=AS_OF, referee_key_id=deal.referee_key)
    assert exc.value.code == "pack_not_held"


def test_a_sealed_result_verifies_for_the_party_and_one_from_another_key_is_refused(tmp_path):
    deal = Deal(tmp_path)
    doc = deal.judge()
    halves = dict(buyer_half=deal.buyer.half, seller_half=deal.seller.half)
    terms = dict(buyer_terms=deal.terms, seller_terms=deal.seller_terms)
    sealed = referee.seal_result(doc, ledger=deal.referee_book, operator="referee-co", **halves).capsule
    assert sealed["key_id"] == deal.referee_key
    referee.verify_referee_result(sealed, doc, **halves, **terms)

    forged = referee.seal_result(doc, ledger=deal.other_book, operator="referee-co", **halves).capsule
    with pytest.raises(RefereeError) as exc:
        referee.verify_referee_result(forged, doc, **halves, **terms)
    assert exc.value.code == "not_named_referee"

    altered = copy.deepcopy(doc)
    item = next(c for c in altered["claims"] if c["id"] == "deal-terms/item-agreed")
    item["verdict"] = "not_met"
    with pytest.raises(RefereeError) as exc:
        referee.verify_referee_result(sealed, altered, **halves, **terms)
    assert exc.value.code == "result_mismatch"

    tampered = copy.deepcopy(sealed)
    tampered["operator"] = "someone-else"
    with pytest.raises(RefereeError) as exc:
        referee.verify_referee_result(tampered, doc, **halves, **terms)
    assert exc.value.code == "result_unverified"


def _dispute(deal: Deal, tmp_path: Path) -> Path:
    path = tmp_path / "dispute.json"

    def side(party: Party, bundles) -> dict:
        return {"half": party.half, "action": party.action, "terms": party.terms,
                "bundles": [str(b) for b in bundles]}

    path.write_text(json.dumps({"buyer": side(deal.buyer, BUYER_RECORDS), "seller": side(deal.seller, SELLER_RECORDS),
                                "rail": deal.legs}))
    return path


def test_the_cli_seals_the_result_in_the_referees_book(tmp_path, capsys):
    deal = Deal(tmp_path)
    out = tmp_path / "result.json"
    code = main(["referee", "judge", "--dispute", str(_dispute(deal, tmp_path)), "--ledger", deal.referee_book,
                 "--as-of", AS_OF, "--out", str(out)])
    printed = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert code == 0 and printed["ok"], printed
    doc = json.loads(out.read_bytes())
    assert out.read_bytes() == jcs(deal.judge())
    sealed = next(r for r in read_ledger(deal.referee_book) if r.get("capsule_id") == printed["capsule_id"])
    referee.verify_referee_result(sealed, doc, buyer_half=deal.buyer.half, seller_half=deal.seller.half,
                                  buyer_terms=deal.terms, seller_terms=deal.seller_terms)


def test_the_cli_refuses_under_a_key_the_terms_do_not_name(tmp_path, capsys):
    deal = Deal(tmp_path)
    out = tmp_path / "result.json"
    code = main(["referee", "judge", "--dispute", str(_dispute(deal, tmp_path)), "--ledger", deal.other_book,
                 "--as-of", AS_OF, "--out", str(out)])
    printed = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert code == 1 and printed == {"ok": False, "code": "not_named_referee", "error": printed["error"]}
    assert not out.exists()


def test_the_cli_reports_a_malformed_dispute_file_as_a_usage_error(tmp_path):
    bad = tmp_path / "dispute.json"
    bad.write_text("[]")
    assert main(["referee", "judge", "--dispute", str(bad), "--ledger", str(tmp_path / "book"),
                 "--as-of", AS_OF, "--out", str(tmp_path / "r.json")]) == 2
