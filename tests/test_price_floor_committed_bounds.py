# SPDX-License-Identifier: Apache-2.0
"""price_floor/2.0.0: the floor opened from a commitment, never read in clear.

The task authority seals only ``body.bounds_commitment``; the user's own
checker gets the ``commercial_bounds_opening`` (the commercial-bounds/v0
document, its nonce and the commitment) beside the action. The golden
vectors are capsulectl's own (``fixtures/commercial-bounds/vectors.json``,
from capsule-cli ``skills/deal/profile/fixtures/commercial-bounds-vectors.json``
at commit 553e5414e480a55ddb3f39eedd2b2af85bfb6e32, sha256
4b85fb3ea7bd7735c93e2f1dd8605e6c8cc40a279100698ecda989366fe7c4e2; capsule-cli
checks them in ``TestCommercialBoundsVectorsMatch``).
"""
from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
import re
from pathlib import Path
from typing import TypedDict

import pytest
from agent_action_capsule.canonical import jcs

from capsule_engine.folds.loader import load_definition_file as load_fold
from capsule_engine.guards import Action, GuardEngine
from capsule_engine.guards.capsule import ALLOW, DENY, ESCALATE, not_applicable_evidence
from capsule_engine.guards.checks import (
    CommercialBoundsOpening,
    TaskAuthorityRecord,
    bounds_commitment,
    check_price_floor,
    task_authority_record_digest,
)
from capsule_engine.guards.checks.price_floor import CommercialBoundsDocument, OpeningMismatchEvidence
from capsule_engine.guards.engine import GuardDecision
from capsule_engine.guards.wickets import WicketDefinition, load_definition_file

ROOT = Path(__file__).parent.parent / "capsule_engine"
FLOOR = load_definition_file(ROOT / "guards" / "wickets" / "catalog_defs" / "price_floor.yaml")
SPEND = load_fold(ROOT / "folds" / "catalog_defs" / "spend.weekly.v3.yaml")
VECTORS = Path(__file__).parent / "fixtures" / "commercial-bounds" / "vectors.json"
VECTORS_SHA256 = "4b85fb3ea7bd7735c93e2f1dd8605e6c8cc40a279100698ecda989366fe7c4e2"
CLASSES = FLOOR.config["action_classes"]
SINGLE_COMMITMENT = load_definition_file(ROOT / "guards" / "wickets" / "catalog_defs" / "single_commitment.seller.yaml")
PROMISE_NEVER = load_definition_file(ROOT / "guards" / "wickets" / "catalog_defs" / "promise_never.yaml")


class GoldenVector(TypedDict):
    document: CommercialBoundsDocument
    nonce: str
    text: str
    bounds_commitment: str


# Reads the vendored JSON vectors: the test's decoding boundary.
def _vectors() -> list[GoldenVector]:
    return json.loads(VECTORS.read_text())["vectors"]


def _opening(vector: GoldenVector) -> CommercialBoundsOpening:
    return {"document": vector["document"], "nonce": vector["nonce"], "bounds_commitment": vector["bounds_commitment"]}


def _authority(commitment: object) -> TaskAuthorityRecord:
    return {"type": "task-authority/v0",
            "body": {"outcome_id": "household.sell_the_bicycle/1.0.0", "allowed_actions": ["offer", "sell"],
                     "preconditions": [], "bounds_commitment": commitment}}


BICYCLE_VECTOR = _vectors()[0]
FLOOR_MINOR = BICYCLE_VECTOR["document"]["min_total_minor"]
OPENING = _opening(BICYCLE_VECTOR)
AUTHORITY = _authority(BICYCLE_VECTOR["bounds_commitment"])
AUTHORITY_REF = task_authority_record_digest(AUTHORITY)
SEALED = BICYCLE_VECTOR["bounds_commitment"]


def _action(amount_minor: int, **overrides) -> Action:
    fields = dict(verb="offer", operator="household", developer="assistant@v1", action_class="marketplace.offer",
                  amount_minor=amount_minor, currency="USD", target="buyer/ref-7",
                  timestamp="2026-10-08T09:00:00Z", task_authority_ref=AUTHORITY_REF)
    fields.update(overrides)
    return Action(**fields)


def _check(amount_minor: int, opening: object = OPENING, record: TaskAuthorityRecord = AUTHORITY):
    # ``opening`` is typed ``object``: the negative cases hand the check what a
    # checker input could decode to, not only the agreed shape.
    action = _action(amount_minor, task_authority_ref=task_authority_record_digest(record))
    return check_price_floor(action, record, opening, action_classes=CLASSES).constraint


def _mismatch(record: TaskAuthorityRecord = AUTHORITY) -> OpeningMismatchEvidence:
    return OpeningMismatchEvidence(task_authority_ref=task_authority_record_digest(record),
                                   bounds_commitment=record["body"]["bounds_commitment"], opens_commitment=False)


# -- the golden vectors ---------------------------------------------------------


def test_the_vendored_vectors_are_the_producers_bytes():
    assert hashlib.sha256(VECTORS.read_bytes()).hexdigest() == VECTORS_SHA256


@pytest.mark.parametrize("vector", _vectors(), ids=lambda v: str(v["document"]["min_total_minor"]))
def test_every_golden_vector_recomputes_byte_for_byte(vector):
    assert jcs(vector["document"]).decode("utf-8") == vector["text"]
    assert bounds_commitment(vector["nonce"], vector["document"]) == vector["bounds_commitment"]


@pytest.mark.parametrize("vector", _vectors(), ids=lambda v: str(v["document"]["min_total_minor"]))
def test_every_golden_vector_opens_and_enforces_its_floor(vector):
    floor = vector["document"]["min_total_minor"]
    record = _authority(vector["bounds_commitment"])
    assert _check(floor, _opening(vector), record).result == "pass"
    if floor > 0:
        below = _check(floor - 1, _opening(vector), record)
        assert (below.result, below.evidence["below_floor"]) == ("fail", True)


# -- a matching opening enforces the floor --------------------------------------


def test_below_the_floor_fails_and_at_or_above_passes():
    assert _check(FLOOR_MINOR - 1).result == "fail"
    assert _check(FLOOR_MINOR).result == "pass"
    assert _check(FLOOR_MINOR + 5_000).result == "pass"


# -- an opening that does not open the sealed commitment fails ------------------


def _tampered_floor() -> CommercialBoundsOpening:
    out = copy.deepcopy(OPENING)
    out["document"]["min_total_minor"] = 100_000
    return out


def _tampered_nonce() -> CommercialBoundsOpening:
    return {**OPENING, "nonce": "0" * 64}


def _restated_commitment() -> CommercialBoundsOpening:
    """An opening for a lower floor that recomputes, restating its own
    commitment: it still is not the one the authority seals."""
    return _opening(_vectors()[1])


# Out of the agreed shape on purpose: what a checker input could decode to.
def _no_nonce() -> dict[str, object]:
    return {k: v for k, v in OPENING.items() if k != "nonce"}


MISMATCHES = {
    "floor lowered": _tampered_floor(),
    "nonce replaced": _tampered_nonce(),
    "another floor's opening": _restated_commitment(),
    "nonce missing": _no_nonce(),
    "nonce upper case": {**OPENING, "nonce": OPENING["nonce"].upper()},
    "document missing": {k: v for k, v in OPENING.items() if k != "document"},
    "not an object": [OPENING],
    "stated commitment differs": {**OPENING, "bounds_commitment": "1" * 64},
}


@pytest.mark.parametrize("opening", MISMATCHES.values(), ids=MISMATCHES.keys())
def test_an_opening_that_does_not_open_the_sealed_commitment_fails_naming_it(opening):
    out = _check(FLOOR_MINOR + 5_000, opening)
    assert out.result == "fail"
    assert out.evidence == _mismatch()
    assert "bounds_commitment" in out.reason


# Out of the agreed shape on purpose: ``document`` is any decoded JSON object.
def _sealed_document(document: dict[str, object]) -> tuple[dict[str, object], TaskAuthorityRecord]:
    """An opening of ``document`` and an authority sealing its commitment:
    the commitment holds, so only the document's own shape can fail it."""
    nonce = BICYCLE_VECTOR["nonce"]
    commitment = bounds_commitment(nonce, document)
    return {"document": document, "nonce": nonce, "bounds_commitment": commitment}, _authority(commitment)


@pytest.mark.parametrize("document", [
    {"type": "commercial-bounds/v1", "min_total_minor": FLOOR_MINOR},
    {"min_total_minor": FLOOR_MINOR},
    {"type": "commercial-bounds/v0", "min_total_minor": -1},
    {"type": "commercial-bounds/v0", "min_total_minor": True},
    {"type": "commercial-bounds/v0", "min_total_minor": "170000"},
    {"type": "commercial-bounds/v0"},
], ids=["other type", "no type", "negative", "bool", "string", "no floor"])
def test_an_opened_document_without_a_floor_in_shape_fails(document):
    opening, record = _sealed_document(document)
    out = _check(FLOOR_MINOR + 5_000, opening, record)
    assert (out.result, out.evidence) == ("fail", _mismatch(record))


@pytest.mark.parametrize("value", [170_000.5, 2**53, "\ud800"], ids=["float", "unsafe integer", "lone surrogate"])
def test_a_document_with_no_jcs_digest_fails_and_does_not_raise(value):
    opening = copy.deepcopy(OPENING)
    opening["document"]["min_total_minor"] = value
    out = _check(FLOOR_MINOR + 5_000, opening)
    assert (out.result, out.evidence) == ("fail", _mismatch())


# -- a floor in clear on the record is never read -------------------------------


def test_a_floor_in_clear_beside_the_commitment_is_never_read():
    """A record that seals the commitment and also a min_total_minor in
    clear: without an opening it is n/a, and with one the opened floor
    decides, whatever the clear one says."""
    both: TaskAuthorityRecord = copy.deepcopy(AUTHORITY)
    both["body"]["min_total_minor"] = FLOOR_MINOR + 50_000
    out = _check(FLOOR_MINOR + 10_000, None, both)
    assert (out.result, out.evidence) == (
        "n/a", not_applicable_evidence("price_floor", in_scope=True, missing_field="commercial_bounds_opening"))
    assert _check(FLOOR_MINOR + 10_000, OPENING, both).result == "pass"


# -- no opening is n/a ----------------------------------------------------------


def test_no_opening_is_n_a_naming_the_field():
    out = _check(1, None)
    assert (out.result, out.evidence) == (
        "n/a", not_applicable_evidence("price_floor", in_scope=True, missing_field="commercial_bounds_opening"))


# -- the engine: below the floor asks, and nothing private is sealed ------------


def _engine(store, signer, definition: WicketDefinition = FLOOR) -> GuardEngine:
    # The pack declares price_floor ASK (``packs/install.py`` ``ask_wickets``).
    return GuardEngine(ledger=store, caps_fold=SPEND, signer_provider=lambda: signer, wickets=(definition,),
                       ask_wickets=frozenset({"price_floor"}))


# (amount, opening, price_floor result, whether the decision holds the offer)
CASES = {
    "above": (175_000, OPENING, "pass", False),
    "at": (FLOOR_MINOR, OPENING, "pass", False),
    "below": (168_000, OPENING, "fail", True),
    "tampered": (175_000, _tampered_floor(), "fail", True),
    "absent": (168_000, None, "n/a", False),
}


def _decide(store, signer, definition: WicketDefinition = FLOOR, action_class: str = "marketplace.offer"):
    engine = _engine(store, signer, definition)
    # One buyer per case, so no case repeats another's act.
    return {name: engine.check(_action(amount, action_id=f"offer/{name}", target=f"buyer/{name}",
                                       action_class=action_class),
                               task_authority_record=AUTHORITY, commercial_bounds_opening=opening)
            for name, (amount, opening, _, _) in CASES.items()}


def _results(decisions: dict[str, GuardDecision]) -> dict[str, tuple[str, str]]:
    return {name: (next(c.result for c in d.constraints if c.id == "price_floor"), d.outcome)
            for name, d in decisions.items()}


def test_on_a_class_with_an_approver_below_the_floor_and_a_bad_opening_ask(store, signer):
    """The ASK path itself, on a class the taxonomy gives an approver."""
    asking = dataclasses.replace(FLOOR, config={**FLOOR.config, "action_classes": ["money.purchase"]})
    expected = {name: (result, ESCALATE if held else ALLOW) for name, (_, _, result, held) in CASES.items()}
    assert _results(_decide(store, signer, asking, "money.purchase")) == expected


@pytest.mark.parametrize("action_class", CLASSES)
def test_on_each_seller_class_a_held_offer_asks_the_account_holder(store, signer, action_class):
    """Taxonomy 6 names the account holder as approver on marketplace.offer,
    marketplace.sale and agreement.accept, so a declared-ASK price_floor
    failure on them asks the seller rather than refusing."""
    expected = {name: (result, ESCALATE if held else ALLOW) for name, (_, _, result, held) in CASES.items()}
    assert _results(_decide(store, signer, action_class=action_class)) == expected


def _seller_engine(store, signer) -> GuardEngine:
    return GuardEngine(ledger=store, caps_fold=SPEND, signer_provider=lambda: signer,
                       wickets=(FLOOR, SINGLE_COMMITMENT, PROMISE_NEVER), ask_wickets=frozenset({"price_floor"}))


def _below_floor(engine: GuardEngine, **overrides) -> GuardDecision:
    action = _action(168_000, action_id="offer/below", target="buyer/below", item_ref="item/bicycle", **overrides)
    return engine.check(action, task_authority_record=AUTHORITY, commercial_bounds_opening=OPENING)


def _failing(decision: GuardDecision) -> list[str]:
    return sorted(c.id for c in decision.constraints if c.result == "fail")


def test_an_offer_below_the_floor_asks_citing_price_floor_only(store, signer):
    decision = _below_floor(_seller_engine(store, signer))
    assert decision.outcome == ESCALATE
    assert _failing(decision) == ["price_floor"]
    assert decision.capsule["disposition"]["decision"] == "needs_input"
    assert decision.capsule["disposition"]["verdict_class"] == "hitl_dispatched"


def test_an_offer_below_the_floor_after_an_acceptance_in_the_sale_refuses(store, signer):
    """single_commitment is an integrity check: beside it the floor's ask refuses."""
    engine = _seller_engine(store, signer)
    accepted = engine.check(
        _action(175_000, verb="accept", action_id="accept/a", action_class="agreement.accept", target="buyer/a",
                item_ref="item/bicycle"),
        task_authority_record=AUTHORITY, commercial_bounds_opening=OPENING)
    assert accepted.outcome == ALLOW
    decision = _below_floor(engine)
    assert decision.outcome == DENY
    assert _failing(decision) == ["price_floor", "single_commitment"]


def test_an_offer_below_the_floor_making_a_never_representation_refuses(store, signer):
    decision = _below_floor(_seller_engine(store, signer), representation_class="authenticity")
    assert decision.outcome == DENY
    assert _failing(decision) == ["price_floor", "promise_never"]


def test_a_repeated_offer_below_the_floor_in_the_same_deal_refuses_on_dedupe(store, signer):
    engine = _seller_engine(store, signer)
    assert _below_floor(engine).outcome == ESCALATE
    decision = _below_floor(engine)
    assert decision.outcome == DENY
    assert _failing(decision) == ["dedupe", "price_floor"]


def test_nothing_the_opening_holds_enters_any_record(store, signer):
    """Every decision, its constraints in full, and every capsule the ledger
    holds, serialised: none carries the floor's name, its value, the nonce or
    the document's text. Only the commitment the authority already seals.
    The offer made at the floor is left out: its own amount is the floor."""
    decisions = {name: d for name, d in _decide(store, signer).items() if name != "at"}
    emitted = [json.dumps([dataclasses.asdict(c) for c in d.constraints]) for d in decisions.values()]
    emitted += [json.dumps(d.capsule) for d in decisions.values()]
    emitted += [json.dumps(record.capsule) for record in store.scan()
                if record.capsule.get("asg_payload", {}).get("amount_minor") != FLOOR_MINOR]
    assert len(emitted) > len(decisions) * 2
    # A floor is matched as a whole number, never as digits inside a hex digest.
    floors = [re.compile(rf"(?<![0-9a-f]){n}(?![0-9a-f])")
              for n in (FLOOR_MINOR, _tampered_floor()["document"]["min_total_minor"])]
    for text in emitted:
        for secret in ("min_total_minor", OPENING["nonce"], BICYCLE_VECTOR["text"], "commercial-bounds/v0"):
            assert secret not in text, secret
        for floor in floors:
            assert floor.search(text) is None, floor.pattern
