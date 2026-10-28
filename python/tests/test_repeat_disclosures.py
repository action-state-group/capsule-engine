# SPDX-License-Identifier: Apache-2.0
"""A repeat of the same detail to the same party passes (everyday 0.3.8, #188).

action_class_gate/1.2.0 splits personal_disclosure in two. r11
(personal_disclosure_new_class) is not matched when every class disclosed is
in ``repeat_disclosures``; r12 (personal_disclosure_new_party) is not matched
when ``recipient_told_before`` is true. Both inputs come from the producer's
telling memory, are read from the check body and are never sealed on the
decision. A missing or malformed input is no exception, so it still asks.
Each case asserts which rules fail, not only the outcome."""
from __future__ import annotations

from pathlib import Path

import pytest
from agent_action_capsule import json_digest

from capsule_engine.guards import Action, LocalSigner
from capsule_engine.guards.action import repeat_disclosures_of
from capsule_engine.guards.capsule import ALLOW, ESCALATE, act_payload
from capsule_engine.guards.checks import check_action_class_gate
from capsule_engine.guards.history import MemoryHistory
from capsule_engine.guards.wickets import load_definition_file
from capsule_engine.packs import build_engine, install_pack, load_pack_dir
from capsule_engine.packs.obligation_results import obligation_results
from capsule_engine.report.replay import action_for_record

ROOT = Path(__file__).parent.parent / "capsule_engine"
CATALOG = ROOT / "guards" / "wickets" / "catalog_defs"
GATE_1_1 = load_definition_file(CATALOG / "action_class_gate.v1.1.yaml")
GATE_1_2 = load_definition_file(CATALOG / "action_class_gate.v1.2.yaml")
EVERYDAY = ROOT / "packs" / "catalog" / "everyday"
FROZEN_0_3_7 = Path(__file__).parent / "fixtures" / "packs" / "everyday-0.3.7"

R11 = "r11-home-address-to-an-individual"
R12 = "r12-personal-contact-to-a-new-party"
OTHER_GATE_RULES = ("r01-research-and-prepare", "r04-prepare-without-committing", "r15-public-posting",
                    "r16-booking-with-a-commitment", "r17-cancellation", "r18-delete-persistent-data")
UNSET = object()


def _share(classes=("email",), repeat=UNSET, told=UNSET, *, n: int = 0, role="fulfilling_merchant", **fields):
    if repeat is not UNSET:
        fields["repeat_disclosures"] = repeat
    if told is not UNSET:
        fields["recipient_told_before"] = told
    return Action(verb="share_contact", operator="repeat-test", developer="assistant@v1",
                  action_class=fields.pop("action_class", "disclosure.personal"), target=f"merchant/{n}",
                  recipient_role=role, disclosed_classes=classes, action_id=f"share_contact/repeat-{n}",
                  timestamp=f"2026-10-22T10:{n:02d}:00Z", **fields)


def _gate(definition, action: Action):
    return check_action_class_gate(action, selectors=definition.config["selectors"]).constraint


# -- the gate --------------------------------------------------------------------


@pytest.mark.parametrize(("action", "matched"), [
    # email to merchant M, told nothing: a new class and a new party.
    (_share(("email",), (), False), ["personal_disclosure_new_class", "personal_disclosure_new_party"]),
    # again: every class told before, to a party told before.
    (_share(("email",), ("email",), True), []),
    # phone to M after the email: a new class to a party told before.
    (_share(("phone",), (), True), ["personal_disclosure_new_class"]),
    # email and phone to M: one class is new.
    (_share(("email", "phone"), ("email",), True), ["personal_disclosure_new_class"]),
    # a party told before, with no repeat list: the class is not known to repeat.
    (_share(("email",), UNSET, True), ["personal_disclosure_new_class"]),
    # a repeat list with no told-before flag: the party is not known to be told.
    (_share(("email",), ("email",), UNSET), ["personal_disclosure_new_party"]),
    # nothing reported: both, as 1.1.0.
    (_share(("email",)), ["personal_disclosure_new_class", "personal_disclosure_new_party"]),
    # no class named: a repeat of nothing is no repeat.
    (_share(None, ("email",), True), ["personal_disclosure_new_class"]),
    (_share(("email",), (), False, n=1, role=None), ["personal_disclosure_new_class", "personal_disclosure_new_party"]),
])
def test_gate_1_2_matches_a_new_class_and_a_new_party_apart(action, matched):
    out = _gate(GATE_1_2, action)
    if matched:
        assert (out.result, out.evidence["matched_selectors"]) == ("fail", matched)
    else:
        assert out.result == "n/a"


def test_gate_1_2_leaves_a_platform_to_recipient_role_with_1_1_s_reason():
    out = _gate(GATE_1_2, _share(("email",), (), False, role="platform"))
    assert (out.result, out.reason) == (
        "n/a", "the selectors naming action class 'disclosure.personal' leave out recipient_role 'platform'")


def test_gate_1_2_says_why_a_repeat_is_left_out_without_naming_a_value():
    out = _gate(GATE_1_2, _share(("email",), ("email",), True))
    assert out.reason == ("the selectors naming action class 'disclosure.personal' leave this action out: "
                          "every class it discloses was told to this recipient before; this recipient was told something before")
    assert "email" not in out.reason


def test_gate_1_1_ignores_the_telling_inputs():
    for action in (_share(("email",), ("email",), True), _share(("email",))):
        out = _gate(GATE_1_1, action)
        assert (out.result, out.evidence["matched_selectors"]) == ("fail", ["personal_disclosure"])


def test_gate_1_2_differs_from_1_1_only_in_the_split():
    old, new = GATE_1_1.config["selectors"], dict(GATE_1_2.config["selectors"])
    new_class, new_party = new.pop("personal_disclosure_new_class"), new.pop("personal_disclosure_new_party")
    assert new_class.pop("except_repeat_disclosures") is True
    assert new_party.pop("except_recipient_told_before") is True
    disclosure = old.pop("personal_disclosure")
    assert new_class == new_party == disclosure
    assert new == old


@pytest.mark.parametrize("key", ["except_repeat_disclosures", "except_recipient_told_before"])
@pytest.mark.parametrize("value", [False, "true", 1, ["email"]])
def test_a_telling_exception_is_true_when_set(key, value):
    with pytest.raises(ValueError, match=f"{key} must be true when set"):
        check_action_class_gate(_share(), selectors={"s": {"trigger_classes": ["DISCLOSE"], key: value,
                                                           "on_match": "fail"}})


# -- the Action and the bridge -----------------------------------------------------


def test_repeat_disclosures_of_keeps_known_classes_sorted_and_allows_none():
    assert repeat_disclosures_of(["phone", "email", "phone"]) == ("email", "phone")
    assert repeat_disclosures_of([]) == ()
    for value in (None, "email", [1], ["email", "user@example.invalid"], {"email": True}):
        assert repeat_disclosures_of(value) is None, value


@pytest.mark.parametrize("fields", [
    {"repeat_disclosures": ("phone", "email")},
    {"repeat_disclosures": ("user@example.invalid",)},
    {"repeat_disclosures": ["email"]},
    {"recipient_told_before": "yes"},
    {"recipient_told_before": 1},
])
def test_an_action_refuses_telling_inputs_not_in_their_shape(fields):
    with pytest.raises(ValueError):
        _share(("email",), **fields)


def _bridged(body: dict) -> Action | None:
    disclosed = {"x-deal-v0": {"record_type": "check"},
                 "body": {"action": "share_contact", "action_class": "disclosure.personal", "taxonomy_version": "6",
                          "disclosing": ["email"], "recipient_role": "fulfilling_merchant", **body}}
    record = {"operator": "deal", "developer": "capsulectl-deal", "action_id": "deal/1",
              "model_attestation": {"compute_attestation": {"agent_input_digest": json_digest(disclosed)}}}
    return action_for_record(record, disclosed)


def test_a_deal_check_carries_its_telling_inputs():
    action = _bridged({"repeat_disclosures": ["email"], "recipient_told_before": True})
    assert (action.repeat_disclosures, action.recipient_told_before) == (("email",), True)
    action = _bridged({"repeat_disclosures": [], "recipient_told_before": False})
    assert (action.repeat_disclosures, action.recipient_told_before) == ((), False)
    absent = _bridged({})
    assert (absent.repeat_disclosures, absent.recipient_told_before) == (None, None)


@pytest.mark.parametrize("body", [
    {"repeat_disclosures": ["email", "user@example.invalid"], "recipient_told_before": "true"},
    {"repeat_disclosures": "email", "recipient_told_before": 1},
    {"repeat_disclosures": [None], "recipient_told_before": None},
])
def test_a_malformed_telling_input_is_dropped_so_both_rules_still_ask(body):
    action = _bridged(body)
    assert (action.repeat_disclosures, action.recipient_told_before) == (None, None)
    assert _gate(GATE_1_2, action).evidence["matched_selectors"] == [
        "personal_disclosure_new_class", "personal_disclosure_new_party"]


def test_the_telling_inputs_are_never_sealed_on_the_decision():
    payload = act_payload(_share(("email",), ("email",), True))
    assert "repeat_disclosures" not in payload and "recipient_told_before" not in payload
    assert payload == act_payload(_share(("email",)))


# -- everyday 0.3.8 ----------------------------------------------------------------


def _decide(pack_dir: Path, actions: list[Action], tmp_path: Path):
    installed = install_pack(load_pack_dir(pack_dir), project_dir=tmp_path / "project", mode="observe")
    signer = LocalSigner(key_id="repeat-test-key", secret=b"repeat-test-fixed-key")
    store = MemoryHistory()
    try:
        engine = build_engine(installed, history=store, signer_provider=lambda: signer)
        return installed.pack, [engine.check(a, dry_run=True) for a in actions]
    finally:
        store.close()


def _results(pack, decision) -> dict[str, str]:
    return {r.obligation_id: r.result for r in obligation_results(pack, decision.constraints)}


def _failed(pack, decision) -> list[str]:
    return sorted(rule for rule, result in _results(pack, decision).items() if result == "fail")


# The cases of the design note (#194 section 6), as capsulectl seals them:
# what each asks, and on which rules.
CASES = [
    ("email to merchant M asks", _share(("email",), (), False, n=1), ESCALATE, [R11, R12]),
    ("email to M again passes", _share(("email",), ("email",), True, n=2), ALLOW, []),
    ("email to M again in the first deal with M passes", _share(("email",), ("email",), True, n=3), ALLOW, []),
    ("phone to M asks on r11 alone", _share(("phone",), (), True, n=4), ESCALATE, [R11]),
    ("email to N asks", _share(("email",), (), False, n=5), ESCALATE, [R11, R12]),
    ("an earlier deal with N that told N nothing still asks on r12", _share(("email",), (), False, n=6), ESCALATE,
     [R11, R12]),
    ("a missing input asks on both", _share(("email",), n=7), ESCALATE, [R11, R12]),
]


def test_everyday_0_3_8_asks_only_for_what_is_new(tmp_path):
    pack, decisions = _decide(EVERYDAY, [action for _, action, _, _ in CASES], tmp_path)
    assert pack.pack_id == "asg/everyday/0.3.9"
    for (name, _, outcome, failed), decision in zip(CASES, decisions, strict=True):
        assert _failed(pack, decision) == failed, name
        assert decision.outcome == outcome, name
        results = _results(pack, decision)
        for rule in (R11, R12):
            assert results[rule] == ("fail" if rule in failed else "n/a"), (name, rule)
        assert results["r03-shipping-address-to-fulfilling-merchant"] == "pass", name


def test_frozen_everyday_0_3_7_still_loads_at_its_digest_and_asks_on_a_repeat(tmp_path):
    pack = load_pack_dir(FROZEN_0_3_7)
    assert (pack.pack_id, pack.definition_digest()) == (
        "asg/everyday/0.3.7", "c1cea374102bba60de82e16663c550a278ee07a399c5faeb285fe1ea1ea27d4d")
    pack, (decision,) = _decide(FROZEN_0_3_7, [_share(("email",), ("email",), True)], tmp_path)
    assert decision.outcome == ESCALATE
    assert _failed(pack, decision) == [R11, R12]


def _sweep() -> list[Action]:
    """One action of each class a gate rule reads, and some it does not."""
    actions = [_share(action_class=cls, classes=None, n=n, role=None) for n, cls in enumerate(
        ["info.query", "communication.publish", "booking.create", "booking.cancel", "data.delete",
         "household.unlisted", "money.transfer"], start=10)]
    actions += [_share(("email",), (), False, n=20), _share(("email",), ("email",), True, n=21),
                _share(("phone",), (), True, n=22), _share(("email",), n=23),
                _share(("email",), (), False, n=24, role="platform")]
    return actions


def test_the_other_gate_rules_decide_as_under_0_3_7(tmp_path):
    """0.3.8 binds 1.2.0 for every gate rule: r01, r04 and r15-r18 keep their
    selectors, and each takes the result it took under 0.3.7."""
    old_pack, old = _decide(FROZEN_0_3_7, _sweep(), tmp_path / "old")
    new_pack, new = _decide(EVERYDAY, _sweep(), tmp_path / "new")
    old_selectors = {o.id: o.selector for o in old_pack.obligations}
    new_selectors = {o.id: o.selector for o in new_pack.obligations}
    for rule in OTHER_GATE_RULES:
        assert new_selectors[rule] == old_selectors[rule], rule
    for action, before, after in zip(_sweep(), old, new, strict=True):
        before_results, after_results = _results(old_pack, before), _results(new_pack, after)
        for rule in OTHER_GATE_RULES:
            assert after_results[rule] == before_results[rule], (action.action_class, rule)


def test_every_gate_rule_cites_1_2_0():
    pack = load_pack_dir(EVERYDAY)
    (gate,) = [c for c in pack.constraints if c.check == "action_class_gate"]
    assert gate.wicket_id == "action_class_gate/1.2.0"
    assert {o.id: o.selector for o in pack.obligations if o.check == "action_class_gate"} == {
        "r01-research-and-prepare": "non_consequential",
        "r04-prepare-without-committing": "non_consequential",
        R11: "personal_disclosure_new_class",
        R12: "personal_disclosure_new_party",
        "r15-public-posting": "public_posting",
        "r16-booking-with-a-commitment": "booking_create",
        "r17-cancellation": "booking_cancel",
        "r18-delete-persistent-data": "destructive_mutation",
    }


def test_a_repeat_decision_is_replayed_from_its_check_body_not_its_capsule(tmp_path):
    """The engine's own decision does not carry the inputs: replaying the
    capsule alone asks again, while the deal's check body still says repeat."""
    action = _share(("email",), ("email",), True, n=30)
    pack, (decision,) = _decide(EVERYDAY, [action], tmp_path)
    assert decision.outcome == ALLOW
    replayed = Action.from_capsule({"action_id": action.action_id, "asg_payload": act_payload(action)})
    assert (replayed.repeat_disclosures, replayed.recipient_told_before) == (None, None)
    pack, (again,) = _decide(EVERYDAY, [replayed], tmp_path / "replay")
    assert _failed(pack, again) == [R11, R12]
