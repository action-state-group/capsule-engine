# SPDX-License-Identifier: Apache-2.0
"""The seller pack, asg/seller/0.1.1: what it cites, at which digest, and
what each rule declares. 0.1.0 is kept byte for byte under
``tests/fixtures/packs/seller-0.1.0`` and still loads at its digest.

The scenarios are in ``test_pack_seller_acceptance.py``; this file pins the
pack's own content, so a definition cited at another digest or a rule
re-declared with another disposition goes red here even where a decision
would not change (an integrity check refuses whatever its rule declares).
"""
from __future__ import annotations

import hashlib
import importlib.util
import shutil
from pathlib import Path

import pytest
import yaml
from capsule_ledger.ledger import LedgerStore

from capsule_engine.guards import Action, LocalSigner
from capsule_engine.guards.capsule import ALLOW, DENY
from capsule_engine.guards.checks import AuthorizationRecord, authorization_record_digest
from capsule_engine.guards.classes import TAXONOMY_VERSION
from capsule_engine.guards.wickets import Catalog
from capsule_engine.packs import build_engine, install_pack
from capsule_engine.packs.errors import PackDefinitionError
from capsule_engine.packs.loader import load_pack_dir

ROOT = Path(__file__).parent.parent / "capsule_engine"
PACK_DIR = ROOT / "packs" / "catalog" / "seller"
FROZEN_0_1_0_DIR = Path(__file__).parent / "fixtures" / "packs" / "seller-0.1.0"
PACK_0_1_0_DIGEST = "7b854f0f0feb51381d936ad992bef659234d77a799a2a319585a856f65a19c77"
PACK_0_1_0_FILE_SHA256 = "b03b7ebb491cdd250bedfba47fcaaa0e60f0316a88a2698db7b886ef5f4753e6"
CATALOG = Catalog(ROOT / "guards" / "wickets" / "catalog_defs")

PACK = load_pack_dir(PACK_DIR)

# Every definition the pack cites, by id.
CITED = [
    "caps/1.0.0",
    "dedupe/1.0.0",
    "verify_before_dispatch/1.0.0",
    "seller.recipient_role/1.0.0",
    "price_floor/2.0.1",
    "promise_requires_approval/1.0.0",
    "promise_never/1.0.0",
    "required_disclosure/1.0.0",
    "offer_expiry/1.0.1",
    "seller.single_commitment/1.0.0",
    "seller.destination_rail/1.0.0",
    "task_authority/1.1.0",
    "seller.release_on_acceptance/1.0.0",
]

# check -> the disposition its one rule declares.
DECLARED = {
    "recipient_role": "DO",
    "price_floor": "ASK",
    "promise_requires_approval": "ASK",
    "required_disclosure": "ASK",
    "offer_expiry": "ASK",
    "destination_rail": "ASK",
    "task_authority": "ASK",
    "dedupe": "ASK",
    "promise_never": "NEVER",
    "single_commitment": "NEVER",
    "verify_before_dispatch": "NEVER",
    "release_on_acceptance": "NEVER",
}


def test_the_pack_id_and_taxonomy_pin():
    assert PACK.pack_id == "asg/seller/0.1.1"
    assert PACK.taxonomy.taxonomy_version == TAXONOMY_VERSION == "6"


def test_it_cites_each_definition_at_its_catalog_digest():
    assert [w.wicket_id for w in PACK.constraints] == CITED
    for wicket in PACK.constraints:
        assert wicket.definition_digest() == CATALOG.get(wicket.wicket_id).digest, wicket.wicket_id


def test_each_check_has_one_rule_with_its_declared_disposition():
    measured = [o for o in PACK.obligations if o.check is not None]
    assert len(measured) == len(PACK.obligations) == len(DECLARED)
    assert {o.check: o.default_disposition for o in measured} == DECLARED


def test_the_address_is_measured_by_the_release_rule_not_the_gate():
    """The gate reads only the class, so it would ask on the address after
    acceptance too; 0.1.1 cites neither the gate nor a rule on it."""
    assert not [o for o in PACK.obligations if o.check == "action_class_gate" or o.selector is not None]
    assert "action_class_gate" not in {w.check for w in PACK.constraints}
    (release,) = [w for w in PACK.constraints if w.check == "release_on_acceptance"]
    assert release.config["release_classes"] == CATALOG.get("promise_never/1.0.0").definition.config["representation_classes"]
    (share,) = [s for s in PACK.action_semantics if s.action_class == "disclosure.personal"]
    assert "representation_class" in share.required_fields
    assert "item_ref" in share.optional_fields


def test_0_1_0_is_kept_byte_for_byte_and_still_loads_at_its_digest():
    frozen = FROZEN_0_1_0_DIR / "pack.yaml"
    assert hashlib.sha256(frozen.read_bytes()).hexdigest() == PACK_0_1_0_FILE_SHA256
    pack = load_pack_dir(FROZEN_0_1_0_DIR)
    assert pack.pack_id == "asg/seller/0.1.0"
    assert pack.definition_digest() == PACK_0_1_0_DIGEST


def test_caps_is_cited_for_the_engine_and_measures_no_rule():
    assert "caps" not in DECLARED
    (caps,) = [w for w in PACK.constraints if w.check == "caps"]
    assert set(caps.config["caps_minor"]) == {"money.transfer"}
    assert not {s.action_class for s in PACK.action_semantics} & set(caps.config["caps_minor"])


def _copy(tmp_path: Path) -> Path:
    target = tmp_path / "seller"
    shutil.copytree(PACK_DIR, target)
    return target


@pytest.mark.parametrize("wicket_id", ["seller.recipient_role/1.0.0", "seller.single_commitment/1.0.0",
                                       "price_floor/2.0.1"])
def test_a_definition_cited_at_another_digest_is_refused_at_load(tmp_path, wicket_id):
    pack_dir = _copy(tmp_path)
    data = yaml.safe_load((pack_dir / "pack.yaml").read_text())
    (entry,) = [c for c in data["constraints"] if c["wicket_ref"] == wicket_id]
    entry["digest"] = CATALOG.get("recipient_role/1.0.0").digest  # a real digest, of another definition
    (pack_dir / "pack.yaml").write_text(yaml.safe_dump(data, sort_keys=False))
    with pytest.raises(PackDefinitionError):
        load_pack_dir(pack_dir)


# -- 0.1.1 is no looser than 0.1.0 on personal data -------------------------------


def _acceptance_module():
    """The seller fixture's module, for its task authority, opening and parties."""
    spec = importlib.util.spec_from_file_location("seller_acceptance",
                                                  Path(__file__).parent / "test_pack_seller_acceptance.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _engines(tmp_path: Path):
    """One observe-mode engine per pack version, each on its own ledger."""
    signer = LocalSigner(key_id="seller-strictness-key", secret=b"seller-strictness-fixed-key")
    for version, pack_dir in (("0.1.0", FROZEN_0_1_0_DIR), ("0.1.1", PACK_DIR)):
        store = LedgerStore(tmp_path / version / "ledger")
        installed = install_pack(load_pack_dir(pack_dir), project_dir=tmp_path / version / "project", mode="observe")
        yield version, store, build_engine(installed, ledger=store, signer_provider=lambda: signer)


def test_no_personal_disclosure_0_1_0_asked_on_is_allowed_by_0_1_1_without_an_acceptance(tmp_path):
    """Every class a disclosure may declare, none, and one outside the set;
    to the buyer, the user and a third party; with and without a one-shot
    approval of the class. No acceptance is sealed. Where 0.1.0 did not
    allow, 0.1.1 must not either -- and 0.1.1 refuses every one."""
    acceptance = _acceptance_module()
    classes = [*CATALOG.get("promise_never/1.0.0").definition.config["representation_classes"], None, "phone"]
    cases = [(cls, role, approved) for cls in classes for role in ("buyer", "self", "third_party")
             for approved in (False, True)]
    outcomes: dict[str, list[str]] = {}
    for version, store, engine in _engines(tmp_path):
        try:
            for n, (cls, role, approved) in enumerate(cases):
                approval: AuthorizationRecord | None = (
                    {"type": "approval/v0", "body": {"representation_class": cls}} if approved else None)
                action = Action(
                    verb="share_personal", operator="seller-strictness", developer="seller-assistant@v1",
                    action_class="disclosure.personal", target=acceptance.BUYER_A, recipient_role=role,
                    representation_class=cls, task_authority_ref=acceptance.TASK_REF, item_ref=acceptance.ITEM,
                    authorized_by=authorization_record_digest(approval) if approval else None,
                    equivalence_key=f"share/{n}", action_id=f"share/seller-strictness-{n}",
                    timestamp="2026-10-09T10:00:00Z")
                decision = engine.check(action, dry_run=True, task_authority_record=acceptance.TASK,
                                        authorization_record=approval,
                                        commercial_bounds_opening=acceptance.OPENING)
                outcomes.setdefault(version, []).append(decision.outcome)
        finally:
            store.close()
    assert len(outcomes["0.1.0"]) == len(outcomes["0.1.1"]) == len(cases) == 96
    for case, old, new in zip(cases, outcomes["0.1.0"], outcomes["0.1.1"], strict=True):
        assert old != ALLOW, case  # 0.1.0 asked on (or refused) every personal disclosure
        assert new == DENY, (case, old, new)
