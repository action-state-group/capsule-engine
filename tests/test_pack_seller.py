# SPDX-License-Identifier: Apache-2.0
"""The seller pack, asg/seller/0.1.5: what it cites, at which digest, and
what each rule declares. 0.1.0 to 0.1.4 are kept byte for byte under
``tests/fixtures/packs/seller-0.1.0`` to ``seller-0.1.4`` and still load at
their digests. 0.1.2 is 0.1.1 without the spending limit
it cited only so the engine would run; every scenario decides the same under
both. 0.1.3 is 0.1.2 with s05 also counting a claim the agent made in the
deal (required_disclosure/1.1.0); no scenario here holds a claim, so every
one decides the same under both. 0.1.4 is 0.1.3 with a platform recipient:
s01 passes an email to the platform a sale is listed on and refuses anything
else to it, and s13 does not apply to it; only the email-to-the-platform
scenario decides differently. 0.1.5 is 0.1.4 with the listing's public pickup
area (pickup_area) also passing to a platform (seller.recipient_role/1.2.0),
and with s13 reading the classes of data a share gives and binding the
acceptance from the history (seller.release_on_acceptance/1.2.0), and with
the payment rail checked at the seller's commit too
(seller.destination_rail/1.1.0: cash only in person, the payment apps
counted as p2p, a commit stating no rail asks): the scenarios giving a
platform the pickup area, the shares to a buyer the new s13 releases, and
the rows s07 now checks at a commit or counts a payment app on decide
differently. s06 (offer_expiry/1.1.0) holds an offer that answers no
proposal (no_proposal) not applicable, so two of the proposal scenarios
decide differently too. And s05 also counts the sale's listings in force
(the check's listing_classes, required_disclosure/1.2.0), so the offer
whose listing stated the condition decides differently too.
And s04 reads the classes the user ordered where capsulectl seals them
(promise_requires_approval/1.1.0) and s10 passes the user's own
authenticity claim (promise_never/1.1.0), so an ordered warranty and the
user's own authenticity claim decide differently too.

The scenarios are in ``test_pack_seller_acceptance.py``; this file pins the
pack's own content, so a definition cited at another digest or a rule
re-declared with another disposition goes red here even where a decision
would not change (an integrity check refuses whatever its rule declares).
"""
from __future__ import annotations

import hashlib
import importlib.util
import shutil
from pathlib import Path

import pytest
import yaml
from agent_action_capsule.canonical import json_digest
from cll.ledger import LedgerStore

from capsule_engine.guards import Action, LocalSigner
from capsule_engine.guards.capsule import ALLOW, DENY, not_applicable_evidence
from capsule_engine.guards.checks import AuthorizationRecord, authorization_record_digest
from capsule_engine.guards.classes import TAXONOMY_VERSION
from capsule_engine.guards.wickets import Catalog
from capsule_engine.packs import build_engine, install_pack
from capsule_engine.packs.errors import PackDefinitionError
from capsule_engine.packs.loader import load_pack_dir

ROOT = Path(__file__).parent.parent / "capsule_engine"
PACK_DIR = ROOT / "packs" / "catalog" / "seller"
FROZEN_0_1_0_DIR = Path(__file__).parent / "fixtures" / "packs" / "seller-0.1.0"
PACK_0_1_0_DIGEST = "7b854f0f0feb51381d936ad992bef659234d77a799a2a319585a856f65a19c77"
PACK_0_1_0_FILE_SHA256 = "b03b7ebb491cdd250bedfba47fcaaa0e60f0316a88a2698db7b886ef5f4753e6"
FROZEN_0_1_1_DIR = Path(__file__).parent / "fixtures" / "packs" / "seller-0.1.1"
PACK_0_1_1_DIGEST = "65e41f7e9a2a9cffc0f71027f8e7c5b70f3ccf2751e6cb95ba1c81649b41e1ac"
PACK_0_1_1_FILE_SHA256 = "edb54be219fdcd31602c5a8eebe159e31d8107e29a13a80496c7ed952972688e"
FROZEN_0_1_2_DIR = Path(__file__).parent / "fixtures" / "packs" / "seller-0.1.2"
PACK_0_1_2_DIGEST = "e599c5aff8d4171d5e58cae33fdc7022e4ee3762d1a884bb6e6098aca532a353"
PACK_0_1_2_FILE_SHA256 = "6174f336f929bfce8bc64bbe34ddab71d57de2951766561796d8685f9dc39f35"
FROZEN_0_1_3_DIR = Path(__file__).parent / "fixtures" / "packs" / "seller-0.1.3"
PACK_0_1_3_DIGEST = "f0068688317b0cae3c40e839d7cff0137991aca466a878f9b558356f1808321a"
PACK_0_1_3_FILE_SHA256 = "4fc21dd877f8858786aad3bd219f1b8b6d5bc328e7447ae2d20a38137616917e"
FROZEN_0_1_4_DIR = Path(__file__).parent / "fixtures" / "packs" / "seller-0.1.4"
PACK_0_1_4_DIGEST = "c14c8f8176a933f97c8a04064f6634f8f0e422178f71c4a531ac01631f529e5b"
PACK_0_1_4_FILE_SHA256 = "c45e7a7d31c1910dd487137056a6fb6564be1664ad267aeb20d8f6c43cb58e72"
CATALOG = Catalog(ROOT / "guards" / "wickets" / "catalog_defs")

PACK = load_pack_dir(PACK_DIR)

# Every definition the pack cites, by id.
CITED = [
    "dedupe/1.0.0",
    "verify_before_dispatch/1.0.0",
    "seller.recipient_role/1.2.0",
    "price_floor/2.0.1",
    "promise_requires_approval/1.1.0",
    "promise_never/1.1.0",
    "required_disclosure/1.2.0",
    "offer_expiry/1.1.0",
    "seller.single_commitment/1.0.0",
    "seller.destination_rail/1.1.0",
    "task_authority/1.1.0",
    "seller.release_on_acceptance/1.2.0",
]

# check -> the disposition its one rule declares.
DECLARED = {
    "recipient_role": "DO",
    "price_floor": "ASK",
    "promise_requires_approval": "ASK",
    "required_disclosure": "ASK",
    "offer_expiry": "ASK",
    "destination_rail": "ASK",
    "task_authority": "ASK",
    "dedupe": "ASK",
    "promise_never": "NEVER",
    "single_commitment": "NEVER",
    "verify_before_dispatch": "NEVER",
    "release_on_acceptance": "NEVER",
}


def test_the_pack_id_and_taxonomy_pin():
    assert PACK.pack_id == "asg/seller/0.1.5"
    assert PACK.taxonomy.taxonomy_version == TAXONOMY_VERSION == "6"


def test_it_cites_each_definition_at_its_catalog_digest():
    assert [w.wicket_id for w in PACK.constraints] == CITED
    for wicket in PACK.constraints:
        assert wicket.definition_digest() == CATALOG.get(wicket.wicket_id).digest, wicket.wicket_id


def test_each_check_has_one_rule_with_its_declared_disposition():
    measured = [o for o in PACK.obligations if o.check is not None]
    assert len(measured) == len(PACK.obligations) == len(DECLARED)
    assert {o.check: o.default_disposition for o in measured} == DECLARED


def test_the_address_is_measured_by_the_release_rule_not_the_gate():
    """The gate reads only the class, so it would ask on the address after
    acceptance too; since 0.1.1 the pack cites neither the gate nor a rule on it."""
    assert not [o for o in PACK.obligations if o.check == "action_class_gate" or o.selector is not None]
    assert "action_class_gate" not in {w.check for w in PACK.constraints}
    (release,) = [w for w in PACK.constraints if w.check == "release_on_acceptance"]
    assert release.config["release_classes"] == [
        "name", "phone", "email", "home_address", "address", "pickup_location", "other_contact", "pickup_area"]
    assert release.config["pre_acceptance_classes"] == ["pickup_area"]
    (share,) = [s for s in PACK.action_semantics if s.action_class == "disclosure.personal"]
    assert share.required_fields == ("target", "recipient_role", "disclosed_classes")
    assert "representation_class" not in (*share.required_fields, *share.optional_fields)
    assert {"item_ref", "deal_id"} <= set(share.optional_fields)


def test_0_1_0_is_kept_byte_for_byte_and_still_loads_at_its_digest():
    frozen = FROZEN_0_1_0_DIR / "pack.yaml"
    assert hashlib.sha256(frozen.read_bytes()).hexdigest() == PACK_0_1_0_FILE_SHA256
    pack = load_pack_dir(FROZEN_0_1_0_DIR)
    assert pack.pack_id == "asg/seller/0.1.0"
    assert pack.definition_digest() == PACK_0_1_0_DIGEST


def test_0_1_1_is_kept_byte_for_byte_and_still_loads_at_its_digest():
    frozen = FROZEN_0_1_1_DIR / "pack.yaml"
    assert hashlib.sha256(frozen.read_bytes()).hexdigest() == PACK_0_1_1_FILE_SHA256
    pack = load_pack_dir(FROZEN_0_1_1_DIR)
    assert pack.pack_id == "asg/seller/0.1.1"
    assert pack.definition_digest() == PACK_0_1_1_DIGEST


def test_0_1_2_is_kept_byte_for_byte_and_still_loads_at_its_digest():
    frozen = FROZEN_0_1_2_DIR / "pack.yaml"
    assert hashlib.sha256(frozen.read_bytes()).hexdigest() == PACK_0_1_2_FILE_SHA256
    pack = load_pack_dir(FROZEN_0_1_2_DIR)
    assert pack.pack_id == "asg/seller/0.1.2"
    assert pack.definition_digest() == PACK_0_1_2_DIGEST


def test_0_1_3_is_kept_byte_for_byte_and_still_loads_at_its_digest():
    frozen = FROZEN_0_1_3_DIR / "pack.yaml"
    assert hashlib.sha256(frozen.read_bytes()).hexdigest() == PACK_0_1_3_FILE_SHA256
    pack = load_pack_dir(FROZEN_0_1_3_DIR)
    assert pack.pack_id == "asg/seller/0.1.3"
    assert pack.definition_digest() == PACK_0_1_3_DIGEST


def test_0_1_4_is_kept_byte_for_byte_and_still_loads_at_its_digest():
    frozen = FROZEN_0_1_4_DIR / "pack.yaml"
    assert hashlib.sha256(frozen.read_bytes()).hexdigest() == PACK_0_1_4_FILE_SHA256
    pack = load_pack_dir(FROZEN_0_1_4_DIR)
    assert pack.pack_id == "asg/seller/0.1.4"
    assert pack.definition_digest() == PACK_0_1_4_DIGEST


def test_0_1_3_changes_only_s05_and_its_folds():
    """0.1.3 cites required_disclosure/1.1.0 where 0.1.2 cited 1.0.0, adds the
    statement fold, and changes nothing else it cites or declares."""
    old, new = load_pack_dir(FROZEN_0_1_2_DIR), load_pack_dir(FROZEN_0_1_3_DIR)
    swapped = [w.wicket_id for w in old.constraints]
    swapped[swapped.index("required_disclosure/1.0.0")] = "required_disclosure/1.1.0"
    assert [w.wicket_id for w in new.constraints] == swapped
    assert [f.fold_id for f in new.folds] == [*(f.fold_id for f in old.folds), "disclosure.statement_seen/1.0.0"]
    assert [(o.id, o.check, o.default_disposition) for o in new.obligations] == [
        (o.id, o.check, o.default_disposition) for o in old.obligations]
    changed = [o.id for o, was in zip(new.obligations, old.obligations, strict=True) if o.statement != was.statement]
    assert changed == ["s05-required-statement-made-first"]


def test_0_1_4_changes_only_s01_and_s13():
    """0.1.4 cites the 1.1.0 seller.recipient_role and
    seller.release_on_acceptance where 0.1.3 cited 1.0.0, and changes nothing
    else it cites or declares; only those two rules' statements change."""
    old, new = load_pack_dir(FROZEN_0_1_3_DIR), load_pack_dir(FROZEN_0_1_4_DIR)
    swapped = [w.wicket_id for w in old.constraints]
    for wicket_id in ("seller.recipient_role", "seller.release_on_acceptance"):
        swapped[swapped.index(f"{wicket_id}/1.0.0")] = f"{wicket_id}/1.1.0"
    assert [w.wicket_id for w in new.constraints] == swapped
    assert [f.fold_id for f in new.folds] == [f.fold_id for f in old.folds]
    assert [(o.id, o.check, o.default_disposition) for o in new.obligations] == [
        (o.id, o.check, o.default_disposition) for o in old.obligations]
    changed = [o.id for o, was in zip(new.obligations, old.obligations, strict=True) if o.statement != was.statement]
    assert changed == ["s01-personal-data-to-the-buyer-or-the-user", "s13-personal-data-only-to-the-buyer-who-accepted"]
    (role,) = [w for w in new.constraints if w.check == "recipient_role"]
    assert role.config["role_classes"] == {"platform": ["email"]}
    (release,) = [w for w in new.constraints if w.check == "release_on_acceptance"]
    assert release.config["except_recipient_roles"] == ["platform"]


def test_0_1_5_changes_only_s01_s04_s05_s06_s07_s10_and_s13():
    """0.1.5 cites seller.recipient_role/1.2.0,
    seller.release_on_acceptance/1.2.0 and required_disclosure/1.2.0 where
    0.1.4 cited 1.1.0, seller.destination_rail/1.1.0 where it cited 1.0.0,
    offer_expiry/1.1.0 where it cited 1.0.1, and promise_requires_approval/1.1.0
    and promise_never/1.1.0 where it cited 1.0.0, and changes nothing else it
    cites or declares; only those seven rules' statements and the offer's,
    the acceptance's, the sale's, statement.send's (statement_attributed_to)
    and the share's action semantics change.
    s07 still asks; offer.accept requires the rail and takes the handover, a
    sale takes the handover, offer.make takes proposal_at or no_proposal,
    and the offer and its acceptance take listing_classes (s05)."""
    old = load_pack_dir(FROZEN_0_1_4_DIR)
    swapped = [w.wicket_id for w in old.constraints]
    for wicket_id in ("seller.recipient_role", "seller.release_on_acceptance", "required_disclosure"):
        swapped[swapped.index(f"{wicket_id}/1.1.0")] = f"{wicket_id}/1.2.0"
    swapped[swapped.index("seller.destination_rail/1.0.0")] = "seller.destination_rail/1.1.0"
    swapped[swapped.index("offer_expiry/1.0.1")] = "offer_expiry/1.1.0"
    for wicket_id in ("promise_requires_approval", "promise_never"):
        swapped[swapped.index(f"{wicket_id}/1.0.0")] = f"{wicket_id}/1.1.0"
    assert [w.wicket_id for w in PACK.constraints] == swapped
    assert [f.fold_id for f in PACK.folds] == [f.fold_id for f in old.folds]
    assert [(o.id, o.check, o.default_disposition) for o in PACK.obligations] == [
        (o.id, o.check, o.default_disposition) for o in old.obligations]
    changed = [o.id for o, was in zip(PACK.obligations, old.obligations, strict=True) if o.statement != was.statement]
    assert changed == ["s01-personal-data-to-the-buyer-or-the-user", "s04-statement-that-needs-the-users-word",
                       "s05-required-statement-made-first", "s06-proposal-still-current",
                       "s07-payment-rail-the-user-allows", "s10-statement-never-made",
                       "s13-personal-data-only-to-the-buyer-who-accepted"]
    semantics = [a.action_type for a, was in zip(PACK.action_semantics, old.action_semantics, strict=True) if a != was]
    assert semantics == ["offer.make", "offer.accept", "sale.complete", "statement.send", "personal_data.share"]
    by_type = {a.action_type: a for a in PACK.action_semantics}
    was = {a.action_type: a for a in old.action_semantics}
    assert set(by_type["offer.accept"].required_fields) - set(was["offer.accept"].required_fields) == {"rail"}
    assert "handover" in by_type["offer.accept"].optional_fields
    assert "handover" in by_type["sale.complete"].optional_fields
    (role,) = [w for w in PACK.constraints if w.check == "recipient_role"]
    assert role.config["role_classes"] == {"platform": ["email", "pickup_area"]}
    (rail,) = [w for w in PACK.constraints if w.check == "destination_rail"]
    assert rail.config["action_classes"] == ["agreement.accept", "marketplace.sale"]
    assert rail.config["missing_rail"] == {"agreement.accept": "fail"}
    assert "proposal_at" not in by_type["offer.make"].required_fields
    assert {"proposal_at", "no_proposal"} <= set(by_type["offer.make"].optional_fields)
    (expiry,) = [w for w in PACK.constraints if w.check == "offer_expiry"]
    assert expiry.config["no_proposal_classes"] == ["marketplace.offer"]
    (required,) = [w for w in PACK.constraints if w.check == "required_disclosure"]
    assert required.config["count_listing_classes"] is True
    listing = {a.action_type for a in PACK.action_semantics if "listing_classes" in a.optional_fields}
    assert listing == {"offer.make", "offer.accept"}
    assert not [a for a in PACK.action_semantics if "listing_classes" in a.required_fields]
    (statement,) = [a for a in PACK.action_semantics if a.action_type == "statement.send"]
    assert "statement_attributed_to" in statement.optional_fields
    (ask,) = [w for w in PACK.constraints if w.check == "promise_requires_approval"]
    assert ask.config["authorized_path"] == ["body", "ordered_representations"]
    (never,) = [w for w in PACK.constraints if w.check == "promise_never"]
    assert never.config["never"] == never.config["attributed_never"] == ["authenticity"]
    template = never.config["attributed_template"]
    assert (template["prefix"], template["suffix"]) == ("The seller says ", " (unverified)")
    # The marks are the shared fixture (test_representing_bridge.py).
    assert len(template["sentence_marks"]) == 181


def test_it_cites_no_spending_limit_and_no_rule_is_measured_by_one():
    assert "caps" not in DECLARED
    assert "caps" not in {w.check for w in PACK.constraints}
    assert not [f for f in PACK.folds if f.fold_id.startswith("spend.")]
    assert load_pack_dir(PACK_DIR).definition_digest() != PACK_0_1_1_DIGEST


def _scenario_run(pack_dir: Path, tmp_path: Path, decisions: dict | None = None):
    """The fixture's scenarios under ``pack_dir``: each step's capsule, by
    name; ``decisions``, when given, collects each step's ``GuardDecision``."""
    acceptance = _acceptance_module()
    acceptance.PACK_DIR = pack_dir
    if pack_dir == FROZEN_0_1_4_DIR:
        acceptance.EXPECTED_OVERRIDES = acceptance.BEFORE_PICKUP_AREA
    elif pack_dir != PACK_DIR:
        acceptance.EXPECTED_OVERRIDES = acceptance.BEFORE_PLATFORM
    store = LedgerStore(tmp_path / "ledger")
    try:
        _, _, capsules, _ = acceptance._run_scenarios(store, project_dir=tmp_path / "project", decisions=decisions)
    finally:
        store.close()
    return capsules


# Checks whose evidence names an earlier record by capsule_id: the matched act
# and the cited mandate.
NAMES_A_CAPSULE_ID = frozenset({"dedupe", "verify_before_dispatch"})


def _constraints(capsule: dict) -> dict[str, dict]:
    return {c["id"]: c for c in capsule["constraints"]}


def test_0_1_2_decides_every_scenario_as_0_1_1_did_and_records_caps_out_of_scope(tmp_path):
    """Dropping the cited limit changes no decision: every scenario has the
    same disposition and every constraint the same result under both, and
    the same evidence except where it names an earlier record's capsule_id,
    which covers the manifest digest. caps is still recorded, n/a and out of
    scope, with the same evidence; only its method, the fold 0.1.1 cited,
    is gone."""
    old = _scenario_run(FROZEN_0_1_1_DIR, tmp_path / "0.1.1")
    new = _scenario_run(FROZEN_0_1_2_DIR, tmp_path / "0.1.2")
    assert list(old) == list(new) and len(new) == len(PACK.fixtures.scenarios)
    out_of_scope = json_digest(not_applicable_evidence("caps", in_scope=False))
    for name in new:
        assert new[name]["disposition"] == old[name]["disposition"], name
        before, after = _constraints(old[name]), _constraints(new[name])
        assert list(after) == list(before), name
        for check_id, record in after.items():
            assert record["result"] == before[check_id]["result"], (name, check_id)
            if check_id not in NAMES_A_CAPSULE_ID:
                assert record.get("evidence_digest") == before[check_id].get("evidence_digest"), (name, check_id)
        caps_before, caps_after = before["caps"], after["caps"]
        assert caps_after["result"] == "n/a" and caps_after["evidence_digest"] == out_of_scope, name
        assert caps_before["method"] == "spend.weekly/1.0.0" and "method" not in caps_after, name
        assert caps_after == {k: v for k, v in caps_before.items() if k != "method"}, name


def test_0_1_3_decides_every_scenario_as_0_1_2_did(tmp_path):
    """No scenario holds a claim, so s05 counts what it counted under 0.1.2:
    every scenario has the same disposition and every constraint the same
    result. Evidence is the same except where it names an earlier record's
    capsule_id (which covers the manifest digest) and s05's, which now also
    names the statement fold, the deal and the deal's counts."""
    old = _scenario_run(FROZEN_0_1_2_DIR, tmp_path / "0.1.2")
    new = _scenario_run(FROZEN_0_1_3_DIR, tmp_path / "0.1.3")
    assert list(old) == list(new) and len(new) == len(PACK.fixtures.scenarios)
    for name in new:
        assert new[name]["disposition"] == old[name]["disposition"], name
        before, after = _constraints(old[name]), _constraints(new[name])
        assert list(after) == list(before), name
        for check_id, record in after.items():
            assert record["result"] == before[check_id]["result"], (name, check_id)
            if check_id not in NAMES_A_CAPSULE_ID | {"required_disclosure"}:
                assert record.get("evidence_digest") == before[check_id].get("evidence_digest"), (name, check_id)


def test_0_1_4_decides_every_scenario_as_0_1_3_did_but_the_email_to_the_platform(tmp_path):
    """Only email-to-the-platform decides differently: 0.1.3 refused it on
    s01 and s13, 0.1.4 allows it. The other platform rows still refuse, now on
    s01 alone. Every other scenario has the same disposition and every
    constraint the same result; the evidence of recipient_role, which now
    records the classes read, and of the checks naming an earlier capsule
    differ."""
    old = _scenario_run(FROZEN_0_1_3_DIR, tmp_path / "0.1.3")
    new = _scenario_run(FROZEN_0_1_4_DIR, tmp_path / "0.1.4")
    assert list(old) == list(new) and len(new) == len(PACK.fixtures.scenarios)
    platform = {"email-to-the-platform", "phone-to-the-platform", "address-to-the-platform",
                "platform-disclosure-naming-no-class", "email-and-phone-to-the-platform",
                "pickup-area-to-the-platform", "pickup-location-to-the-platform",
                "email-and-pickup-area-to-the-platform"}
    for name in new:
        before, after = _constraints(old[name]), _constraints(new[name])
        assert list(after) == list(before), name
        if name in platform:
            assert before["recipient_role"]["result"] == before["release_on_acceptance"]["result"] == "fail", name
            assert after["release_on_acceptance"]["result"] == "n/a", name
            expected = "accept" if name == "email-to-the-platform" else "reject"
            assert (old[name]["disposition"]["decision"], new[name]["disposition"]["decision"]) == (
                "reject", expected), name
            continue
        assert new[name]["disposition"] == old[name]["disposition"], name
        for check_id, record in after.items():
            assert record["result"] == before[check_id]["result"], (name, check_id)
            if check_id not in NAMES_A_CAPSULE_ID | {"recipient_role"}:
                assert record.get("evidence_digest") == before[check_id].get("evidence_digest"), (name, check_id)


def test_0_1_5_decides_every_scenario_as_0_1_4_did_but_the_pickup_area_shares_rail_no_proposal_and_listing(
        tmp_path):
    """Five kinds of scenario decide differently. The two giving the platform
    the listing's pickup area: 0.1.4 refused them on s01, 0.1.5 allows them.
    The shares to a buyer s13 1.2.0 releases (the pickup area before
    acceptance, each class to the buyer who accepted in the accepting deal):
    0.1.4's s13 read a statement class and a citation they do not carry, or
    refused the citation, and 0.1.5's passes them. And the rows s07 now
    checks at the commit or counts a payment app on: 0.1.4 held s07 out of
    scope at every commit, 0.1.5 passes a commit on an allowed rail and asks
    on the others, and passes a sale by Venmo or Zelle that 0.1.4 asked on.
    Every other scenario has the same disposition and every constraint the
    same result, with the same evidence except recipient_role's (it records
    the platform's classes), s13's (it records the classes of data read),
    destination_rail's (it records how the rail was counted and the handover),
    s05's (1.2.0 also records the listing's classes) and the checks naming an
    earlier capsule. And two on s06, which only offer_expiry/1.1.0 reads
    no_proposal for: the first offer answering no proposal (0.1.4 asked for a
    missing proposal time, 0.1.5 holds s06 not applicable and allows it), and
    no_proposal beside a proposal time (0.1.4 passed it on age, 0.1.5 asks).
    And the offer whose listing stated the condition asked on s05 under 0.1.4
    and passes under 0.1.5.
    And the ordered warranty (0.1.4's s04 read a member no task authority
    seals and asked; 0.1.5's reads the order and passes) and the user's own
    authenticity claim (0.1.4's s10 refused it; 0.1.5's passes it); their
    promise_never evidence also records the attribution and the words' check."""
    acceptance = _acceptance_module()
    old_decisions: dict = {}
    new_decisions: dict = {}
    old = _scenario_run(FROZEN_0_1_4_DIR, tmp_path / "0.1.4", old_decisions)
    new = _scenario_run(PACK_DIR, tmp_path / "0.1.5", new_decisions)
    assert list(old) == list(new) and len(new) == len(PACK.fixtures.scenarios)
    pickup_area = {"pickup-area-to-the-platform", "email-and-pickup-area-to-the-platform"}
    released = set(acceptance.BEFORE_RELEASE_FROM_HISTORY)
    commits = {name for name in new if new[name]["asg_payload"].get("action_class") == "agreement.accept"}
    assert {"acceptance-in-a", "acceptance-in-b-of-item-2", "commit-stating-no-rail"} <= commits
    by_app = {"sale-to-a-by-venmo", "sale-to-a-by-zelle"}
    # s06 under 1.1.0 reads no_proposal; 1.0.1 never did.
    no_proposal = {"first-offer-answering-no-proposal": (("fail", "n/a"), ("needs_input", "accept")),
                   "no-proposal-beside-a-proposal-time": (("pass", "fail"), ("accept", "needs_input"))}
    # s05's evidence is 1.1.0's plus the listing's classes, absent on every
    # scenario but the three listing offers.
    for name in new:
        (was,) = [c for c in old_decisions[name].constraints if c.id == "required_disclosure"]
        (now,) = [c for c in new_decisions[name].constraints if c.id == "required_disclosure"]
        if now.result == "n/a":
            assert now.evidence == was.evidence, name
            continue
        evidence, before_listing = dict(now.evidence), dict(was.evidence)
        listing = evidence.pop("listing_classes")
        if name in acceptance.BEFORE_LISTING:
            # The listing states the condition: nothing is missing now.
            assert (before_listing.pop("missing_classes"), evidence.pop("missing_classes")) == (["condition"], []), name
        assert evidence == before_listing, name
        assert (listing is None) == (not name.startswith("offer-after-")), name
    statements = {"statement-warranty-ordered": ("promise_requires_approval", "needs_input"),
                  "statement-authenticity-attributed": ("promise_never", "reject")}
    for name in new:
        before, after = _constraints(old[name]), _constraints(new[name])
        assert list(after) == list(before), name
        if name in pickup_area | released:
            check_id = "recipient_role" if name in pickup_area else "release_on_acceptance"
            assert (before[check_id]["result"], after[check_id]["result"]) == ("fail", "pass"), name
            assert (old[name]["disposition"]["decision"], new[name]["disposition"]["decision"]) == (
                "reject", "accept"), name
            continue
        if name in commits | by_app:
            assert before["destination_rail"]["result"] == ("n/a" if name in commits else "fail"), name
            moved = "fail" if acceptance.BEFORE_COMMIT_RAIL.get(name) == ALLOW else "pass"
            assert after["destination_rail"]["result"] == moved, name
            for check_id, record in after.items():
                if check_id != "destination_rail":
                    assert record["result"] == before[check_id]["result"], (name, check_id)
            continue
        if name in no_proposal:
            results, decisions = no_proposal[name]
            assert (before["offer_expiry"]["result"], after["offer_expiry"]["result"]) == results, name
            assert (old[name]["disposition"]["decision"], new[name]["disposition"]["decision"]) == decisions, name
            continue
        if name in acceptance.BEFORE_LISTING:
            assert (before["required_disclosure"]["result"], after["required_disclosure"]["result"]) == (
                "fail", "pass"), name
            assert (old[name]["disposition"]["decision"], new[name]["disposition"]["decision"]) == (
                "needs_input", "accept"), name
            continue
        if name in statements:
            check_id, was = statements[name]
            assert (before[check_id]["result"], after[check_id]["result"]) == ("fail", "pass"), name
            assert (old[name]["disposition"]["decision"], new[name]["disposition"]["decision"]) == (
                was, "accept"), name
            continue
        assert new[name]["disposition"] == old[name]["disposition"], name
        for check_id, record in after.items():
            assert record["result"] == before[check_id]["result"], (name, check_id)
            if check_id not in NAMES_A_CAPSULE_ID | {"recipient_role", "release_on_acceptance", "destination_rail",
                                                     "required_disclosure", "promise_never"}:
                assert record.get("evidence_digest") == before[check_id].get("evidence_digest"), (name, check_id)


def _copy(tmp_path: Path) -> Path:
    target = tmp_path / "seller"
    shutil.copytree(PACK_DIR, target)
    return target


@pytest.mark.parametrize("wicket_id", ["seller.recipient_role/1.2.0", "seller.single_commitment/1.0.0",
                                       "price_floor/2.0.1"])
def test_a_definition_cited_at_another_digest_is_refused_at_load(tmp_path, wicket_id):
    pack_dir = _copy(tmp_path)
    data = yaml.safe_load((pack_dir / "pack.yaml").read_text())
    (entry,) = [c for c in data["constraints"] if c["wicket_ref"] == wicket_id]
    entry["digest"] = CATALOG.get("recipient_role/1.0.0").digest  # a real digest, of another definition
    (pack_dir / "pack.yaml").write_text(yaml.safe_dump(data, sort_keys=False))
    with pytest.raises(PackDefinitionError):
        load_pack_dir(pack_dir)


# -- 0.1.1 to 0.1.5 are no looser than 0.1.0 on personal data, without an
# acceptance, to anyone but a platform (0.1.5 also passes the public pickup
# area to a buyer, a class 0.1.0 could not name) --


def _acceptance_module():
    """The seller fixture's module, for its task authority, opening and parties."""
    spec = importlib.util.spec_from_file_location("seller_acceptance",
                                                  Path(__file__).parent / "test_pack_seller_acceptance.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _engines(tmp_path: Path):
    """One observe-mode engine per pack version, each on its own ledger."""
    signer = LocalSigner(key_id="seller-strictness-key", secret=b"seller-strictness-fixed-key")
    for version, pack_dir in (("0.1.0", FROZEN_0_1_0_DIR), ("0.1.1", FROZEN_0_1_1_DIR), ("0.1.2", FROZEN_0_1_2_DIR),
                              ("0.1.3", FROZEN_0_1_3_DIR), ("0.1.4", FROZEN_0_1_4_DIR), ("0.1.5", PACK_DIR)):
        store = LedgerStore(tmp_path / version / "ledger")
        installed = install_pack(load_pack_dir(pack_dir), project_dir=tmp_path / version / "project", mode="observe")
        yield version, store, build_engine(installed, ledger=store, signer_provider=lambda: signer)


def test_no_personal_disclosure_0_1_0_asked_on_is_allowed_by_a_later_version_without_an_acceptance(tmp_path):
    """Every class a disclosure may declare, none, and one outside the set;
    to the buyer, the user and a third party; with and without a one-shot
    approval of the class. No acceptance is sealed. Where 0.1.0 did not
    allow, 0.1.1 to 0.1.5 must not either -- and each refuses every one."""
    acceptance = _acceptance_module()
    classes = [*CATALOG.get("promise_never/1.0.0").definition.config["representation_classes"], None, "phone"]
    cases = [(cls, role, approved) for cls in classes for role in ("buyer", "self", "third_party")
             for approved in (False, True)]
    outcomes: dict[str, list[str]] = {}
    for version, store, engine in _engines(tmp_path):
        try:
            for n, (cls, role, approved) in enumerate(cases):
                approval: AuthorizationRecord | None = (
                    {"type": "approval/v0", "body": {"representation_class": cls}} if approved else None)
                action = Action(
                    verb="share_personal", operator="seller-strictness", developer="seller-assistant@v1",
                    action_class="disclosure.personal", target=acceptance.BUYER_A, recipient_role=role,
                    representation_class=cls, task_authority_ref=acceptance.TASK_REF, item_ref=acceptance.ITEM,
                    authorized_by=authorization_record_digest(approval) if approval else None,
                    equivalence_key=f"share/{n}", action_id=f"share/seller-strictness-{n}",
                    timestamp="2026-10-09T10:00:00Z")
                decision = engine.check(action, dry_run=True, task_authority_record=acceptance.TASK,
                                        authorization_record=approval,
                                        commercial_bounds_opening=acceptance.OPENING)
                outcomes.setdefault(version, []).append(decision.outcome)
        finally:
            store.close()
    later = ("0.1.1", "0.1.2", "0.1.3", "0.1.4", "0.1.5")
    assert {len(outcomes[v]) for v in ("0.1.0", *later)} == {len(cases)} and len(cases) == 96
    for n, case in enumerate(cases):
        assert outcomes["0.1.0"][n] != ALLOW, case  # 0.1.0 asked on (or refused) every personal disclosure
        assert [outcomes[v][n] for v in later] == [DENY] * len(later), case
