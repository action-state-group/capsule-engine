# SPDX-License-Identifier: Apache-2.0
"""seller.destination_rail/1.1.0 (capsule-engine#192): the payment rail is
checked at the seller's commit (agreement.accept) as well as on a sale,
cash passes only when the item is handed over in person, the payment apps
count as p2p, and a commit that states no rail fails. 1.0.0 decides, and
records, exactly as before. The handover reaches the check from the
commit's ``terms.handover`` through the bridge, and is sealed as a closed
token."""
from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from pathlib import Path

import pytest
from agent_action_capsule import json_digest
from cll.ledger import LedgerStore

from capsule_engine.folds.loader import load_definition_file as load_fold
from capsule_engine.guards import Action, GuardEngine, LocalSigner
from capsule_engine.guards.action import HANDOVERS
from capsule_engine.guards.capsule import act_payload, not_applicable_evidence
from capsule_engine.guards.checks import CONFIGURED_CHECKS
from capsule_engine.guards.checks.destination_rail import MISSING_RAIL_REASON
from capsule_engine.guards.wickets import WicketDefinition, load_definition_file
from capsule_engine.report.replay import action_for_record

CATALOG = Path(__file__).parent.parent / "capsule_engine" / "guards" / "wickets" / "catalog_defs"
RAIL_1_0 = load_definition_file(CATALOG / "destination_rail.seller.yaml")
RAIL_1_1 = load_definition_file(CATALOG / "destination_rail.seller.v1.1.yaml")
SPEND = load_fold(Path(__file__).parent.parent / "capsule_engine" / "folds" / "catalog_defs" / "spend.weekly.v3.yaml")

COMMIT = "agreement.accept"
SALE = "marketplace.sale"


def _action(action_class: str = COMMIT, **fields) -> Action:
    return Action(verb="commit", operator="seller", developer="assistant@v1", action_class=action_class,
                  amount_minor=175_000, currency="USD", target="buyer/ref-7", **fields)


def _run(definition: WicketDefinition, action: Action):
    return CONFIGURED_CHECKS[definition.check](action, None, definition.config).constraint


def test_the_definition_is_the_ruled_config():
    assert RAIL_1_1.wicket_id == "seller.destination_rail/1.1.0"
    assert RAIL_1_1.check == RAIL_1_0.check == "destination_rail"
    assert RAIL_1_1.config == {
        "action_classes": [COMMIT, SALE],
        "allowed_rails": ["card", "p2p"],
        "in_person_rails": ["cash"],
        "rail_aliases": {"venmo": "p2p", "zelle": "p2p", "cash_app": "p2p", "paypal": "p2p"},
        "missing_rail": {COMMIT: "fail"},
    }
    assert RAIL_1_1.semantics is not None and MISSING_RAIL_REASON in RAIL_1_1.semantics


# The cases of the design note (capsule-engine#194 §4), at the check.
CASES = [
    ("cash in person at commit", dict(rail="cash", handover="in_person"), COMMIT, "pass"),
    ("cash shipped", dict(rail="cash", handover="shipped"), COMMIT, "fail"),
    ("cash with no handover", dict(rail="cash"), COMMIT, "fail"),
    ("an unlisted rail at commit", dict(rail="cheque"), COMMIT, "fail"),
    ("card at commit", dict(rail="card"), COMMIT, "pass"),
    ("p2p at commit", dict(rail="p2p"), COMMIT, "pass"),
    ("venmo at commit", dict(rail="venmo"), COMMIT, "pass"),
    ("zelle at commit", dict(rail="zelle"), COMMIT, "pass"),
    ("cash_app at commit", dict(rail="cash_app"), COMMIT, "pass"),
    ("paypal at commit", dict(rail="paypal"), COMMIT, "pass"),
    ("venmo on a sale", dict(rail="venmo"), SALE, "pass"),
    ("zelle on a sale", dict(rail="zelle"), SALE, "pass"),
    ("cash in person on a sale", dict(rail="cash", handover="in_person"), SALE, "pass"),
    ("cash shipped on a sale", dict(rail="cash", handover="shipped"), SALE, "fail"),
    ("an unlisted rail on a sale", dict(rail="cheque"), SALE, "fail"),
    ("no rail at commit", {}, COMMIT, "fail"),
    ("no rail on a sale", {}, SALE, "n/a"),
    ("an offer", dict(rail="cash"), "marketplace.offer", "n/a"),
]


@pytest.mark.parametrize("fields,action_class,expected", [c[1:] for c in CASES], ids=[c[0] for c in CASES])
def test_each_case_decides_as_the_design_says(fields, action_class, expected):
    assert _run(RAIL_1_1, _action(action_class, **fields)).result == expected


def test_a_commit_stating_no_rail_names_the_gap():
    out = _run(RAIL_1_1, _action(COMMIT))
    assert (out.result, out.reason) == ("fail", "payment method not stated")
    assert out.evidence == {"rail": None, "watched_rails": [], "allowed_rails": ["card", "p2p"], "counted_as": None,
                            "in_person_rails": ["cash"], "handover": None, "missing_field": "rail"}


def test_a_sale_stating_no_rail_is_not_applicable_as_before():
    out = _run(RAIL_1_1, _action(SALE))
    assert out.evidence == not_applicable_evidence("destination_rail", in_scope=True, missing_field="rail")


def test_an_alias_is_recorded_as_the_token_read_and_what_it_counted_as():
    out = _run(RAIL_1_1, _action(COMMIT, rail="venmo"))
    assert out.evidence == {"rail": "venmo", "watched_rails": [], "allowed_rails": ["card", "p2p"],
                            "counted_as": "p2p", "in_person_rails": ["cash"], "handover": None}
    assert out.reason == "rail 'venmo' (counted as 'p2p') is one of the allowed rails"


@pytest.mark.parametrize("handover,reason", [
    (None, "rail 'cash' is allowed only when the item is handed over in person; the handover is not stated"),
    ("shipped", "rail 'cash' is allowed only when the item is handed over in person; the handover is 'shipped'"),
])
def test_cash_not_in_person_says_why(handover, reason):
    out = _run(RAIL_1_1, _action(COMMIT, rail="cash", handover=handover))
    assert (out.result, out.reason) == ("fail", reason)
    assert out.evidence["handover"] == handover


def test_cash_in_person_records_the_handover():
    out = _run(RAIL_1_1, _action(COMMIT, rail="cash", handover="in_person"))
    assert out.reason == "rail 'cash' is allowed: the item is handed over in person"
    assert out.evidence == {"rail": "cash", "watched_rails": [], "allowed_rails": ["card", "p2p"],
                            "counted_as": "cash", "in_person_rails": ["cash"], "handover": "in_person"}


# -- 1.0.0 is unchanged ---------------------------------------------------------


@pytest.mark.parametrize("fields,result,evidence", [
    (dict(rail="card"), "pass", {"rail": "card", "watched_rails": [], "allowed_rails": ["card", "p2p"]}),
    (dict(rail="cheque"), "fail", {"rail": "cheque", "watched_rails": [], "allowed_rails": ["card", "p2p"]}),
    # 1.0.0 has no aliases and no in-person rails: venmo and cash fail as they did.
    (dict(rail="venmo"), "fail", {"rail": "venmo", "watched_rails": [], "allowed_rails": ["card", "p2p"]}),
    (dict(rail="cash", handover="in_person"), "fail",
     {"rail": "cash", "watched_rails": [], "allowed_rails": ["card", "p2p"]}),
    ({}, "n/a", not_applicable_evidence("destination_rail", in_scope=True, missing_field="rail")),
])
def test_1_0_0_decides_a_sale_as_before(fields, result, evidence):
    out = _run(RAIL_1_0, _action(SALE, **fields))
    assert (out.result, out.evidence) == (result, evidence)


def test_1_0_0_does_not_run_at_the_commit():
    out = _run(RAIL_1_0, _action(COMMIT))
    assert out.evidence == not_applicable_evidence("destination_rail", in_scope=False)


# -- one mutant per config field ------------------------------------------------

MUTANTS = [
    ("action_classes", dict(rail="cheque"), [SALE], "fail", "n/a"),
    ("allowed_rails", dict(rail="card"), ["p2p"], "pass", "fail"),
    ("in_person_rails", dict(rail="cash", handover="in_person"), [], "pass", "fail"),
    ("rail_aliases", dict(rail="venmo"), {}, "pass", "fail"),
    ("missing_rail", {}, {}, "fail", "n/a"),
]


@pytest.mark.parametrize("field,fields,mutated,before,after", MUTANTS, ids=[m[0] for m in MUTANTS])
def test_each_config_field_moves_the_result(field, fields, mutated, before, after):
    action = _action(COMMIT, **fields)
    assert _run(RAIL_1_1, action).result == before
    mutant = dataclasses.replace(RAIL_1_1, config={**RAIL_1_1.config, field: mutated})
    assert _run(mutant, action).result == after


def test_every_config_field_has_a_mutant():
    assert {m[0] for m in MUTANTS} == set(RAIL_1_1.config)


# -- the handover on the action and the capsule ------------------------------------


def test_the_handover_set_is_closed():
    assert HANDOVERS == {"in_person", "shipped"}
    with pytest.raises(ValueError):
        _action(COMMIT, handover="the parking lot")


def test_the_handover_is_sealed_only_when_set_and_read_back(tmp_path):
    store = LedgerStore(tmp_path / "ledger")
    try:
        signer = LocalSigner(key_id="rail-key", secret=b"rail-test-key")
        engine = GuardEngine(ledger=store, caps_fold=SPEND, signer_provider=lambda: signer, wickets=(RAIL_1_1,))
        bare = engine.check(_action(COMMIT, rail="card", action_id="commit/bare")).capsule
        assert "handover" not in bare["asg_payload"]
        sealed = engine.check(_action(COMMIT, rail="cash", handover="in_person", action_id="commit/cash")).capsule
        assert sealed["asg_payload"]["handover"] == "in_person"
        assert {c["id"]: c["result"] for c in sealed["constraints"]}["destination_rail"] == "pass"
        assert Action.from_capsule(sealed).handover == "in_person"
    finally:
        store.close()
    assert act_payload(_action(COMMIT, handover="shipped"))["handover"] == "shipped"


@pytest.mark.parametrize("value", ["drone", {}, [], ["in_person"], {"in_person": True}, 1, True, None])
def test_a_foreign_capsule_with_a_handover_not_in_the_set_reads_none(value):
    """A non-string (an unhashable container included) is missing, never a
    TypeError, and a cash commit replayed from it then asks."""
    capsule = {"action_id": "commit/x", "asg_payload": {"action_class": COMMIT, "rail": "cash", "handover": value}}
    action = Action.from_capsule(capsule)
    assert action.handover is None
    assert _run(RAIL_1_1, action).result == "fail"


@pytest.mark.parametrize("value", [{}, [], ["in_person"]])
def test_an_action_refuses_a_handover_container(value):
    with pytest.raises(ValueError):
        _action(COMMIT, handover=value)


# -- the bridge: terms.handover on the check body --------------------------------


# ``body`` holds values in any shape on purpose: the bridge is what filters them.
def _bridged(body: Mapping[str, object]) -> Action:
    disclosed = {"x-deal-v0": {"record_type": "check"},
                 "body": {"action": "commit", "action_class": COMMIT, "taxonomy_version": "6", **body}}
    record = {"operator": "deal", "developer": "capsulectl-deal", "action_id": "deal/1",
              "model_attestation": {"compute_attestation": {"agent_input_digest": json_digest(disclosed)}}}
    action = action_for_record(record, disclosed)
    assert action is not None
    return action


@pytest.mark.parametrize("handover", ["in_person", "shipped"])
def test_the_bridge_carries_the_commits_handover(handover):
    action = _bridged({"terms": {"item": "bicycle", "handover": handover}, "recourse": {"rail": "cash"}})
    assert (action.rail, action.handover) == ("cash", handover)


@pytest.mark.parametrize("terms", [None, {"item": "bicycle"}, {"handover": "drone"}, {"handover": 1}, "in_person",
                                   {"handover": {}}, {"handover": []}, {"handover": ["in_person"]}])
def test_the_bridge_drops_a_handover_not_in_the_closed_set(terms):
    body = {"recourse": {"rail": "cash"}} if terms is None else {"terms": terms, "recourse": {"rail": "cash"}}
    action = _bridged(body)
    assert action.handover is None
    assert _run(RAIL_1_1, action).result == "fail"


def test_a_bridged_cash_commit_in_person_passes():
    action = _bridged({"terms": {"handover": "in_person"}, "recourse": {"rail": "cash", "refundable": False}})
    assert _run(RAIL_1_1, action).result == "pass"
