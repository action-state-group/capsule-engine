"""No reason of an action_class_gate rule names a selector or an action class
(capsule-engine#182). r11 and r12 read "Asks: first time sharing your {class}
with {party}", worded from the role and classes recipient_role recorded; every
other gate rule's reason is a sentence that names neither. go/guard/report.go
words the same sentences (go/guard/gate_reason_182_test.go pins them there)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from capsule_engine.guards import Action, LocalSigner
from capsule_engine.guards.history import MemoryHistory
from capsule_engine.packs import build_engine, install_pack, load_pack_dir, obligation_results
from capsule_engine.packs.obligation_results import (
    GATE_FIRST_SHARE_UNKNOWN,
    GATE_FLAGGED,
    GATE_NOT_APPLICABLE,
    GATE_UNRECOGNISED,
    first_share_reason,
)

PACK_DIR = Path(__file__).parent.parent / "capsule_engine" / "packs" / "catalog" / "everyday"
PACK = load_pack_dir(PACK_DIR)
R11, R12 = "r11-home-address-to-an-individual", "r12-personal-contact-to-a-new-party"
GATE_RULES = [o.id for o in PACK.obligations if o.check == "action_class_gate"]
BANNED = ("selector", "action class", "disclosure.personal", "booking.create", "household.unlisted", "non_consequential")


def _action(n: int, action_class: str, **fields) -> Action:
    return Action(
        verb=f"act_{n}", operator="household-reasons", developer=f"household-assistant-{n}@v1",
        action_class=action_class, target=f"service/reason-{n}", action_id=f"act/gate-reasons-{n}",
        timestamp=f"2026-08-10T11:{n:02d}:00Z", **fields,
    )


ACTIONS = {
    "merchant-phone": _action(1, "disclosure.personal", recipient_role="fulfilling_merchant", disclosed_classes=("phone",)),
    "merchant-two": _action(2, "disclosure.personal", recipient_role="fulfilling_merchant",
                            disclosed_classes=("email", "phone")),
    "third-party": _action(3, "disclosure.personal", recipient_role="third_party",
                           disclosed_classes=("email", "home_address", "phone")),
    "buyer": _action(4, "disclosure.personal", recipient_role="buyer", disclosed_classes=("phone",)),
    "names-no-class": _action(5, "disclosure.personal", recipient_role="fulfilling_merchant"),
    "platform": _action(6, "disclosure.personal", recipient_role="platform", disclosed_classes=("email",)),
    "booking": _action(7, "booking.create", amount_minor=2_000, currency="EUR"),
    "unmapped": _action(8, "household.unlisted"),
}


@pytest.fixture(scope="module")
def results(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("gate-reasons")
    store = MemoryHistory()
    try:
        installed = install_pack(PACK, project_dir=tmp / "project", mode="observe")
        signer = LocalSigner(key_id="gate-reasons-key", secret=b"gate-reasons-fixed-key")
        engine = build_engine(installed, history=store, signer_provider=lambda: signer)
        return {
            name: {r.obligation_id: (r.result, r.reason) for r in obligation_results(PACK, engine.check(a, dry_run=True).constraints)}
            for name, a in ACTIONS.items()
        }
    finally:
        store.close()


@pytest.mark.parametrize(
    ("name", "want"),
    [
        ("merchant-phone", "Asks: first time sharing your phone with the merchant."),
        ("merchant-two", "Asks: first time sharing your email and phone with the merchant."),
        ("third-party", "Asks: first time sharing your email, home address and phone with a third party."),
        ("buyer", "Asks: first time sharing your phone with the buyer."),
        ("names-no-class", "Asks: first time sharing your details with this recipient."),
    ],
)
def test_r11_and_r12_say_who_gets_what(results, name, want):
    assert results[name][R11] == ("fail", want)
    assert results[name][R12] == ("fail", want)


def test_every_other_gate_reason_is_generic(results):
    assert results["booking"]["r16-booking-with-a-commitment"] == ("fail", "Asks: this kind of action needs your approval first.")
    assert results["booking"]["r15-public-posting"] == ("n/a", "This rule does not apply to this action.")
    assert results["platform"][R11] == ("n/a", "This rule does not apply to this action.")
    assert results["unmapped"]["r16-booking-with-a-commitment"] == (
        "fail", "The rules do not recognise this kind of action, so it is held.")
    assert results["merchant-phone"]["r01-research-and-prepare"] == ("n/a", "This rule does not apply to this action.")


def test_no_gate_reason_names_a_selector_or_a_class(results):
    for name, by_rule in results.items():
        for rule in GATE_RULES:
            reason = by_rule[rule][1] or ""
            assert not any(word in reason for word in BANNED), (name, rule, reason)


def test_an_unrecorded_role_or_class_reads_as_details_to_this_recipient():
    unknown = "Asks: first time sharing your details with this recipient."
    assert first_share_reason(None, ["phone"]) == unknown
    assert first_share_reason("buyer", None) == unknown
    assert first_share_reason("buyer", []) == unknown
    assert first_share_reason("buyer", [3]) == unknown
    assert first_share_reason("somebody_new", ["phone"]) == "Asks: first time sharing your phone with this recipient."


TABLE = json.loads((Path(__file__).parents[2] / "conformance-vectors" / "guard" / "reasons" / "gate-reasons.json").read_text())


def test_the_sentences_are_the_shared_tables():
    assert GATE_NOT_APPLICABLE == TABLE["not_applicable"]
    assert GATE_FLAGGED == TABLE["flagged"]
    assert GATE_UNRECOGNISED == TABLE["unrecognised"]
    assert GATE_FIRST_SHARE_UNKNOWN == TABLE["first_share_unknown"]
    for case in TABLE["first_share"]:
        assert first_share_reason(case["role"], case["classes"]) == case["reason"], case


def test_a_check_held_for_another_taxonomy_keeps_its_own_reason(tmp_path):
    store = MemoryHistory()
    try:
        installed = install_pack(PACK, project_dir=tmp_path / "project", mode="observe")
        signer = LocalSigner(key_id="gate-reasons-key", secret=b"gate-reasons-fixed-key")
        engine = build_engine(installed, history=store, signer_provider=lambda: signer)
        decision = engine.check(_action(9, "booking.create", taxonomy_version="1"), dry_run=True)
        by_rule = {r.obligation_id: r.reason for r in obligation_results(PACK, decision.constraints)}
    finally:
        store.close()
    assert decision.taxonomy_mismatch is not None
    reason = by_rule["r16-booking-with-a-commitment"]
    assert "taxonomy" in reason and not any(word in reason for word in BANNED), reason
