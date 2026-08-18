# SPDX-License-Identifier: Apache-2.0
"""Wicket definitions: parse validation and a pinned-digest worked example
per wicket (the manifest task's acceptance gate: "at least one worked
example and a pinned digest test")."""
from __future__ import annotations

from pathlib import Path

import pytest

from capsule_ledger.guards.wickets import (
    Catalog,
    WicketDefinitionError,
    load_definition_file,
    load_definition_text,
)
from capsule_ledger.guards.wickets.definition import WicketDefinition

WICKET_CATALOG_DIR = Path(__file__).parent.parent / "capsule_ledger" / "guards" / "wickets" / "catalog_defs"

# Pinned digests -- independently recomputable from the checked-in YAML.
# A change to any of these files' content is a real policy-config change and
# MUST move its digest; that's the entire point of this test. `caps_holds`
# and `hold_reconcile` (capsule-emit #51/#53, `holds/engine.py`) were added
# alongside the original three reference wickets.
EXPECTED_DIGESTS = {
    "dedupe/1.0.0": "18ab5d489f1e5774d576b8f99897edd4f4b20f609b85683456a3e3b6b4912abb",
    "caps/1.0.0": "906a75a0b908d38fa7b05823ba11f229c3d593516119ad757b541cee7083f54b",
    "verify_before_dispatch/1.0.0": "a721624813f785de49f3dcef2090662e7045bc393e59db72defcdbf47269453c",
    "caps_holds/1.0.0": "86a07c5c2739502b1211dbb1c73df0d6950f91ba454ff151ff32cb6946fc21f6",
    "hold_reconcile/1.0.0": "cf6f76b1aeb1d705f90f89c97667c2db035e697211f7f7dc0f8455c54acaec74",
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
