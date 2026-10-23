# SPDX-License-Identifier: Apache-2.0
"""The listing's public pickup area (capsule-engine#181, seller 0.1.5).

``pickup_area`` is the ZIP or region a listing shows, apart from
``pickup_location``, the exact spot. seller.recipient_role/1.2.0 lets the
platform a sale is listed on be given it beside the user's email; the exact
spot, an address and a phone to a platform still fail. The everyday
definitions do not change: a buyer's agent never gives a platform a pickup
area, so recipient_role/1.1.0 still refuses it."""
from __future__ import annotations

from pathlib import Path

import pytest
from agent_action_capsule import json_digest

from capsule_engine.guards import Action
from capsule_engine.guards.action import DISCLOSED_CLASSES, disclosed_classes_of
from capsule_engine.guards.checks import check_recipient_role
from capsule_engine.guards.wickets import load_definition_file
from capsule_engine.report.replay import action_for_record

CATALOG = Path(__file__).parent.parent / "capsule_engine" / "guards" / "wickets" / "catalog_defs"
SELLER_ROLE_1_1 = load_definition_file(CATALOG / "recipient_role.seller.v1.1.yaml")
SELLER_ROLE_1_2 = load_definition_file(CATALOG / "recipient_role.seller.v1.2.yaml")
ROLE_1_1 = load_definition_file(CATALOG / "recipient_role.v1.1.yaml")


def _share(classes: tuple[str, ...] | None, role: str = "platform") -> Action:
    return Action(verb="share_contact", operator="pickup-area-test", developer="assistant@v1",
                  action_class="disclosure.personal", target="platform/listings", recipient_role=role,
                  disclosed_classes=classes, action_id="share_contact/pickup-area-test",
                  timestamp="2026-10-22T10:00:00Z")


def _role(definition, action: Action):
    config = definition.config
    return check_recipient_role(action, roles=config["roles"], allowed_roles=config["allowed_roles"],
                                action_classes=config["action_classes"],
                                role_classes=config.get("role_classes")).constraint


def test_pickup_area_is_a_class_a_disclosure_may_name_apart_from_the_exact_spot():
    assert {"pickup_area", "pickup_location"} <= DISCLOSED_CLASSES
    assert disclosed_classes_of(["pickup_area", "email"]) == ("email", "pickup_area")


@pytest.mark.parametrize("classes", [("pickup_area",), ("email", "pickup_area")])
def test_1_2_passes_the_pickup_area_to_a_platform(classes):
    out = _role(SELLER_ROLE_1_2, _share(classes))
    assert out.result == "pass"
    assert out.reason == f"the recipient is a platform, given only {' or '.join(classes)}"
    assert out.evidence["role_classes"] == {"platform": ["email", "pickup_area"]}
    assert out.evidence["disclosed_classes"] == list(classes)


@pytest.mark.parametrize("classes,named", [(("pickup_location",), "pickup_location"), (("address",), "address"),
                                           (("phone",), "phone"),
                                           (("pickup_area", "pickup_location"), "pickup_location")])
def test_1_2_still_refuses_the_exact_spot_an_address_or_a_phone_to_a_platform(classes, named):
    out = _role(SELLER_ROLE_1_2, _share(classes))
    assert out.result == "fail"
    assert out.reason == ("the recipient is a platform, which may be given only email or pickup_area; "
                          f"this disclosure also gives {named}")


@pytest.mark.parametrize("definition", [SELLER_ROLE_1_1, ROLE_1_1], ids=["seller-1.1", "everyday-1.1"])
def test_the_earlier_definitions_still_refuse_the_pickup_area_to_a_platform(definition):
    out = _role(definition, _share(("pickup_area",)))
    assert out.result == "fail"
    assert out.reason.endswith("this disclosure also gives pickup_area")


def test_1_2_differs_from_1_1_only_in_what_a_platform_may_be_given():
    old, new = SELLER_ROLE_1_1.config, SELLER_ROLE_1_2.config
    assert {k: v for k, v in new.items() if k != "role_classes"} == {k: v for k, v in old.items() if k != "role_classes"}
    assert old["role_classes"] == {"platform": ["email"]}
    assert new["role_classes"] == {"platform": ["email", "pickup_area"]}


def test_the_buyer_and_the_user_still_pass_whatever_they_are_given():
    assert _role(SELLER_ROLE_1_2, _share(("pickup_location",), role="buyer")).result == "pass"
    assert _role(SELLER_ROLE_1_2, _share(None, role="self")).result == "pass"


def test_a_deal_check_bridges_the_pickup_area_class():
    """The capsulectl bridge reads a share's ``disclosing`` through the same
    closed set, so a check naming pickup_area carries it."""
    disclosed = {"x-deal-v0": {"record_type": "check"},
                 "body": {"action": "share_contact", "action_class": "disclosure.personal", "taxonomy_version": "6",
                          "disclosing": ["pickup_area"], "recipient_role": "platform"}}
    record = {"operator": "deal", "developer": "capsulectl-deal", "action_id": "deal/1",
              "model_attestation": {"compute_attestation": {"agent_input_digest": json_digest(disclosed)}}}
    action = action_for_record(record, disclosed)
    assert (action.recipient_role, action.disclosed_classes) == ("platform", ("pickup_area",))
    assert _role(SELLER_ROLE_1_2, action).result == "pass"
