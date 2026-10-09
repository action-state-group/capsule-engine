# SPDX-License-Identifier: Apache-2.0
"""The seller pack, asg/seller/0.1.0: what it cites, at which digest, and
what each rule declares.

The scenarios are in ``test_pack_seller_acceptance.py``; this file pins the
pack's own content, so a definition cited at another digest or a rule
re-declared with another disposition goes red here even where a decision
would not change (an integrity check refuses whatever its rule declares).
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from capsule_engine.guards.classes import TAXONOMY_VERSION
from capsule_engine.guards.wickets import Catalog
from capsule_engine.packs.errors import PackDefinitionError
from capsule_engine.packs.loader import load_pack_dir

ROOT = Path(__file__).parent.parent / "capsule_engine"
PACK_DIR = ROOT / "packs" / "catalog" / "seller"
CATALOG = Catalog(ROOT / "guards" / "wickets" / "catalog_defs")

PACK = load_pack_dir(PACK_DIR)

# Every definition the pack cites, by id.
CITED = [
    "caps/1.0.0",
    "dedupe/1.0.0",
    "verify_before_dispatch/1.0.0",
    "action_class_gate/1.0.0",
    "seller.recipient_role/1.0.0",
    "price_floor/2.0.1",
    "promise_requires_approval/1.0.0",
    "promise_never/1.0.0",
    "required_disclosure/1.0.0",
    "offer_expiry/1.0.1",
    "seller.single_commitment/1.0.0",
    "seller.destination_rail/1.0.0",
    "task_authority/1.1.0",
]

# check -> the disposition its one rule declares.
DECLARED = {
    "recipient_role": "DO",
    "action_class_gate": "ASK",
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
}


def test_the_pack_id_and_taxonomy_pin():
    assert PACK.pack_id == "asg/seller/0.1.0"
    assert PACK.taxonomy.taxonomy_version == TAXONOMY_VERSION == "6"


def test_it_cites_each_definition_at_its_catalog_digest():
    assert [w.wicket_id for w in PACK.constraints] == CITED
    for wicket in PACK.constraints:
        assert wicket.definition_digest() == CATALOG.get(wicket.wicket_id).digest, wicket.wicket_id


def test_each_check_has_one_rule_with_its_declared_disposition():
    measured = [o for o in PACK.obligations if o.check is not None]
    assert len(measured) == len(PACK.obligations) == len(DECLARED)
    assert {o.check: o.default_disposition for o in measured} == DECLARED


def test_the_gate_rule_names_the_personal_disclosure_selector():
    (rule,) = [o for o in PACK.obligations if o.check == "action_class_gate"]
    assert rule.selector == "personal_disclosure"


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
