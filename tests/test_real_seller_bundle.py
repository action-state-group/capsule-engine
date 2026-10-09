# SPDX-License-Identifier: Apache-2.0
"""A real seller fixture from capsulectl, under the seller pack.

``fixtures/real-seller/`` holds what capsulectl wrote, byte for byte (``build.sh``
and ``README.md`` there): two sales of one synthetic item, asking 1900 with a
floor of 1700. Sale 1 has two buyer threads: buyer A is told the item's
condition, offered 1900, accepts, and the seller commits; buyer B is offered 1500,
under the floor, and the card is never answered. Sale 2's one buyer, C, accepts
an offer of 1600, under the floor, and the seller's commit is checked. Each
thread's own copy is a bundle, and ``check-inputs/`` holds the
external-check-input/v0 capsulectl gave the pinned rules checker for each check.

The replay reads the three bundles. The live path reads each check input as the
plugin does: ``action_for_check_input`` and ``GuardEngine.check`` with the input's
task-authority record and floor opening. ``expected_decisions.json`` is both, in
sorted canonical JSON, compared byte for byte here and handed to the Go plugin.
Regenerate it with ``python -m tests.test_real_seller_bundle``.
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from functools import cache
from pathlib import Path
from typing import TypedDict

import pytest
from agent_action_capsule import json_digest
from capsule_ledger.ledger import LedgerStore

import capsule_engine
from capsule_engine.guards import ALLOW, ESCALATE, GuardDecision, GuardEngine, LocalSigner
from capsule_engine.guards.checks import RUNNABLE_CHECKS
from capsule_engine.packs import install_pack, load_pack_dir
from capsule_engine.packs.install import engine_ask_sets
from capsule_engine.packs.obligation_results import obligation_results
from capsule_engine.report.replay import (
    ReplayResult,
    action_for_check_input,
    load_disclosed,
    load_records,
    load_withheld,
    replay,
)

FIXTURE = Path(__file__).parent / "fixtures" / "real-seller"
OWN = (FIXTURE / "buyer-a.bundle.json", FIXTURE / "buyer-b.bundle.json", FIXTURE / "buyer-c.bundle.json")
INPUTS = ("a-offer-1900", "a-commit-1900", "b-offer-1500", "c-offer-1600", "c-commit-1600")
EXPECTED = FIXTURE / "expected_decisions.json"
FIXTURE_SHA256 = {
    "buyer-a.bundle.json": "b691898e2d21ac1de86c3f4f748633fbddd82d7a55dd495ec2afef4199d97ce7",
    "buyer-b.bundle.json": "3a81c153bbedffe47384672dfdde71e96515959ceb3b88b2862c0020291beb70",
    "buyer-c.bundle.json": "2c3323a359d5181418f8cfd8290d8c4e04f443f3b2b8d9822e4e64c99a87147e",
    "check-inputs/a-commit-1900.json": "736205088e5bde569cc6b5837f3601c22520ad99857a2177b730ed9fd3bb6c28",
    "check-inputs/a-offer-1900.json": "790ca35c37b0e6356e841e07d7578c4a6948fa0f8d9a25b0edcddbd0aa80fc2f",
    "check-inputs/b-offer-1500.json": "9f6b600f29c799027021992080680293916cf4a9f00027153447507d555678f3",
    "check-inputs/c-commit-1600.json": "6f109b631ae6edee9aec7b280b26b34683b1955583996bd10c3d73d31a1a985c",
    "check-inputs/c-offer-1600.json": "24e0879b98f0d89655e98fc4b4dcf6a0a78fdde5f98029c4e8917850ccb10977",
}
PACK = load_pack_dir(Path(capsule_engine.__file__).parent / "packs" / "catalog" / "seller")
PACK_ID = "asg/seller/0.1.2"
PRODUCER = {"commit": "831afeeb9fd76b7196486a9af38ca455b1230791", "name": "capsulectl", "version": "v0.1.0-rc13-6-g831afee"}
FLOOR = 170000
S03 = "s03-price-below-the-floor"
S06 = "s06-proposal-still-current"
S09 = "s09-no-repeated-act"
PROPOSED = "proposed-action/v0"
TYPED_NO_ACT = ("task-authority/v0", "action-evaluation/v0", "action-approval/v0", "action-record/v0")


class Decision(TypedDict):
    source: str
    capsule_id: str
    verb: str
    action_class: str | None
    stated_amount_minor: int | None
    proposal_at: str | None
    outcome: str
    failing_checks: list[str]
    rules: dict[str, str]


class DecisionsDocument(TypedDict):
    pack: dict[str, str]
    producer: dict[str, str]
    replayed: list[str]
    decisions: list[Decision]


# Reads the vendored fixture: the test's decoding boundary.
def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _shown(path: Path) -> dict[str, dict]:
    """Each disclosed record of a bundle, by capsule id."""
    return {k: m["agent_input"] for k, m in _json(path)["disclosures"].items() if isinstance(m.get("agent_input"), dict)}


def _type(shown: dict) -> str | None:
    block = shown.get("x-deal-v0")
    return block.get("record_type") if isinstance(block, dict) else shown.get("type")


def _body(shown: dict) -> dict:
    return shown.get("body") or {}


def _replay(records: list[dict], disclosed: dict[str, dict]) -> ReplayResult:
    with tempfile.TemporaryDirectory() as tmp:
        installed = install_pack(PACK, project_dir=Path(tmp) / "replay-project", mode="observe")
        resolved = installed.resolved
        return replay(
            records,
            caps_fold=resolved.caps_fold(),
            caps_minor=resolved.caps_minor(),
            per_action_minor=resolved.per_action_minor(),
            per_action_reads=resolved.per_action_reads(),
            manifest_digest=resolved.manifest_digest,
            disclosed=disclosed,
            withheld=load_withheld(OWN),
            wickets=resolved.configured_wickets(RUNNABLE_CHECKS),
            pack=installed.pack,
        )


@cache
def _replayed() -> ReplayResult:
    return _replay(load_records(OWN), load_disclosed(OWN))


def _check_input(name: str) -> dict:
    return _json(FIXTURE / "check-inputs" / f"{name}.json")


@cache
def _live(name: str) -> GuardDecision:
    """The decision on one check input, by a fresh engine under the pack, as
    the plugin makes it."""
    entry = _check_input(name)
    with tempfile.TemporaryDirectory() as tmp, LedgerStore(Path(tmp) / "ledger") as store:
        installed = install_pack(PACK, project_dir=Path(tmp) / "live-project", mode="observe")
        resolved = installed.resolved
        wickets = resolved.configured_wickets(RUNNABLE_CHECKS)
        gates, asks = engine_ask_sets(installed.pack, wickets)
        signer = LocalSigner(key_id="real-seller", secret=b"real-seller-fixture")
        engine = GuardEngine(
            ledger=store, caps_fold=resolved.caps_fold(), signer_provider=lambda: signer,
            caps_minor=resolved.caps_minor() or {}, manifest_digest=resolved.manifest_digest, wickets=wickets,
            ask_gate_selectors=gates, ask_wickets=asks, evaluate_under_record_taxonomy=True,
        )
        action = action_for_check_input(entry["record"], item_ref=entry.get("item_ref"))
        return engine.check(
            action,
            task_authority_record=entry.get("task_authority_record"),
            commercial_bounds_opening=entry.get("commercial_bounds_opening"),
        )


def _decision(source: str, capsule_id: str, decision: GuardDecision, action) -> Decision:
    return {
        "source": source,
        "capsule_id": capsule_id,
        "verb": action.verb,
        "action_class": action.action_class,
        "stated_amount_minor": action.stated_amount_minor,
        "proposal_at": action.proposal_at,
        "outcome": decision.outcome,
        "failing_checks": sorted(c.id for c in decision.constraints if c.result == "fail"),
        "rules": {r.obligation_id: r.result for r in obligation_results(PACK, decision.constraints)},
    }


def decisions_document() -> DecisionsDocument:
    """The replay's decision for every record that gets one, in replay order,
    then the live decision on each check input, in ``INPUTS`` order."""
    decisions = [
        _decision("replay", s.record["capsule_id"], s.decision, s.action) for s in _replayed().decisions
    ]
    for name in INPUTS:
        entry = _check_input(name)
        action = action_for_check_input(entry["record"], item_ref=entry.get("item_ref"))
        decisions.append(_decision(f"check-inputs/{name}.json", entry["record"]["capsule_id"], _live(name), action))
    return {
        "pack": {"pack_id": PACK.pack_id, "definition_digest": PACK.definition_digest()},
        "producer": PRODUCER,
        "replayed": [p.name for p in OWN],
        "decisions": decisions,
    }


def canonical(document: DecisionsDocument) -> str:
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"


def _replayed_check(path: Path, action: str):
    """The replay's decision on the one check of ``action`` in a bundle."""
    (capsule_id,) = [k for k, v in _shown(path).items() if _type(v) == PROPOSED and _body(v)["action"] == action]
    (sourced,) = [s for s in _replayed().decisions if s.record["capsule_id"] == capsule_id]
    return sourced


def _constraint(decision: GuardDecision, check: str):
    (found,) = [c for c in decision.constraints if c.id == check]
    return found


# -- the fixture is capsulectl's bytes ---------------------------------------------


def test_the_fixture_is_the_bytes_capsulectl_wrote():
    for name, digest in FIXTURE_SHA256.items():
        assert hashlib.sha256((FIXTURE / name).read_bytes()).hexdigest() == digest, name


def test_every_deal_record_was_sealed_by_the_pinned_capsulectl():
    blocks = [s["x-deal-v0"] for p in OWN for s in _shown(p).values() if isinstance(s.get("x-deal-v0"), dict)]
    assert blocks
    assert all(block["producer"] == PRODUCER for block in blocks)


def test_the_pack_is_seller_0_1_2():
    assert PACK.pack_id == PACK_ID


def test_the_producer_seals_a_sale_as_spend_0_and_states_its_price():
    for path in OWN:
        for shown in _shown(path).values():
            if _type(shown) == PROPOSED:
                assert _body(shown)["spend_minor"] == 0
                assert _body(shown)["amount_minor"] in (150000, 160000, 190000)


def test_every_check_input_is_a_sellers_and_opens_the_floor():
    for name in INPUTS:
        entry = _check_input(name)
        assert entry["party_role"] == "seller"
        assert entry["commercial_bounds_opening"]["document"]["min_total_minor"] == FLOOR


# -- the replay ----------------------------------------------------------------


def test_the_replay_matches_expected_decisions_byte_for_byte():
    assert canonical(decisions_document()) == EXPECTED.read_text(encoding="utf-8")


def test_only_a_proposed_action_gets_a_decision():
    disclosed = load_disclosed(OWN)
    assert {_type(disclosed[s.record["capsule_id"]]) for s in _replayed().decisions} == {PROPOSED}


def test_every_typed_record_that_states_no_act_gets_none():
    disclosed = load_disclosed(OWN)
    undecided = {_type(disclosed[r["capsule_id"]]) for r in _replayed().undecided if r["capsule_id"] in disclosed}
    for record_type in TYPED_NO_ACT:
        assert record_type in undecided, record_type


def test_each_commit_reads_the_age_of_the_offer_the_buyer_accepted():
    for path in (OWN[0], OWN[2]):
        shown = _shown(path)
        records = {r["capsule_id"]: r for r in load_records([path])}
        (offer_id,) = [k for k, v in shown.items() if _type(v) == PROPOSED and _body(v)["action"] == "offer"]
        sourced = _replayed_check(path, "commit")
        assert sourced.action.proposal_at == records[offer_id]["timestamp"]
        expiry = _constraint(sourced.decision, "offer_expiry")
        assert expiry.result == "pass"
        assert expiry.evidence["proposal_at"] == records[offer_id]["timestamp"]


def test_an_offer_states_its_price_never_its_spend():
    for path, amount in ((OWN[0], 190000), (OWN[1], 150000), (OWN[2], 160000)):
        action = _replayed_check(path, "offer").action
        assert (action.amount_minor, action.stated_amount_minor) == (0, amount)


# -- the same records, changed: a commit with no current accepted offer -----------


def _rebound(capsule: dict, shown: dict, *, at: str) -> tuple[dict, dict]:
    """A copy of ``capsule`` binding ``shown`` by digest, sealed at ``at``. Its
    signature no longer verifies; the replay reads only the binding."""
    copy = json.loads(json.dumps(capsule))
    copy["model_attestation"]["compute_attestation"]["agent_input_digest"] = json_digest(shown)
    copy["timestamp"] = at
    copy["capsule_id"] = json_digest({"rebound": json_digest(shown), "at": at})
    return copy, shown


def _thread_a(*, drop: tuple[str, ...] = (), insert_before_commit: tuple[tuple[dict, dict], ...] = (),
              insert_after_acceptance: tuple[tuple[dict, dict], ...] = ()) -> tuple[list[dict], dict[str, dict]]:
    """Buyer A's records, with the record types in ``drop`` left out and the
    given bound records inserted."""
    disclosed = dict(load_disclosed(OWN))
    out: list[dict] = []
    for capsule in load_records([OWN[0]]):
        shown = disclosed.get(capsule["capsule_id"])
        kind = _type(shown) if shown is not None else None
        if shown is not None and _type(shown) == PROPOSED and _body(shown)["action"] == "commit":
            for extra, extra_shown in insert_before_commit:
                out.append(extra)
                disclosed[extra["capsule_id"]] = extra_shown
        if kind in drop:
            continue
        out.append(capsule)
        if kind == "action-approval/v0":
            for extra, extra_shown in insert_after_acceptance:
                out.append(extra)
                disclosed[extra["capsule_id"]] = extra_shown
    return out, disclosed


def _a_offer() -> tuple[dict, dict]:
    disclosed = load_disclosed(OWN)
    (capsule,) = [c for c in load_records([OWN[0]])
                  if _type(disclosed[c["capsule_id"]]) == PROPOSED
                  and _body(disclosed[c["capsule_id"]])["action"] == "offer"]
    return capsule, disclosed[capsule["capsule_id"]]


def _a_commit_expiry(records: list[dict], disclosed: dict[str, dict]):
    result = _replay(records, disclosed)
    (sourced,) = [s for s in result.decisions
                  if _type(disclosed[s.record["capsule_id"]]) == PROPOSED and s.action.verb == "commit"]
    return sourced.action.proposal_at, _constraint(sourced.decision, "offer_expiry")


def _unverified(proposal_at, expiry) -> bool:
    return (proposal_at is None and expiry.result == "fail" and expiry.evidence["expiry_unverified"] is True
            and expiry.evidence["missing_field"] == "proposal_at")


def test_a_commit_with_no_acceptance_has_no_proposal_to_date():
    assert _unverified(*_a_commit_expiry(*_thread_a(drop=("action-approval/v0",))))


def test_an_offer_after_the_acceptance_supersedes_it():
    capsule, shown = _a_offer()
    later = json.loads(json.dumps(shown))
    later["body"]["amount_minor"] = 185000
    later["body"]["terms"]["price_minor"] = 185000
    extra = _rebound(capsule, later, at="2026-10-09T16:54:27Z")
    assert _unverified(*_a_commit_expiry(*_thread_a(insert_after_acceptance=(extra,))))


def _a_acceptance() -> tuple[dict, dict]:
    disclosed = load_disclosed(OWN)
    (capsule,) = [c for c in load_records([OWN[0]]) if _type(disclosed[c["capsule_id"]]) == "action-approval/v0"]
    return capsule, disclosed[capsule["capsule_id"]]


def test_an_acceptance_of_an_earlier_offer_dates_nothing_once_another_was_made():
    """A second offer, then the acceptance of the first: the acceptance names
    an offer that is no longer the latest."""
    capsule, shown = _a_offer()
    later = json.loads(json.dumps(shown))
    later["body"]["amount_minor"] = 185000
    later["body"]["terms"]["price_minor"] = 185000
    extra = _rebound(capsule, later, at="2026-10-09T16:54:26Z")
    records, disclosed = _thread_a(drop=("action-approval/v0",), insert_before_commit=(extra, _a_acceptance()))
    assert _unverified(*_a_commit_expiry(records, disclosed))


def test_the_acceptance_moved_to_just_before_the_commit_still_dates_it():
    """The control for the test above: the same move, with no second offer."""
    records, disclosed = _thread_a(drop=("action-approval/v0",), insert_before_commit=(_a_acceptance(),))
    proposal_at, expiry = _a_commit_expiry(records, disclosed)
    assert expiry.result == "pass" and proposal_at is not None


def test_a_change_of_details_after_the_acceptance_leaves_it_behind():
    disclosed = load_disclosed(OWN)
    (claim,) = [c for c in load_records([OWN[0]]) if _type(disclosed[c["capsule_id"]]) == "claim"]
    change = json.loads(json.dumps(disclosed[claim["capsule_id"]]))
    change["x-deal-v0"]["record_type"] = "change"
    extra = _rebound(claim, change, at="2026-10-09T16:54:27Z")
    assert _unverified(*_a_commit_expiry(*_thread_a(insert_after_acceptance=(extra,))))


def test_another_deals_accepted_offer_never_dates_this_commit():
    """Buyer C's thread (sale 2) is replayed first, with its accepted offer;
    buyer A's offer and acceptance are left out, so A's commit rests on none."""
    disclosed = dict(load_disclosed(OWN))
    records = list(load_records([OWN[2]]))
    for capsule in load_records([OWN[0]]):
        shown = disclosed[capsule["capsule_id"]] if capsule["capsule_id"] in disclosed else None
        if shown is not None and (_type(shown) == "action-approval/v0"
                                  or (_type(shown) == PROPOSED and _body(shown)["action"] == "offer")):
            continue
        records.append(capsule)
    result = _replay(records, disclosed)
    a_records = {c["capsule_id"] for c in load_records([OWN[0]])}
    (sourced,) = [s for s in result.decisions if s.record["capsule_id"] in a_records and s.action.verb == "commit"]
    assert _unverified(sourced.action.proposal_at, _constraint(sourced.decision, "offer_expiry"))


def test_a_typed_record_of_a_type_not_known_to_state_no_act_still_gets_a_decision():
    disclosed = dict(load_disclosed(OWN))
    (capsule,) = [c for c in load_records([OWN[0]]) if _type(disclosed[c["capsule_id"]]) == "task-authority/v0"]
    unknown = json.loads(json.dumps(disclosed[capsule["capsule_id"]]))
    unknown["type"] = "made-up-record/v0"
    extra, extra_shown = _rebound(capsule, unknown, at=capsule["timestamp"])
    disclosed[extra["capsule_id"]] = extra_shown
    result = _replay([extra], disclosed)
    assert [s.record["capsule_id"] for s in result.decisions] == [extra["capsule_id"]]


# -- the live path: each check input, as the plugin decides it ----------------------


def test_a_commit_at_the_asking_price_passes_the_floor_on_its_stated_price():
    floor = _constraint(_live("a-commit-1900"), "price_floor")
    assert floor.result == "pass"
    assert (floor.evidence["amount_minor"], floor.evidence["below_floor"]) == (190000, False)


def test_a_commit_under_the_floor_asks():
    decision = _live("c-commit-1600")
    floor = _constraint(decision, "price_floor")
    assert floor.result == "fail"
    assert (floor.evidence["amount_minor"], floor.evidence["below_floor"]) == (160000, True)
    assert decision.outcome == ESCALATE


def test_the_floor_is_never_stated():
    for name in INPUTS:
        floor = _constraint(_live(name), "price_floor")
        assert str(FLOOR) not in json.dumps([floor.reason, floor.evidence], sort_keys=True), name


# -- open: each needs the producer, or u286 (see the README) -----------------------


@pytest.mark.xfail(strict=True, reason="capsulectl seals an offer as external_commitment.other, a class "
                   "no seller check names, so price_floor is out of scope on an offer")
def test_an_offer_under_the_floor_asks():
    decision = _live("b-offer-1500")
    assert _constraint(decision, "price_floor").result == "fail"
    assert decision.outcome == ESCALATE


@pytest.mark.xfail(strict=True, reason="a commit's check input carries no proposal_at and no acceptance, "
                   "so offer_expiry cannot date the offer it rests on")
def test_a_live_commit_reads_the_age_of_the_accepted_offer():
    assert _constraint(_live("a-commit-1900"), "offer_expiry").result == "pass"
    assert _live("a-commit-1900").outcome == ALLOW


@pytest.mark.xfail(strict=True, reason="a typed check names no payee (u286) and a sale's spend is 0, so "
                   "dedupe reads offers to different buyers at different prices as one act")
def test_the_real_seller_deals_replay_with_no_repeated_act():
    rules = [r for d in decisions_document()["decisions"] if d["source"] == "replay" for r in [d["rules"]]]
    assert all(r[S09] != "fail" for r in rules)


if __name__ == "__main__":
    sys.stdout.write(canonical(decisions_document()))
