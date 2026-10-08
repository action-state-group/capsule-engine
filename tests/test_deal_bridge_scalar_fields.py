# SPDX-License-Identifier: Apache-2.0
"""The deal-check bridge (report/replay.py) reads the rail and refundable
from the check body's recourse block, sets the target from the sealed payee
fingerprint, and carries each scalar body field only in the shape the action
takes. Read on a real capsulectl bundle, and on built records."""
from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import TypedDict

from agent_action_capsule import json_digest

from capsule_engine.folds.loader import load_definition_file as load_fold
from capsule_engine.guards.wickets import load_definition_file
from capsule_engine.report.replay import action_for_record, load_disclosed, load_records, replay

PACKAGE = Path(__file__).parent.parent / "capsule_engine"
WICKETS = PACKAGE / "guards" / "wickets" / "catalog_defs"
REFUND = Path(__file__).parent / "fixtures" / "deal-bundles" / "deal-purchase-then-refund.bundle.json"
DIGEST = "ab" * 32


class BundleCapsule(TypedDict):
    capsule_id: str


class Recourse(TypedDict):
    rail: str
    refundable: bool


class PayCheckBody(TypedDict):
    action: str
    recourse: Recourse


class DealBlock(TypedDict):
    record_type: str


SealedCheck = TypedDict("SealedCheck", {"body": PayCheckBody, "x-deal-v0": DealBlock})


# Reads raw bundle JSON: the test's decoding boundary.
def _pay_check() -> tuple[BundleCapsule, SealedCheck]:
    value = json.loads(REFUND.read_text())
    for capsule in value["records"]:
        record = value["disclosures"][capsule["capsule_id"]]["agent_input"]
        if (record.get("x-deal-v0") or {}).get("record_type") == "check" and record["body"]["action"] == "pay":
            return capsule, record
    raise AssertionError("the bundle has no pay check")


def test_a_real_pay_check_carries_its_recourse_rail_and_refundable():
    capsule, record = _pay_check()
    assert "rail" not in record["body"]  # the rail is only in the recourse block
    assert record["body"]["recourse"] == {"rail": "card", "refundable": True}
    action = action_for_record(capsule, record)
    assert (action.rail, action.refundable) == ("card", True)


def test_on_replay_of_the_real_bundle_destination_rail_and_refundability_are_measured():
    rail = load_definition_file(WICKETS / "destination_rail.yaml")
    rail = replace(rail, config={**rail.config, "action_classes": ["money.purchase"]})
    refund = load_definition_file(WICKETS / "refundability.yaml")
    result = replay(
        load_records([REFUND]),
        caps_fold=load_fold(PACKAGE / "folds" / "catalog_defs" / "spend.weekly.yaml"),
        disclosed=load_disclosed([REFUND]),
        wickets=(rail, refund),
    )
    capsule, _ = _pay_check()
    (sourced,) = [d for d in result.decisions if d.record["capsule_id"] == capsule["capsule_id"]]
    results = {c.id: (c.result, c.evidence) for c in sourced.decision.constraints}
    assert results["destination_rail"] == ("pass", {"rail": "card", "watched_rails": ["crypto", "gift_card", "p2p"]})
    assert results["refundability"] == ("pass", {"refundable": True, "rail": "card"})


class Counterparty(TypedDict):
    ids: dict[str, str]
    fp_alg: str


# ``body`` holds values in any shape on purpose: the bridge is what filters them.
def _bridged(body: Mapping[str, object], counterparty: Counterparty | None = None):
    disclosed = {"x-deal-v0": {"record_type": "check", **({"counterparty": counterparty} if counterparty else {})},
                 "body": {"action": "pay", "action_class": "money.purchase", "taxonomy_version": "1", **body}}
    record = {"operator": "deal", "developer": "capsulectl-deal", "action_id": "deal/1",
              "model_attestation": {"compute_attestation": {"agent_input_digest": json_digest(disclosed)}}}
    return action_for_record(record, disclosed)


def test_every_agreed_body_field_is_carried():
    action = _bridged({
        "recourse": {"rail": "card", "refundable": False},
        "recipient_role": "fulfilling_merchant",
        "channel": "whatsapp",
        "first_contact_channel": "marketplace",
        "upfront_amount_minor": 500,
        "material_fields_changed": 1,
        "material_fields_basis": DIGEST,
        "offer_fields_changed": 0,
        "offer_fields_basis": DIGEST,
        "task_authority_ref": {"type": "record", "digest_alg": "SHA-256", "digest": DIGEST},
    })
    assert (action.rail, action.refundable, action.recipient_role) == ("card", False, "fulfilling_merchant")
    assert (action.channel, action.first_contact_channel) == ("whatsapp", "marketplace")
    assert (action.upfront_amount_minor, action.material_fields_changed, action.offer_fields_changed) == (500, 1, 0)
    assert (action.material_fields_basis, action.offer_fields_basis, action.task_authority_ref) == (DIGEST,) * 3


def test_a_value_in_the_wrong_shape_is_dropped_not_coerced():
    action = _bridged({
        "recourse": {"rail": 7, "refundable": "no"},
        "channel": {"kind": "email"},
        "upfront_amount_minor": True,
        "material_fields_changed": "2",
        "task_authority_ref": DIGEST,
    })
    assert (action.rail, action.refundable, action.channel) == (None, None, None)
    assert (action.upfront_amount_minor, action.material_fields_changed, action.task_authority_ref) == (None,) * 3


def test_a_typed_ref_that_is_not_sha256_is_dropped():
    for ref in ({"type": "record", "digest_alg": "SHA-512", "digest": DIGEST},
                {"type": "record", "digest_alg": "SHA-256", "digest": "not-hex"},
                {"digest_alg": "SHA-256", "digest": DIGEST}):
        assert _bridged({"task_authority_ref": ref}).task_authority_ref is None, ref


def test_a_top_level_rail_is_read_when_there_is_no_recourse_rail():
    assert _bridged({"rail": "p2p"}).rail == "p2p"
    assert _bridged({"rail": "p2p", "recourse": {"rail": "card"}}).rail == "card"


def test_the_target_is_the_prefixed_payee_fingerprint_and_absent_without_one():
    action = _bridged({}, counterparty={"ids": {"payee": "cd" * 32}, "fp_alg": "hmac-sha256-deal-key"})
    assert action.target == "payee-fp:hmac-sha256-deal-key:" + "cd" * 32
    assert _bridged({}, counterparty={"ids": {"phone": "cd" * 32}, "fp_alg": "hmac-sha256-deal-key"}).target is None
    assert _bridged({}).target is None
