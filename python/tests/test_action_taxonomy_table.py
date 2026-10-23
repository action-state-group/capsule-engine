# SPDX-License-Identifier: Apache-2.0
"""The versioned action-class table (guards/action_taxonomy.json): every row
fully declared, legacy names readable through aliases, unknown names refused
by the measurement axis, and the taxonomy version sealed only when set."""
from __future__ import annotations

import copy
import json
from importlib import resources
from pathlib import Path

import pytest

from capsule_engine.guards import Action, LocalSigner
from capsule_engine.guards.capsule import ALLOW, build_decision_capsule
from capsule_engine.guards.classes import (
    TAXONOMY,
    TAXONOMY_VERSION,
    UNCLASSIFIED_DEFAULT,
    UNVERSIONED_TAXONOMY_VERSION,
    TaxonomyDocument,
    TaxonomyRow,
    TaxonomyTableError,
    UnmappedActionClass,
    VersionedPayload,
    check_connector_declaration,
    classify,
    load_taxonomy_table,
    record_taxonomy_version,
    resolve,
    trigger_class,
)
from capsule_engine.packs import load_pack_dir

PAYMENTS_PACK_DIR = Path(__file__).parent.parent / "capsule_engine" / "packs" / "catalog" / "payments-safety"

CANONICAL_ACTIONS = (
    "money.purchase",
    "money.transfer",
    "money.subscription",
    "money.refund",
    "booking.create",
    "booking.modify",
    "booking.cancel",
    "communication.send",
    "communication.publish",
    "disclosure.personal",
    "disclosure.secret",
    "agreement.accept",
    "marketplace.offer",
    "marketplace.sale",
    "data.delete",
    "account.security_change",
    "background.schedule",
    "external_commitment.other",
)
ROW_KEYS = ("name", "trigger_class", "consequential", "fail_open_allowed", "approver_role", "legacy_aliases")


def _raw() -> TaxonomyDocument:
    text = resources.files("capsule_engine.guards").joinpath("action_taxonomy.json").read_text(encoding="utf-8")
    return json.loads(text)


def _row(raw: TaxonomyDocument, name: str) -> TaxonomyRow:
    return next(r for r in raw["actions"] if r["name"] == name)


def test_table_carries_every_canonical_action_and_a_negative_member():
    assert set(TAXONOMY) == {*CANONICAL_ACTIONS, "info.query"}
    assert [n for n, ac in TAXONOMY.items() if not ac.consequential] == ["info.query"]


def test_every_packaged_row_declares_every_key_explicitly():
    for row in _raw()["actions"]:
        assert set(row) == set(ROW_KEYS), row["name"]


@pytest.mark.parametrize("key", ROW_KEYS)
def test_row_missing_a_declaration_is_rejected(key):
    raw = _raw()
    del _row(raw, "booking.cancel")[key]
    with pytest.raises(TaxonomyTableError, match="does not declare"):
        load_taxonomy_table(raw)


def test_consequential_row_may_not_fail_open():
    raw = _raw()
    _row(raw, "money.refund")["fail_open_allowed"] = True
    with pytest.raises(TaxonomyTableError, match="only a non-consequential class may"):
        load_taxonomy_table(raw)


def test_consequential_row_without_trigger_class_is_rejected():
    raw = _raw()
    _row(raw, "external_commitment.other")["trigger_class"] = None
    with pytest.raises(TaxonomyTableError, match="has no trigger_class"):
        load_taxonomy_table(raw)


def test_no_row_may_declare_the_state_derived_class():
    raw = _raw()
    _row(raw, "booking.modify")["trigger_class"] = "CHANGE"
    with pytest.raises(TaxonomyTableError, match="not a declarable trigger class"):
        load_taxonomy_table(raw)


def test_table_without_a_non_consequential_row_is_rejected():
    raw = _raw()
    raw["actions"] = [r for r in raw["actions"] if r["consequential"]]
    with pytest.raises(TaxonomyTableError, match="negative set"):
        load_taxonomy_table(raw)


def test_alias_colliding_with_a_canonical_name_is_rejected():
    raw = _raw()
    _row(raw, "data.delete")["legacy_aliases"] = ["money.transfer"]
    with pytest.raises(TaxonomyTableError, match="collides"):
        load_taxonomy_table(raw)


def test_packaged_table_is_valid_unmutated():
    assert load_taxonomy_table(copy.deepcopy(_raw())).version == TAXONOMY_VERSION


def test_catch_all_declares_its_trigger_class():
    assert _row(_raw(), "external_commitment.other")["trigger_class"] == "COMMIT"


# Policy each pre-table class name carried before the table existed; a
# sealed record holding one of these names must gate exactly as before,
# except where a later taxonomy version changed it on purpose: taxonomy 4
# named the account holder as data.delete's approver, and taxonomy 5 as
# communication.send's (comms.external's).
LEGACY_POLICY = {
    "money.transfer": ("money.transfer", True, False, "treasury-approver", "COMMIT"),
    "data.delete": ("data.delete", True, False, "account_holder", "MUTATE"),
    "comms.external": ("communication.send", True, False, "account_holder", "COMMUNICATE"),
    "info.query": ("info.query", False, True, None, None),
}


@pytest.mark.parametrize("legacy", sorted(LEGACY_POLICY))
def test_legacy_class_names_keep_their_class_flags(legacy):
    name, consequential, fail_open, approver, trigger = LEGACY_POLICY[legacy]
    ac = classify(legacy)
    assert (ac.name, ac.consequential, ac.fail_open_allowed, ac.approver_role) == (
        name,
        consequential,
        fail_open,
        approver,
    )
    assert trigger_class(legacy) == trigger


def test_pack_local_action_type_resolves_through_its_pack_to_a_class():
    pack = load_pack_dir(PAYMENTS_PACK_DIR)
    semantic = next(s for s in pack.action_semantics if s.action_type == "payment.dispatch")
    assert resolve(semantic.action_class) is TAXONOMY["money.transfer"]
    assert trigger_class(semantic.action_class) == "COMMIT"
    # The pack-local name is not itself an engine class; gating it directly
    # stays on the fail-closed default it always had.
    assert classify("payment.dispatch") is UNCLASSIFIED_DEFAULT


def test_unknown_name_gates_fail_closed_but_is_refused_by_measurement():
    assert classify("connector.new_action") is UNCLASSIFIED_DEFAULT
    with pytest.raises(UnmappedActionClass, match="connector.new_action"):
        trigger_class("connector.new_action")


def test_change_is_derived_from_state_not_from_the_name():
    assert trigger_class("booking.modify") == "COMMIT"
    assert trigger_class("booking.modify", after_prior_approval=True) == "CHANGE"
    assert trigger_class("data.delete", after_prior_approval=True) == "CHANGE"
    assert trigger_class("info.query", after_prior_approval=True) is None


def test_connector_declaration_maps_tools_onto_canonical_actions():
    declared = check_connector_declaration({"mail.send": "communication.send", "chat.post": "communication.send"})
    assert {ac.trigger_class for ac in declared.values()} == {"COMMUNICATE"}


@pytest.mark.parametrize("target", ["connector.new_action", "comms.external"])
def test_connector_declaration_refuses_unmapped_or_legacy_targets(target):
    with pytest.raises(UnmappedActionClass, match="mail.forward"):
        check_connector_declaration({"mail.send": "communication.send", "mail.forward": target})


def _sealed(**action_kwargs) -> tuple[str, VersionedPayload]:
    action = Action(
        verb="dispatch_payout",
        operator="op",
        developer="dev@v1",
        action_class="money.transfer",
        action_id="dispatch_payout/taxonomy-version",
        timestamp="2026-10-05T00:00:00Z",
        **action_kwargs,
    )
    capsule = build_decision_capsule(
        action=action,
        outcome=ALLOW,
        constraints=[],
        signer=LocalSigner(key_id="k1", secret=b"s1"),
        checkpoint={},
    )
    return capsule["capsule_id"], capsule["asg_payload"]


def test_taxonomy_version_is_sealed_only_when_set():
    unversioned_id, unversioned = _sealed()
    assert "taxonomy_version" not in unversioned
    assert record_taxonomy_version(unversioned) == UNVERSIONED_TAXONOMY_VERSION

    versioned_id, versioned = _sealed(taxonomy_version=TAXONOMY_VERSION)
    assert versioned["taxonomy_version"] == TAXONOMY_VERSION
    assert record_taxonomy_version(versioned) == TAXONOMY_VERSION
    assert versioned_id != unversioned_id


def test_replaying_a_versioned_record_keeps_its_version():
    _, payload = _sealed(taxonomy_version=TAXONOMY_VERSION)
    replayed = Action.from_capsule({"action_id": "dispatch_payout/x", "asg_payload": payload})
    assert replayed.taxonomy_version == TAXONOMY_VERSION
    assert Action.from_capsule({"action_id": "dispatch_payout/x", "asg_payload": {}}).taxonomy_version is None


def test_pack_declaring_a_legacy_class_still_loads():
    pack = load_pack_dir(Path(__file__).parent / "fixtures" / "packs" / "causal_remediation_shaped")
    (semantic,) = pack.action_semantics
    assert semantic.action_class == "comms.external"
    assert classify(semantic.action_class) is TAXONOMY["communication.send"]
