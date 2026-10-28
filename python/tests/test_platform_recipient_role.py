# SPDX-License-Identifier: Apache-2.0
"""A platform recipient: the marketplace, booking site or mail relay acting
between the user and the counterparty (everyday 0.3.7, seller 0.1.4).

recipient_role/1.1.0 and seller.recipient_role/1.1.0 pass a disclosure to a
platform only when every class of data it names is email, and refuse
anything else to one. action_class_gate/1.1.0 leaves such a disclosure out of
everyday's r11 and r12, and seller.release_on_acceptance/1.1.0 out of s13.
Every reason names the role read and the roles that pass, once. The first
email to a platform is paused by the producer, which knows the recipient;
these tests pin what the packs decide."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from agent_action_capsule import json_digest

from capsule_engine.guards import Action, LocalSigner
from capsule_engine.guards.action import disclosed_classes_of
from capsule_engine.guards.capsule import ALLOW, DENY, ESCALATE, act_payload
from capsule_engine.guards.checks import (
    check_action_class_gate,
    check_recipient_role,
    check_release_on_acceptance,
)
from capsule_engine.guards.history import MemoryHistory
from capsule_engine.guards.wickets import load_definition_file
from capsule_engine.packs import build_engine, install_pack, load_pack_dir
from capsule_engine.packs.obligation_results import obligation_results
from capsule_engine.report.replay import action_for_record

ROOT = Path(__file__).parent.parent / "capsule_engine"
CATALOG = ROOT / "guards" / "wickets" / "catalog_defs"
ROLE_1_0 = load_definition_file(CATALOG / "recipient_role.yaml")
ROLE_1_1 = load_definition_file(CATALOG / "recipient_role.v1.1.yaml")
SELLER_ROLE_1_1 = load_definition_file(CATALOG / "recipient_role.seller.v1.1.yaml")
GATE_1_0 = load_definition_file(CATALOG / "action_class_gate.yaml")
GATE_1_1 = load_definition_file(CATALOG / "action_class_gate.v1.1.yaml")
RELEASE_1_0 = load_definition_file(CATALOG / "release_on_acceptance.seller.yaml")
RELEASE_1_1 = load_definition_file(CATALOG / "release_on_acceptance.seller.v1.1.yaml")


def _share(role: str | None = "platform", classes: tuple[str, ...] | None = ("email",), **fields) -> Action:
    fields.setdefault("action_class", "disclosure.personal")
    return Action(verb="share_contact", operator="platform-test", developer="assistant@v1", target="platform/listings",
                  recipient_role=role, disclosed_classes=classes, action_id="share_contact/platform-test",
                  timestamp="2026-10-21T10:00:00Z", **fields)


def _role(definition, action: Action):
    config = definition.config
    return check_recipient_role(action, roles=config["roles"], allowed_roles=config["allowed_roles"],
                                action_classes=config["action_classes"],
                                role_classes=config.get("role_classes")).constraint


# -- the reason reads plainly ----------------------------------------------------


def test_a_third_party_reason_names_the_role_read_and_the_roles_that_pass_once():
    out = _role(ROLE_1_0, _share("third_party", None))
    assert out.result == "fail"
    assert out.reason == "the recipient is a third_party; personal data passes only to fulfilling_merchant or self"


def test_under_1_1_the_reason_also_names_what_a_platform_may_be_given():
    out = _role(ROLE_1_1, _share("third_party", ("email",)))
    assert out.reason == ("the recipient is a third_party; personal data passes only to fulfilling_merchant or self, "
                          "or a platform given only email")
    seller = _role(SELLER_ROLE_1_1, _share("third_party", ("email",)))
    assert seller.reason == ("the recipient is a third_party; personal data passes only to buyer or self, "
                             "or a platform given only email")


def test_a_role_outside_the_set_says_it_is_not_known():
    out = _role(ROLE_1_1, _share("neighbour", ("email",)))
    assert out.result == "fail"
    assert out.evidence["recognised"] is False
    assert out.reason == ("recipient_role 'neighbour' is not a role this rule knows; personal data passes only to "
                          "fulfilling_merchant or self, or a platform given only email")


def test_1_0_keeps_its_evidence_and_does_not_know_a_platform():
    out = _role(ROLE_1_0, _share())
    assert out.result == "fail"
    assert out.evidence == {"recipient_role": "platform", "recognised": False,
                            "allowed_roles": ["fulfilling_merchant", "self"]}


# -- recipient_role/1.1.0 on a platform ------------------------------------------


@pytest.mark.parametrize("definition", [ROLE_1_1, SELLER_ROLE_1_1], ids=["everyday", "seller"])
def test_an_email_to_a_platform_passes_and_records_the_classes_read(definition):
    out = _role(definition, _share())
    assert out.result == "pass"
    assert out.reason == "the recipient is a platform, given only email"
    assert out.evidence == {"recipient_role": "platform", "recognised": True,
                            "allowed_roles": sorted(definition.config["allowed_roles"]),
                            "role_classes": {"platform": ["email"]}, "disclosed_classes": ["email"]}


@pytest.mark.parametrize("classes,named", [(("phone",), "phone"), (("home_address",), "home_address"),
                                           (("address",), "address"), (("email", "phone"), "phone"),
                                           (("email", "home_address", "phone"), "home_address or phone")])
def test_anything_but_email_to_a_platform_fails_and_names_what_else_it_gives(classes, named):
    out = _role(ROLE_1_1, _share(classes=classes))
    assert out.result == "fail"
    assert out.reason == f"the recipient is a platform, which may be given only email; this disclosure also gives {named}"
    assert out.evidence["disclosed_classes"] == list(classes)


def test_a_disclosure_to_a_platform_naming_no_class_fails():
    out = _role(ROLE_1_1, _share(classes=None))
    assert out.result == "fail"
    assert out.reason == "the recipient is a platform, which may be given only email; the disclosure names no class of data"
    assert out.evidence["disclosed_classes"] is None


def test_the_merchant_still_passes_whatever_it_is_given():
    out = _role(ROLE_1_1, _share("fulfilling_merchant", ("home_address", "phone")))
    assert out.result == "pass"


def test_a_rule_that_passes_no_role_says_so():
    out = check_recipient_role(_share("third_party"), roles=["third_party", "self"], allowed_roles=[],
                               action_classes=["disclosure.personal"]).constraint
    assert (out.result, out.reason) == ("fail", "the recipient is a third_party; personal data passes only to no role")


@pytest.mark.parametrize("role_classes", [{"neighbour": ["email"]}, {"self": ["email"]}, {"platform": []}])
def test_role_classes_must_name_a_known_role_that_is_not_already_allowed(role_classes):
    with pytest.raises(ValueError, match="role_classes"):
        check_recipient_role(_share(), roles=ROLE_1_1.config["roles"], allowed_roles=ROLE_1_1.config["allowed_roles"],
                             action_classes=ROLE_1_1.config["action_classes"], role_classes=role_classes)


# -- disclosed_classes on the action ---------------------------------------------


def test_disclosed_classes_of_sorts_and_names_each_class_once():
    assert disclosed_classes_of(["phone", "email", "phone"]) == ("email", "phone")
    for value in (None, [], "email", ["email", 3], ["email", ""], {"email": True}):
        assert disclosed_classes_of(value) is None, value


def test_a_class_outside_the_closed_set_drops_the_whole_value_so_no_data_is_sealed():
    # The producer's free text in place of a class name may be the data itself.
    for value in (["email", "user@example.invalid"], ["email=user@example.invalid"], ["EMAIL"]):
        assert disclosed_classes_of(value) is None, value
    out = _role(ROLE_1_1, _share(classes=disclosed_classes_of(["email", "user@example.invalid"])))
    assert out.result == "fail"
    assert "example" not in out.reason and "example" not in str(out.evidence)


@pytest.mark.parametrize("value", [(), ("phone", "email"), ("email", "email"), ("",), ["email"],
                                   ("email=user@example.invalid",)])
def test_an_action_refuses_disclosed_classes_not_in_their_one_shape(value):
    with pytest.raises(ValueError, match="disclosed_classes"):
        _share(classes=value)


def test_disclosed_classes_are_sealed_and_read_back_from_the_capsule():
    payload = act_payload(_share(classes=("email", "phone")))
    assert payload["disclosed_classes"] == ["email", "phone"]
    assert "disclosed_classes" not in act_payload(_share(classes=None))
    capsule = {"action_id": "share_contact/platform-test", "asg_payload": payload}
    assert Action.from_capsule(capsule).disclosed_classes == ("email", "phone")


def _bridged(body: dict) -> Action | None:
    disclosed = {"x-deal-v0": {"record_type": "check"},
                 "body": {"action": "share_contact", "action_class": "disclosure.personal", "taxonomy_version": "6",
                          **body}}
    record = {"operator": "deal", "developer": "capsulectl-deal", "action_id": "deal/1",
              "model_attestation": {"compute_attestation": {"agent_input_digest": json_digest(disclosed)}}}
    return action_for_record(record, disclosed)


def test_a_deal_check_carries_its_disclosing_classes():
    action = _bridged({"disclosing": ["phone", "email"], "recipient_role": "platform"})
    assert (action.recipient_role, action.disclosed_classes) == ("platform", ("email", "phone"))
    for value in ("email", [], [1], None, ["email", "user@example.invalid"]):
        assert _bridged({"disclosing": value}).disclosed_classes is None, value


# -- the gate and s13 leave a platform to recipient_role --------------------------


def _gate(definition, action: Action):
    return check_action_class_gate(action, selectors=definition.config["selectors"]).constraint


def test_gate_1_1_leaves_a_disclosure_to_a_platform_out_and_flags_every_other():
    out = _gate(GATE_1_1, _share())
    assert out.result == "n/a"
    assert out.reason == "the selectors naming action class 'disclosure.personal' leave out recipient_role 'platform'"
    for role in ("fulfilling_merchant", "third_party", "self", None):
        assert _gate(GATE_1_1, _share(role)).result == "fail", role
    assert _gate(GATE_1_0, _share()).result == "fail"


def test_every_class_the_gate_leaves_to_recipient_role_is_one_recipient_role_reads():
    """A platform share of a class recipient_role/1.1.0 does not read would be
    measured by no rule; under the pinned taxonomy the two sets agree."""
    from capsule_engine.guards.classes import TAXONOMY
    selector = GATE_1_1.config["selectors"]["personal_disclosure"]
    gated = {name for name, ac in TAXONOMY.items() if ac.trigger_class in selector["trigger_classes"]}
    assert gated <= set(ROLE_1_1.config["action_classes"]) == set(SELLER_ROLE_1_1.config["action_classes"])


def test_gate_1_1_differs_from_1_0_only_in_that_exception():
    old, new = GATE_1_0.config["selectors"], GATE_1_1.config["selectors"]
    assert new["personal_disclosure"].pop("except_recipient_roles") == ["platform"]
    assert new == old


@pytest.mark.parametrize("selector,message", [
    ({"except_recipient_roles": ["platform"], "on_match": "fail"}, "selects nothing"),
    ({"trigger_classes": ["DISCLOSE"], "except_recipient_roles": "platform", "on_match": "fail"}, "non-empty list"),
    ({"trigger_classes": ["DISCLOSE"], "except_recipient_roles": [], "on_match": "fail"}, "non-empty list"),
])
def test_an_exception_must_narrow_a_selector_with_a_list_of_roles(selector, message):
    with pytest.raises(ValueError, match=message):
        check_action_class_gate(_share(), selectors={"s": selector})


@pytest.fixture
def ledger(tmp_path):
    store = MemoryHistory()
    yield store
    store.close()


def _release(definition, action: Action, ledger: MemoryHistory):
    config = definition.config
    return check_release_on_acceptance(
        action, ledger, release_classes=config["release_classes"],
        acceptance_classes=config["acceptance_classes"], action_classes=config["action_classes"],
        except_recipient_roles=config.get("except_recipient_roles")).constraint


def test_s13_1_1_does_not_apply_to_a_platform_and_holds_everyone_else(ledger):
    out = _release(RELEASE_1_1, _share(representation_class="other"), ledger)
    assert (out.result, out.reason) == ("n/a", "the recipient is a platform, not a buyer; recipient_role measures it")
    assert _release(RELEASE_1_0, _share(representation_class="other"), ledger).result == "fail"
    for role in ("buyer", "third_party", None):
        assert _release(RELEASE_1_1, _share(role, representation_class="other"), ledger).result == "fail", role


def test_s13_1_1_still_refuses_a_platform_disclosure_whose_class_is_unknown(ledger):
    out = _release(RELEASE_1_1, _share(action_class="household.unlisted"), ledger)
    assert (out.result, out.evidence["missing_field"]) == ("fail", "action_class")


# -- the installed packs ---------------------------------------------------------


def _decide(pack: str, actions: list[Action], tmp_path: Path):
    installed = install_pack(load_pack_dir(ROOT / "packs" / "catalog" / pack), project_dir=tmp_path / "project",
                             mode="observe")
    signer = LocalSigner(key_id="platform-test-key", secret=b"platform-test-fixed-key")
    store = MemoryHistory()
    try:
        engine = build_engine(installed, history=store, signer_provider=lambda: signer)
        return installed.pack, [engine.check(a, dry_run=True) for a in actions]
    finally:
        store.close()


def _rules(pack, decision) -> dict[str, str]:
    return {r.obligation_id: r.result for r in obligation_results(pack, decision.constraints)}


def test_everyday_allows_an_email_to_a_marketplace_and_refuses_its_address_or_phone(tmp_path):
    """The listing case: the user's email to the marketplace a listing goes
    through. 0.3.6 refused it on r03; 0.3.7 allows it with every rule passing
    or not applicable, and still refuses a phone or a home address to it."""
    actions = [replace(_share(classes=c), action_id=f"share_contact/listing-{n}", target=f"platform/{n}")
               for n, c in enumerate([("email",), ("phone",), ("home_address",)])]
    pack, (email, phone, address) = _decide("everyday", actions, tmp_path)
    assert email.outcome == ALLOW
    rules = _rules(pack, email)
    assert rules["r03-shipping-address-to-fulfilling-merchant"] == "pass"
    assert rules["r11-home-address-to-an-individual"] == rules["r12-personal-contact-to-a-new-party"] == "n/a"
    for decision in (phone, address):
        assert decision.outcome == DENY
        assert [c.id for c in decision.constraints if c.result == "fail"] == ["recipient_role"]


def test_frozen_everyday_0_3_6_still_loads_at_its_digest_and_refuses_the_email(tmp_path):
    frozen = Path(__file__).parent / "fixtures" / "packs" / "everyday-0.3.6"
    pack = load_pack_dir(frozen)
    assert (pack.pack_id, pack.definition_digest()) == (
        "asg/everyday/0.3.6", "8a8e0a7e1ca4319b6e45f2037f4af49e6592919f373e59874ce1ead7249b073d")
    installed = install_pack(pack, project_dir=tmp_path / "project", mode="observe")
    signer = LocalSigner(key_id="platform-test-key", secret=b"platform-test-fixed-key")
    store = MemoryHistory()
    try:
        decision = build_engine(installed, history=store, signer_provider=lambda: signer).check(_share(), dry_run=True)
    finally:
        store.close()
    assert decision.outcome == DENY
    assert _rules(pack, decision)["r03-shipping-address-to-fulfilling-merchant"] == "fail"


def test_everyday_still_asks_on_a_disclosure_to_the_merchant(tmp_path):
    pack, (decision,) = _decide("everyday", [_share("fulfilling_merchant", ("home_address",))], tmp_path)
    assert decision.outcome == ESCALATE
    assert _rules(pack, decision)["r11-home-address-to-an-individual"] == "fail"


def test_seller_allows_an_email_to_the_platform_before_any_acceptance(tmp_path):
    actions = [replace(_share(classes=c, representation_class="other"), action_id=f"share_contact/sale-{n}",
                       target=f"platform/{n}") for n, c in enumerate([("email",), ("phone",)])]
    _, (email, phone) = _decide("seller", actions, tmp_path)
    assert email.outcome == ALLOW
    assert phone.outcome == DENY
    assert [c.id for c in phone.constraints if c.result == "fail"] == ["recipient_role"]
