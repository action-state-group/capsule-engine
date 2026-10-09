# SPDX-License-Identifier: Apache-2.0
"""seller pack acceptance: install in observe mode, run one sale with two
buyer threads through a pack-installed ``GuardEngine``, and check every
declared obligation fires both ways, on pack-attributed records.

Same discipline as ``test_pack_everyday_acceptance.py``: this script
regenerates the pack's checked-in ``fixtures/mini_ledger.jsonl`` and proves
it reproduces byte-for-byte. Run ``python -m tests.test_pack_seller_acceptance``
to rewrite the fixture after an intended change.

The scenario is synthetic and hand-built in the engine's action shape: one
synthetic seller, two synthetic buyers, each named only by an opaque
fingerprint reference. Steps run in order on one ledger, because the
required statement, the acceptance and the single commitment are read from
the history the earlier steps sealed.

Every assertion names the field on the named record.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from capsule_ledger.ledger import LedgerStore

from capsule_engine.guards import Action, GuardDecision, LocalSigner
from capsule_engine.guards.capsule import ALLOW, DENY, ESCALATE
from capsule_engine.guards.checks import (
    AuthorizationRecord,
    CommercialBoundsOpening,
    TaskAuthorityRecord,
    authorization_record_digest,
    task_authority_record_digest,
)
from capsule_engine.packs import build_engine, install_pack, load_pack_dir, record_pack_activation

PACK_DIR = Path(__file__).parent.parent / "capsule_engine" / "packs" / "catalog" / "seller"
FIXTURE_PATH = PACK_DIR / "fixtures" / "mini_ledger.jsonl"

OPERATOR = "seller-fixture"
DEVELOPER = "seller-assistant@v1"
SIGNER_SECRET = b"seller-acceptance-fixture-fixed-key"


def _fingerprint(name: str) -> str:
    """An opaque counterparty reference: a digest of a synthetic name, never the name."""
    return "cp-fp:" + hashlib.sha256(f"seller-fixture/{name}".encode()).hexdigest()


BUYER_A = _fingerprint("buyer-a")
BUYER_B = _fingerprint("buyer-b")
# A third buyer who was never told the item's condition.
BUYER_C = _fingerprint("buyer-c")
ITEM = "item-ref:" + hashlib.sha256(b"seller-fixture/item-1").hexdigest()
# A second item the same user sells, accepted by buyer B: its acceptance is
# a sealed acceptance, but of another sale by another buyer.
ITEM_2 = "item-ref:" + hashlib.sha256(b"seller-fixture/item-2").hexdigest()

# The user's private floor, 1,700.00: the record seals only its salted
# commitment, and the opening is supplied beside each decision. This is
# capsulectl's first commercial-bounds golden vector, the same one
# tests/test_seller_wickets.py uses.
OPENING: CommercialBoundsOpening = {
    "document": {"type": "commercial-bounds/v0", "min_total_minor": 170_000},
    "nonce": "43a3c8a2914ecd228f722ed9f47dc4b4005cc1e257bc64777c0635a41ba7ea67",
    "bounds_commitment": "13563d6685d99e1bd4b5508a85a6ca679331cb3ff60e8516177ccafcfd5b4998",
}
# One sale, one task authority, cited by every thread.
TASK: TaskAuthorityRecord = {
    "type": "task-authority/v0",
    "body": {"outcome_id": "seller.sell_one_item/1.0.0", "allowed_actions": ["offer", "accept", "sell"],
             "preconditions": [], "bounds_commitment": OPENING["bounds_commitment"]},
}
TASK_REF = task_authority_record_digest(TASK)
# The user's word for one warranty statement, sealed and cited by digest.
WARRANTY_APPROVAL: AuthorizationRecord = {"type": "approval/v0", "body": {"representation_class": "warranty"}}
WARRANTY_APPROVAL_REF = authorization_record_digest(WARRANTY_APPROVAL)
# A one-shot approval of sharing the address: it does not release it.
ADDRESS_APPROVAL: AuthorizationRecord = {"type": "approval/v0", "body": {"representation_class": "address"}}
ADDRESS_APPROVAL_REF = authorization_record_digest(ADDRESS_APPROVAL)
APPROVALS = {"statement-warranty-approved": WARRANTY_APPROVAL,
             "address-to-a-before-acceptance-approved": ADDRESS_APPROVAL}

# Steps recorded as real decisions rather than dry runs: only a real
# accepted statement counts as made, and only a real accepted acceptance
# seals the sale.
REAL_RUN = frozenset({"condition-told-to-a", "condition-told-to-b", "acceptance-in-a", "acceptance-in-b-of-item-2"})
# The step whose sealed acceptance later steps cite.
ACCEPTANCE = "acceptance-in-a"
# Buyer B's acceptance, of the other item.
ACCEPTANCE_B = "acceptance-in-b-of-item-2"
# A capsule id no record in the ledger has.
UNKNOWN_MANDATE = "6" * 64


def _signer() -> LocalSigner:
    return LocalSigner(key_id="seller-fixture-key", secret=SIGNER_SECRET)


def _at(minute: int) -> str:
    return f"2026-10-09T10:{minute:02d}:00Z"


# Every other step gives dedupe its own equivalence_key, so no two steps are
# the same act. This pair leaves the key to dedupe's formula, under an agent
# no other step uses, so the second is the first act repeated.
REPEATED = {"equivalence_key": None, "developer": "seller-assistant-repeat@v1"}


def _statement(name: str, minute: int, target: str, cls: str, **fields) -> Action:
    fields.setdefault("equivalence_key", f"tell/{name}")
    fields.setdefault("developer", DEVELOPER)
    return Action(verb="tell", operator=OPERATOR, action_class="communication.send",
                  target=target, representation_class=cls, action_id=f"tell/seller-fixture-{name}",
                  timestamp=_at(minute), **fields)


def _commitment(verb: str, action_class: str, name: str, minute: int, target: str, amount_minor: int,
                **fields) -> Action:
    fields.setdefault("proposal_at", _at(minute - 1))
    fields.setdefault("item_ref", ITEM)
    return Action(verb=verb, operator=OPERATOR, developer=DEVELOPER, action_class=action_class, target=target,
                  amount_minor=amount_minor, currency="USD", task_authority_ref=TASK_REF,
                  equivalence_key=f"{verb}/{name}", action_id=f"{verb}/seller-fixture-{name}",
                  timestamp=_at(minute), **fields)


def _offer(name: str, minute: int, target: str, amount_minor: int, **fields) -> Action:
    return _commitment("offer", "marketplace.offer", name, minute, target, amount_minor, **fields)


def _address(name: str, minute: int, target: str, role: str, **fields) -> Action:
    """The user's address shared in the sale of ``ITEM``; the disclosure
    carries only its class, never the address."""
    fields.setdefault("item_ref", ITEM)
    fields.setdefault("action_class", "disclosure.personal")
    return Action(verb="share_address", operator=OPERATOR, developer=DEVELOPER,
                  target=target, recipient_role=role, representation_class="address", task_authority_ref=TASK_REF,
                  equivalence_key=f"share_address/{name}", action_id=f"share_address/seller-fixture-{name}",
                  timestamp=_at(minute), **fields)


# A step that cites an acceptance names it by a marker; the runner replaces
# it with that acceptance's capsule_id once it is sealed.
CITES_ACCEPTANCE = "<acceptance>"
CITES_ACCEPTANCE_B = "<acceptance-b>"
CITED = {CITES_ACCEPTANCE: ACCEPTANCE, CITES_ACCEPTANCE_B: ACCEPTANCE_B}


def _scenarios() -> list[tuple[str, Action, str]]:
    return [
        # -- before any acceptance ------------------------------------------
        # The listing's required statement, made in each thread (real runs).
        ("condition-told-to-a", _statement("condition-a", 1, BUYER_A, "condition"), ALLOW),
        ("condition-told-to-b", _statement("condition-b", 2, BUYER_B, "condition"), ALLOW),
        # Thread A: a counter above the floor, inside the task, on a fresh proposal.
        ("counter-above-floor-in-a", _offer("counter-a", 3, BUYER_A, 180_000), ALLOW),
        # Thread B: below the floor. price_floor is the sole failure and
        # declares ASK, and marketplace.offer names an approver, so it asks.
        ("offer-below-floor-in-b", _offer("below-floor-b", 4, BUYER_B, 168_000), ESCALATE),
        # A buyer never told the item's condition: the offer cannot proceed
        # as constructed, and an approval would not clear it.
        ("offer-before-required-statement", _offer("no-condition-c", 5, BUYER_C, 180_000), ESCALATE),
        # Acting on a proposal two days old.
        ("offer-on-a-stale-proposal", _offer("stale-a", 6, BUYER_A, 180_000, proposal_at="2026-10-07T10:06:00Z"),
         ESCALATE),
        # A verb the task authority's plan does not allow.
        ("offer-outside-the-task", _commitment("offer_bundle", "marketplace.offer", "bundle-a", 7, BUYER_A, 190_000),
         ESCALATE),
        ("statement-warranty-unapproved", _statement("warranty-unapproved", 8, BUYER_A, "warranty"), ESCALATE),
        ("statement-warranty-approved", _statement("warranty-approved", 9, BUYER_A, "warranty",
                                                   authorized_by=WARRANTY_APPROVAL_REF), ALLOW),
        # A class never made: refused whatever it cites.
        ("statement-authenticity", _statement("authenticity", 10, BUYER_A, "authenticity",
                                              authorized_by=WARRANTY_APPROVAL_REF), DENY),
        # The same act twice: one agent, one verb, one recipient, keyed by
        # dedupe's own formula (no equivalence_key given).
        ("pickup-told-to-a", _statement("pickup-a", 11, BUYER_A, "pickup", **REPEATED), ALLOW),
        ("pickup-told-to-a-again", _statement("pickup-a-again", 12, BUYER_A, "pickup", **REPEATED), DENY),
        # The address to buyer A before the sale is accepted: no acceptance
        # to cite, so release_on_acceptance refuses.
        ("address-to-a-before-acceptance", _address("a-before", 13, BUYER_A, "buyer"), DENY),
        # The same, with the user's one-shot approval of sharing the address:
        # an approval does not release it.
        ("address-to-a-before-acceptance-approved", _address("a-before-approved", 14, BUYER_A, "buyer",
                                                             authorized_by=ADDRESS_APPROVAL_REF), DENY),
        # A disclosure citing a mandate that is not in the ledger.
        ("address-citing-an-unknown-mandate", _address("a-unknown-mandate", 15, BUYER_A, "buyer",
                                                       cited_mandate_capsule_id=UNKNOWN_MANDATE), DENY),
        # A non-consequential class: nothing here measures it.
        ("listing-views-read", Action(verb="read_listing_views", operator=OPERATOR, developer=DEVELOPER,
                                      action_class="info.query", target="listing/own",
                                      equivalence_key="read_listing_views/1",
                                      action_id="read_listing_views/seller-fixture-1", timestamp=_at(16)), ALLOW),
        # -- buyer A accepts --------------------------------------------------
        ("acceptance-in-a", _commitment("accept", "agreement.accept", "accept-a", 17, BUYER_A, 175_000), ALLOW),
        # -- after the acceptance ---------------------------------------------
        # Any commitment for the same sale in thread B is refused.
        ("second-commitment-in-b", _offer("after-accept-b", 18, BUYER_B, 180_000), DENY),
        # Buyer B accepts the other item: a sealed acceptance of another sale.
        ("acceptance-in-b-of-item-2", _commitment("accept", "agreement.accept", "accept-b-item-2", 19, BUYER_B,
                                                  175_000, item_ref=ITEM_2), ALLOW),
        # The address to buyer A, citing A's acceptance: released.
        # recipient_role passes for the buyer and the acceptance re-verifies.
        ("address-to-a-after-acceptance", _address("a-after", 20, BUYER_A, "buyer",
                                                   cited_mandate_capsule_id=CITES_ACCEPTANCE), ALLOW),
        # The address to buyer C, a buyer too, citing A's acceptance: C did
        # not accept, so it is refused though recipient_role passes.
        ("address-to-c-after-acceptance", _address("c-after", 21, BUYER_C, "buyer",
                                                   cited_mandate_capsule_id=CITES_ACCEPTANCE), DENY),
        # The address in A's thread citing B's acceptance (of the other item).
        ("address-to-a-citing-b-acceptance", _address("a-cites-b", 22, BUYER_A, "buyer",
                                                      cited_mandate_capsule_id=CITES_ACCEPTANCE_B), DENY),
        # The address to buyer C with its action class left out: nothing can
        # show it is out of scope, so it is refused rather than passed as n/a.
        ("address-with-no-action-class", _address("c-no-class", 23, BUYER_C, "buyer", action_class=None,
                                                  cited_mandate_capsule_id=CITES_ACCEPTANCE), DENY),
        # The same address to someone who is not the buyer.
        ("address-to-a-third-party", _address("third-party", 24, _fingerprint("someone-else"), "third_party",
                                              cited_mandate_capsule_id=CITES_ACCEPTANCE), DENY),
        # The sale to buyer A, following its own acceptance, on an allowed rail.
        ("sale-to-a-on-an-allowed-rail", _commitment("sell", "marketplace.sale", "sale-a", 25, BUYER_A, 175_000,
                                                     rail="card", cited_mandate_capsule_id=CITES_ACCEPTANCE), ALLOW),
        ("sale-to-a-on-a-rail-not-allowed", _commitment("sell", "marketplace.sale", "sale-a-cheque", 26, BUYER_A,
                                                        175_000, rail="cheque",
                                                        cited_mandate_capsule_id=CITES_ACCEPTANCE), ESCALATE),
    ]


def _run_scenarios(ledger, *, project_dir, decisions: dict[str, GuardDecision] | None = None):
    """Run every step in order; ``decisions``, when given, collects each
    step's ``GuardDecision`` (its unsealed reasons and evidence)."""
    import dataclasses

    installed = install_pack(load_pack_dir(PACK_DIR), project_dir=project_dir, mode="observe")
    signer = _signer()
    engine = build_engine(installed, ledger=ledger, signer_provider=lambda: signer)
    activation = record_pack_activation(
        installed,
        ledger=ledger,
        operator=OPERATOR,
        developer="capsule-init-tool",
        signer=signer,
        timestamp="2026-10-09T10:00:00Z",
        action_id="policy_manifest_activated/seller-fixture-install",
    )
    capsules: dict[str, dict] = {}
    for name, action, expected in _scenarios():
        if action.cited_mandate_capsule_id in CITED:
            cited = capsules[CITED[action.cited_mandate_capsule_id]]["capsule_id"]
            action = dataclasses.replace(action, cited_mandate_capsule_id=cited)
        decision = engine.check(
            action,
            dry_run=name not in REAL_RUN,
            task_authority_record=TASK,
            authorization_record=APPROVALS.get(name),
            commercial_bounds_opening=OPENING,
        )
        if decision.outcome != expected:
            raise AssertionError(f"scenario {name!r}: expected {expected!r}, got {decision.outcome!r} ({decision.reason})")
        capsules[name] = decision.capsule
        if decisions is not None:
            decisions[name] = decision
    return installed, activation, capsules, list(ledger.scan())


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("seller")
    store = LedgerStore(tmp / "ledger")
    decisions: dict[str, GuardDecision] = {}
    try:
        installed, activation, capsules, records = _run_scenarios(store, project_dir=tmp / "project",
                                                                  decisions=decisions)
        verified = {name: store.verify(c["capsule_id"]) for name, c in capsules.items()}
    finally:
        store.close()
    return installed, activation, capsules, records, verified, decisions


# Reads raw capsule or fixture JSON/YAML: the test's decoding boundary.
def _constraint(capsule: dict, constraint_id: str) -> dict:
    (record,) = [c for c in capsule["constraints"] if c["id"] == constraint_id]
    return record


# Reads raw capsule or fixture JSON/YAML: the test's decoding boundary.
def _failing(capsule: dict) -> list[str]:
    return [c["id"] for c in capsule["constraints"] if c["result"] == "fail"]


def test_every_declared_scenario_ran_at_its_declared_outcome(run):
    installed, _, capsules, records, verified, _ = run
    declared = {s.id: s.outcome for s in installed.pack.fixtures.scenarios}
    assert list(declared) == list(capsules)
    for name, result in verified.items():
        assert result.ok, f"{name}: {[f.detail for f in result.findings]}"
    assert len(records) == 1 + len(capsules)


def test_records_are_pack_attributed_and_observe_mode(run):
    installed, activation, capsules, _, _, _ = run
    for name, capsule in capsules.items():
        assert capsule["asg_payload"]["manifest_digest"] == installed.resolved.manifest_digest, name
        assert capsule["asg_payload"]["checkpoint"].get("dry_run") is (True if name not in REAL_RUN else None), name
    assert activation["asg_payload"]["detail"]["packs"] == [
        {"pack_id": "asg/seller/0.1.1", "digest": installed.pack.definition_digest(), "mode": "observe"}
    ]


# -- the acceptance criteria, each on its named record ---------------------------


# release_on_acceptance's own reasons: fixed sentences, never a value.
RELEASE_REASONS = {
    "the disclosure cites no acceptance",
    "the disclosure does not cite this sale's sealed acceptance by this recipient",
    "the disclosure cites this sale's sealed acceptance by this recipient",
    "the action class has no taxonomy row; it cannot be shown to be outside the rule",
}


def _release(run, name: str):
    (outcome,) = [c for c in run[5][name].constraints if c.id == "release_on_acceptance"]
    return outcome


def _refused_on_release_alone(run, name: str) -> None:
    capsule = run[2][name]
    assert _constraint(capsule, "recipient_role")["result"] == "pass"
    assert _failing(capsule) == ["release_on_acceptance"]
    assert capsule["disposition"]["decision"] == "reject"
    # The decision's reason names the rule that refused it.
    assert run[5][name].outcome == DENY
    assert "release_on_acceptance=fail" in run[5][name].reason


def test_the_address_before_acceptance_is_refused_by_the_release_rule(run):
    _refused_on_release_alone(run, "address-to-a-before-acceptance")
    assert _release(run, "address-to-a-before-acceptance").reason == "the disclosure cites no acceptance"


def test_a_one_shot_approval_does_not_release_the_address(run):
    name = "address-to-a-before-acceptance-approved"
    assert run[2][name]["asg_payload"]["authorized_by"] == ADDRESS_APPROVAL_REF
    _refused_on_release_alone(run, name)


def test_the_address_after_acceptance_is_released_to_the_buyer_who_accepted(run):
    capsules = run[2]
    capsule = capsules["address-to-a-after-acceptance"]
    assert capsule["asg_payload"]["recipient_role"] == "buyer"
    assert _constraint(capsule, "recipient_role")["result"] == "pass"
    assert _constraint(capsule, "verify_before_dispatch")["result"] == "pass"
    assert _constraint(capsule, "release_on_acceptance")["result"] == "pass"
    # The cited acceptance is sealed as the record's chain parent.
    assert capsule["chain"]["parent_capsule_id"] == capsules[ACCEPTANCE]["capsule_id"]
    assert _failing(capsule) == []
    assert capsule["disposition"]["decision"] == "accept"


def test_the_address_to_another_buyer_citing_the_acceptance_is_refused(run):
    name = "address-to-c-after-acceptance"
    assert run[2][name]["asg_payload"]["recipient_role"] == "buyer"
    _refused_on_release_alone(run, name)


def test_the_address_citing_another_buyers_acceptance_is_refused(run):
    capsules = run[2]
    capsule = capsules["address-to-a-citing-b-acceptance"]
    # B's acceptance is a real, re-verifying record; it is just not this sale's by A.
    assert capsules[ACCEPTANCE_B]["disposition"]["decision"] == "accept"
    assert _constraint(capsule, "verify_before_dispatch")["result"] == "pass"
    _refused_on_release_alone(run, "address-to-a-citing-b-acceptance")


def test_the_release_rule_names_no_value(run):
    """Every release_on_acceptance record, pass or fail, holds none of the
    references, counterparties or acceptance ids, and its reason is one of
    the fixed sentences."""
    capsules = run[2]
    values = [BUYER_A, BUYER_B, BUYER_C, _fingerprint("someone-else"), ITEM, ITEM_2, TASK_REF,
              capsules[ACCEPTANCE]["capsule_id"], capsules[ACCEPTANCE_B]["capsule_id"]]
    measured = 0
    for name in capsules:
        outcome = _release(run, name)
        if outcome.result == "n/a":
            continue
        measured += 1
        assert outcome.reason in RELEASE_REASONS, name
        assert set(outcome.evidence) == {"constraint_id", "representation_class", "bound_to_acceptance",
                                         "missing_field"}, name
        serialised = json.dumps({"reason": outcome.reason, "evidence": outcome.evidence})
        for value in values:
            assert value not in serialised, (name, value)
    assert measured == 8


def test_an_address_with_no_action_class_is_refused(run):
    name = "address-with-no-action-class"
    assert _failing(run[2][name]) == ["release_on_acceptance"]
    assert run[2][name]["disposition"]["decision"] == "reject"
    assert _release(run, name).evidence["missing_field"] == "action_class"


def test_the_address_to_anyone_but_the_buyer_is_refused(run):
    capsule = run[2]["address-to-a-third-party"]
    assert _constraint(capsule, "recipient_role")["result"] == "fail"
    assert capsule["disposition"]["decision"] == "reject"


def test_a_below_floor_offer_asks_on_price_floor_alone(run):
    capsule = run[2]["offer-below-floor-in-b"]
    assert _failing(capsule) == ["price_floor"]
    assert capsule["disposition"]["decision"] == "needs_input"
    assert capsule["disposition"]["verdict_class"] == "hitl_dispatched"


def test_the_floor_never_reaches_a_record(run):
    floor = str(OPENING["document"]["min_total_minor"])
    for name, capsule in run[2].items():
        assert floor not in json.dumps(capsule["asg_payload"]), name
    assert OPENING["nonce"] not in FIXTURE_PATH.read_text()


def test_a_statement_never_made_is_refused_even_with_an_approval(run):
    capsule = run[2]["statement-authenticity"]
    assert _failing(capsule) == ["promise_never"]
    assert capsule["disposition"]["decision"] == "reject"


def test_a_second_commitment_in_another_thread_is_refused(run):
    capsules = run[2]
    capsule = capsules["second-commitment-in-b"]
    assert _failing(capsule) == ["single_commitment"]
    assert capsule["disposition"]["decision"] == "reject"
    assert _constraint(capsules["counter-above-floor-in-a"], "single_commitment")["result"] == "pass"


def test_the_sale_following_its_own_acceptance_passes(run):
    capsule = run[2]["sale-to-a-on-an-allowed-rail"]
    assert _constraint(capsule, "single_commitment")["result"] == "pass"
    assert _constraint(capsule, "destination_rail")["result"] == "pass"
    assert _failing(capsule) == []


def test_an_offer_before_the_required_statement_asks_and_is_not_cleared(run):
    capsule = run[2]["offer-before-required-statement"]
    assert _failing(capsule) == ["required_disclosure"]
    assert capsule["disposition"]["decision"] == "needs_input"


def test_a_denial_in_thread_b_names_nothing_from_thread_a(run):
    capsules = run[2]
    record = _constraint(capsules["second-commitment-in-b"], "single_commitment")
    for value in (BUYER_A, ITEM, TASK_REF, capsules[ACCEPTANCE]["capsule_id"]):
        assert value not in json.dumps(record), value


def test_fixture_is_reproducible_byte_for_byte(run):
    _, _, _, records, _, _ = run
    regenerated = [json.dumps(r.capsule, separators=(",", ":")) for r in records]
    assert regenerated == FIXTURE_PATH.read_text().splitlines()


def _regenerate_fixture() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        store = LedgerStore(tmp / "ledger")
        try:
            _, _, _, records = _run_scenarios(store, project_dir=tmp / "project")
        finally:
            store.close()
    FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(FIXTURE_PATH, "w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record.capsule, separators=(",", ":")) + "\n")
    print(f"wrote {len(records)} record(s) to {FIXTURE_PATH}")


if __name__ == "__main__":
    _regenerate_fixture()
