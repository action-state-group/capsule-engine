# SPDX-License-Identifier: Apache-2.0
"""action_class_gate: a selector over the action taxonomy, with no fold and
no field beyond ``Action.action_class``. Each shipped selector is shown
firing on its own class and not on a neighbouring one."""
from __future__ import annotations

from pathlib import Path

import pytest

from capsule_engine.guards import Action, GuardEngine
from capsule_engine.guards.capsule import not_applicable_evidence
from capsule_engine.guards.checks import CONFIGURED_CHECKS, check_action_class_gate
from capsule_engine.guards.checks.action_class_gate import Selector, parse_selectors
from capsule_engine.guards.classes import TAXONOMY_VERSION
from capsule_engine.guards.wickets import load_definition_file

CATALOG = Path(__file__).parent.parent / "capsule_engine" / "guards" / "wickets" / "catalog_defs"
GATE = load_definition_file(CATALOG / "action_class_gate.yaml")
SELECTORS = GATE.config["selectors"]


def _action(action_class: str | None) -> Action:
    return Action(verb="act", operator="household", developer="assistant@v1", action_class=action_class)


def _gate(action_class: str | None, selectors: dict[str, Selector] = SELECTORS):
    return check_action_class_gate(_action(action_class), selectors=selectors).constraint


# (selector id, a class it must fire on, a neighbouring class it must not fire on)
SHIPPED = [
    ("non_consequential", "info.query", "communication.send"),
    ("public_posting", "communication.publish", "communication.send"),
    ("booking_create", "booking.create", "booking.modify"),
    ("booking_cancel", "booking.cancel", "booking.modify"),
    ("destructive_mutation", "data.delete", "account.security_change"),
    ("personal_disclosure", "disclosure.personal", "disclosure.secret"),
]


def test_the_catalog_ships_exactly_the_tested_selectors():
    assert set(SELECTORS) == {sid for sid, _, _ in SHIPPED}


@pytest.mark.parametrize("selector_id,fires_on,neighbour", SHIPPED)
def test_each_selector_fires_on_its_class_and_not_on_a_neighbour(selector_id, fires_on, neighbour):
    hit = _gate(fires_on)
    assert hit.evidence["matched_selectors"] == [selector_id]
    assert hit.result == SELECTORS[selector_id]["on_match"]
    miss = _gate(neighbour)
    assert selector_id not in miss.evidence.get("matched_selectors", [])
    assert (miss.result, miss.evidence) == ("n/a", not_applicable_evidence("action_class_gate", in_scope=False))


def test_an_ask_selector_fails_and_the_non_consequential_selector_passes():
    assert _gate("communication.publish").result == "fail"
    assert _gate("info.query").result == "pass"


def test_a_class_no_selector_names_is_out_of_scope():
    out = _gate("money.purchase")
    assert out.result == "n/a"
    assert out.evidence == not_applicable_evidence("action_class_gate", in_scope=False)


def test_matched_evidence_is_facts_only_and_names_the_resolved_class():
    out = _gate("disclosure.personal")
    assert out.evidence == {
        "action_class": "disclosure.personal",
        "trigger_class": "DISCLOSE",
        "consequential": True,
        "taxonomy_version": TAXONOMY_VERSION,
        "matched_selectors": ["personal_disclosure"],
    }


def test_a_legacy_alias_resolves_to_its_canonical_class():
    selectors = {"outbound_message": {"action_classes": ["communication.send"], "on_match": "fail"}}
    out = _gate("comms.external", selectors)
    assert out.result == "fail"
    assert out.evidence["action_class"] == "communication.send"
    assert out.evidence["matched_selectors"] == ["outbound_message"]


@pytest.mark.parametrize("action_class", ["payments.teleport", None, "", "INFO.QUERY", " info.query"])
def test_a_class_missing_from_the_taxonomy_fails_closed(action_class):
    out = _gate(action_class)
    assert out.result == "fail"
    assert out.evidence == {"action_class": action_class, "in_taxonomy": False, "taxonomy_version": TAXONOMY_VERSION}


@pytest.mark.parametrize(
    "selector,message",
    [
        ({"action_classes": ["payments.teleport"], "on_match": "fail"}, "not a canonical class"),
        # legacy name: config must be canonical
        ({"action_classes": ["comms.external"], "on_match": "fail"}, "not a canonical class"),
        # state-derived, no row declares it
        ({"trigger_classes": ["CHANGE"], "on_match": "fail"}, "no taxonomy row declares it"),
        ({"trigger_classes": ["SPEND"], "on_match": "fail"}, "no taxonomy row declares it"),
    ],
)
def test_a_selector_the_taxonomy_cannot_resolve_raises(selector, message):
    with pytest.raises(ValueError, match=message):
        _gate("info.query", {"bad": selector})


@pytest.mark.parametrize(
    "selectors,message",
    [
        ({"bad": {"on_match": "fail"}}, "selects nothing"),
        ({"bad": {"action_classes": "info.query", "on_match": "fail"}}, "non-empty list of names"),
        ({"bad": {"action_classes": [["info.query"]], "on_match": "fail"}}, "non-empty list of names"),
        ({"bad": {"trigger_classes": [], "on_match": "fail"}}, "non-empty list of names"),
        ({"bad": {"consequential": "no", "on_match": "fail"}}, "true or false"),
        ({"bad": {"action_classes": ["info.query"], "on_match": "escalate"}}, "on_match"),
        ({"bad": {"action_classes": ["info.query"], "on_match": "fail", "extra": 1}}, "unknown keys"),
        ({1: {"action_classes": ["info.query"], "on_match": "fail"}}, "selector id"),
        ({}, "non-empty mapping"),
    ],
)
def test_a_malformed_selector_raises(selectors, message):
    with pytest.raises(ValueError, match=message):
        _gate("info.query", selectors)


def test_the_shipped_selectors_parse():
    assert parse_selectors(SELECTORS) == SELECTORS


def test_selector_keys_are_and_combined():
    selectors = {"commit_money_in": {"trigger_classes": ["COMMIT"], "action_classes": ["money.refund"], "on_match": "fail"}}
    assert _gate("money.refund", selectors).result == "fail"
    assert _gate("money.purchase", selectors).result == "n/a"


def test_a_fail_selector_outranks_a_pass_selector():
    selectors = {
        "anything_consequential": {"consequential": True, "on_match": "pass"},
        "public_posting": {"action_classes": ["communication.publish"], "on_match": "fail"},
    }
    out = _gate("communication.publish", selectors)
    assert out.result == "fail"
    assert out.evidence["matched_selectors"] == ["anything_consequential", "public_posting"]


def test_engine_runs_the_gate_as_a_configured_check(store, caps_fold, signer):
    assert "action_class_gate" in CONFIGURED_CHECKS
    engine = GuardEngine(ledger=store, caps_fold=caps_fold, signer_provider=lambda: signer, wickets=(GATE,))
    decision = engine.check(
        Action(verb="post", operator="household", developer="assistant@v1", action_class="communication.publish"),
        dry_run=True,
    )
    gate = [c for c in decision.constraints if c.id == "action_class_gate"]
    assert [c.result for c in gate] == ["fail"]
    assert decision.outcome == "deny"
