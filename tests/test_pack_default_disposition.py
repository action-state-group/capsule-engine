# SPDX-License-Identifier: Apache-2.0
"""``Obligation.default_disposition``: optional, additive, closed-set.

The digest pins below were measured on the five catalog packs BEFORE this
field existed (capsule-engine 671a8a36cd7291ac28a391a1463763e4836846ce, a
clean ``git archive`` export). None of those packs declares the field, so
every one must still digest to exactly the same value.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from capsule_engine.packs.errors import INVALID_DEFAULT_DISPOSITION, PackDefinitionError
from capsule_engine.packs.loader import load_pack_dir

CATALOG = Path(__file__).parent.parent / "capsule_engine" / "packs" / "catalog"

DIGESTS_BEFORE_DEFAULT_DISPOSITION = {
    "airline-engagement": "ea74a099524b5f7a82047cc883e211a25ef230a29c34438e68bfac22c9169277",
    "eu-ai-act": "9d3197935b6b002ccd4682c7cfa2bacf01d9dbc2b3b04811e94dd50e6a2b9cfa",
    "eu-ai-act-deterministic": "b6f6b62454380c6ab4fc1c11fd4418045cc310b95f6a7cbbf2ea23bf54ca5609",
    "payments-safety": "81278051db5ca3755956b7adeaea727d333d12a0c14c51c7e030f15ea8b9666f",
    "standard-vendor": "9ae420d59ebfb1f87ecd740c948be4d0df16cefac97d8640da24934302359ae2",
}


@pytest.mark.parametrize("pack_name", sorted(DIGESTS_BEFORE_DEFAULT_DISPOSITION))
def test_existing_catalog_pack_digests_are_unchanged(pack_name):
    pack = load_pack_dir(CATALOG / pack_name)
    assert all(o.default_disposition is None for o in pack.obligations)
    assert pack.definition_digest() == DIGESTS_BEFORE_DEFAULT_DISPOSITION[pack_name]


def _copy_payments_safety(tmp_path: Path, **obligation_fields) -> Path:
    pack_dir = tmp_path / "pack"
    shutil.copytree(CATALOG / "payments-safety", pack_dir)
    doc = yaml.safe_load((pack_dir / "pack.yaml").read_text())
    doc["obligations"][0].update(obligation_fields)
    (pack_dir / "pack.yaml").write_text(yaml.safe_dump(doc, sort_keys=False))
    return pack_dir


@pytest.mark.parametrize("value", ["GO", "ASK", "NEVER"])
def test_declared_default_disposition_is_parsed_and_digested(tmp_path, value):
    pack = load_pack_dir(_copy_payments_safety(tmp_path, default_disposition=value))
    assert pack.obligations[0].default_disposition == value
    assert pack.canonical_dict()["obligations"][0]["default_disposition"] == value
    assert pack.definition_digest() != DIGESTS_BEFORE_DEFAULT_DISPOSITION["payments-safety"]


def test_undeclared_default_disposition_is_absent_from_the_canonical_form(tmp_path):
    pack = load_pack_dir(_copy_payments_safety(tmp_path))
    assert "default_disposition" not in pack.canonical_dict()["obligations"][0]


@pytest.mark.parametrize("value", ["ask", "ESCALATE", "", 1])
def test_default_disposition_outside_the_closed_set_is_refused(tmp_path, value):
    with pytest.raises(PackDefinitionError) as exc:
        load_pack_dir(_copy_payments_safety(tmp_path, default_disposition=value))
    assert exc.value.reason == INVALID_DEFAULT_DISPOSITION
