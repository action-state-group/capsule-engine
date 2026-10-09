# SPDX-License-Identifier: Apache-2.0
"""Wicket definitions: parse validation and a pinned-digest worked example
per wicket (the manifest task's acceptance gate: "at least one worked
example and a pinned digest test")."""
from __future__ import annotations

from pathlib import Path

import pytest

from capsule_engine.guards.wickets import (
    Catalog,
    WicketDefinitionError,
    load_definition_file,
    load_definition_text,
)
from capsule_engine.guards.wickets.definition import WicketDefinition

WICKET_CATALOG_DIR = Path(__file__).parent.parent / "capsule_engine" / "guards" / "wickets" / "catalog_defs"

# Pinned digests -- independently recomputable from the checked-in YAML.
# A change to any of these files' content is a real policy-config change and
# MUST move its digest; that's the entire point of this test. `caps_holds`
# and `hold_reconcile` (capsule-emit #51/#53, `holds/engine.py`) were added
# alongside the original three reference wickets.
EXPECTED_DIGESTS = {
    "dedupe/1.0.0": "18ab5d489f1e5774d576b8f99897edd4f4b20f609b85683456a3e3b6b4912abb",
    "caps/1.0.0": "906a75a0b908d38fa7b05823ba11f229c3d593516119ad757b541cee7083f54b",
    "caps/2.0.0": "b7ea63ec3d9fdb872d3b5db952e774f04ff8f4b945ca803e49181b91b2a25f80",
    "caps/3.0.0": "54870cd7059d18c5a88221185cb0a49fe1ea09c30825548e1e3134569c5cb66f",
    "caps/4.0.0": "2b07340e8fc858af76d8accf6afc3d6c9d05bb92d50abcfddd1fee8d9b51de35",
    # caps/4.0.0 with the per-action limit reading the authorised maximum; the everyday pack cites it.
    "caps/5.0.0": "2807e174dc7c817917621f90a53f3fa54992b76fe3ec28e8567f814b9e72a741",
    "verify_before_dispatch/1.0.0": "a721624813f785de49f3dcef2090662e7045bc393e59db72defcdbf47269453c",
    "caps_holds/1.0.0": "86a07c5c2739502b1211dbb1c73df0d6950f91ba454ff151ff32cb6946fc21f6",
    "hold_reconcile/1.0.0": "cf6f76b1aeb1d705f90f89c97667c2db035e697211f7f7dc0f8455c54acaec74",
    # The configured checks the everyday pack cites (guards/checks/
    # __init__.py CONFIGURED_CHECKS).
    "destination_rail/1.0.0": "4d76251eba138b37e6be3e83390072315ebcea42cd8d790f7e9765ecf1f63429",
    "counterparty_identity_change/1.0.0": "0cfb1cb380b355abb88ea385334f3bb31f2e145269a2768022f85005665fc6fe",
    "credential_pattern/1.0.0": "3617fdcaa39c03cf70a3254490328ccee5eaac0faba7cd52d9e3dc3fc50336c0",
    "recurring_charge/1.0.0": "ea4ae204e5dc33f3a28e5e15471f404f20d4f8fca193d355a099a4a7e08b082e",
    # Configured check no pack cites yet.
    "counterparty_seen_before/1.0.0": "e3a89876d6547fd1af6dde151e0802388fb68f7b2f9660c39b95b44e397cb263",
    "counterparty_seen_before/2.0.0": "c82a29eae8ef72736851d275ffa4d85e511a21d202822366d3b03bf3834c8cd0",
    # Taxonomy-only selectors, no fold (guards/checks/action_class_gate.py).
    "action_class_gate/1.0.0": "fcc352838832e13f5e87662f0194ec4a7065bc989bbe624b43ac5d9d1b638ae2",
    # An empty deny list; the user's list rides in a policy profile. No pack cites it yet.
    "counterparty_list/1.0.0": "d95162931a6c1f88c763f2fd221d63b238888c1bdd7c6d594123ee5671e18f65",
    # The everyday pack 0.3.1 checks that each read one number, one member of
    # a closed set, or one opaque reference.
    "recipient_role/1.0.0": "917ab5edea21867f318b8dfd54a6d7b458cbabcc5cebfac6ff0c4861e918d43b",
    "refundability/1.0.0": "410ad28fa2dd518502cbcbc00972d31ae4597722f17836ed2bf80e717f2f3294",
    "material_fields_changed/1.0.0": "b7c98d710e48c8fc87d92abf2599b04b6ac9ac6f129eee6335edb742f1dbc02a",
    "offer_fields_changed/1.0.0": "b1ca52ccdc821cbb57df844a10eb9e7d6242fff612fb5324c3c4f642d57f7ab1",
    "recipient_seen_before/1.0.0": "df8803e9c4bdab9deff48e772fcd21ff813d00157068dd250a8a5c6b45cd6c44",
    "channel_change/1.0.0": "fb580fc03afe1e641286c3ba62650d8e7150be91c6de0c6dac6a225a2eb6c793",
    "upfront_amount/1.0.0": "30996a832fa0cd94dc9a64f11e69d903f73a6e6415bff4005a665460a0798a6f",
    "task_authority/1.0.0": "bdb53ad1d4f3f18dc3a8ed5752c4429725df1df692f1e3b3687517f40074c150",
    # The seller-side checks, and the seller configurations of two existing
    # ones. No pack cites them yet.
    "price_floor/1.0.0": "a7eb755a71cf9dabaf04fbd740fc9ceaad5f0ba883795adad6fd9038796dffa9",
    "required_disclosure/1.0.0": "c8f216415be1fac673d1efc6df2e7a9666b5d5c8e639fbb3d8379a24bb8f5ebf",
    "promise_requires_approval/1.0.0": "b29e29e0a31ef764b282fa5754992b49b5ffc4f3fc261fb821987ae6c2e1c9b2",
    "promise_never/1.0.0": "836ae58f704a9c8ab8027fee344ca47ddb3de76a7a1973a650f42a3595e649f4",
    "offer_expiry/1.0.0": "7b1072fc6997b07e7f08941a723e60d53fd3a54dbccfda6fa7391124ec2702ee",
    "seller.recipient_role/1.0.0": "001d4d4198921ffec5f492f88286765a075eece1418d15b68576d4fceec7826b",
    "seller.destination_rail/1.0.0": "fab02cc2b39b2f45a5ac2a260eff7544814a2fb82de5524d91b00fd0b8d19800",
}


@pytest.mark.parametrize("wicket_id,expected_digest", EXPECTED_DIGESTS.items())
def test_builtin_wicket_digest_is_pinned(wicket_id, expected_digest):
    entry = Catalog(WICKET_CATALOG_DIR).get(wicket_id)
    assert entry is not None, f"{wicket_id} missing from the built-in wicket catalog"
    assert entry.digest == expected_digest


def test_catalog_lists_all_reference_wickets():
    ids = {e.definition.wicket_id for e in Catalog(WICKET_CATALOG_DIR).list_entries()}
    assert ids == set(EXPECTED_DIGESTS)
    assert Catalog(WICKET_CATALOG_DIR).list_errors() == []


def test_catalog_lookup_by_digest_matches_lookup_by_id():
    catalog = Catalog(WICKET_CATALOG_DIR)
    by_id = catalog.get("caps/1.0.0")
    by_digest = catalog.get(EXPECTED_DIGESTS["caps/1.0.0"])
    assert by_id.definition == by_digest.definition


def test_digest_changes_when_config_changes():
    """The mutant: a real config edit must move the digest -- a digest that
    can't detect a config change isn't a config digest."""
    base = load_definition_text("wicket_id: caps/1.0.0\ncheck: caps\nconfig:\n  caps_minor:\n    money.transfer: 100\n")
    mutant = load_definition_text("wicket_id: caps/1.0.0\ncheck: caps\nconfig:\n  caps_minor:\n    money.transfer: 200\n")
    assert base.definition_digest() != mutant.definition_digest()


def test_digest_is_stable_across_key_order():
    """JCS canonicalization: dict key order in the YAML source must not
    affect the digest."""
    a = load_definition_text("wicket_id: dedupe/1.0.0\ncheck: dedupe\nconfig:\n  method: x\n  window_days: 7\n")
    b = load_definition_text("wicket_id: dedupe/1.0.0\ncheck: dedupe\nconfig:\n  window_days: 7\n  method: x\n")
    assert a.definition_digest() == b.definition_digest()


def test_config_defaults_to_empty_mapping_when_omitted():
    d = load_definition_text("wicket_id: verify_before_dispatch/1.0.0\ncheck: verify_before_dispatch\n")
    assert d.config == {}


@pytest.mark.parametrize(
    "text,expected_reason",
    [
        ("wicket_id: not-a-valid-id\ncheck: caps\n", "invalid_wicket_id_namespace"),
        ("wicket_id: caps/1.0.0\ncheck: not_a_real_check\n", "unknown_check"),
        ("wicket_id: caps/1.0.0\ncheck: caps\nconfig: not-a-mapping\n", "malformed_definition"),
        ("not-a-mapping-at-all", "malformed_definition"),
    ],
)
def test_must_fail_cases(text, expected_reason):
    with pytest.raises(WicketDefinitionError) as exc_info:
        load_definition_text(text)
    assert exc_info.value.reason == expected_reason


def test_empty_document_is_malformed():
    with pytest.raises(WicketDefinitionError) as exc_info:
        load_definition_text("")
    assert exc_info.value.reason == "malformed_definition"


def test_load_definition_file_matches_load_definition_text(tmp_path):
    text = "wicket_id: dedupe/1.0.0\ncheck: dedupe\nconfig:\n  window_days: 30\n"
    path = tmp_path / "dedupe.yaml"
    path.write_text(text)
    assert load_definition_file(path) == load_definition_text(text)


def test_canonical_dict_shape():
    d = WicketDefinition(wicket_id="caps/1.0.0", check="caps", config={"a": 1})
    assert d.canonical_dict() == {"wicket_id": "caps/1.0.0", "check": "caps", "config": {"a": 1}}
