# SPDX-License-Identifier: Apache-2.0
"""A pack can cite a built-in wicket or fold by id and digest
(``wicket_ref``/``fold_ref`` + ``digest``) instead of copying its body."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from capsule_engine.folds.catalog import Catalog as FoldCatalog
from capsule_engine.guards.wickets import Catalog as WicketCatalog
from capsule_engine.packs.errors import CATALOG_REF_DIGEST_MISMATCH, UNKNOWN_CATALOG_REF, PackDefinitionError
from capsule_engine.packs.loader import CORE_FOLD_CATALOG_DIR, CORE_WICKET_CATALOG_DIR, load_pack_dir

CAPS = WicketCatalog(CORE_WICKET_CATALOG_DIR).get("caps/1.0.0")
SPEND = FoldCatalog(CORE_FOLD_CATALOG_DIR).get("spend.weekly/1.0.0")


# Reads raw capsule or fixture JSON/YAML: the test's decoding boundary.
def _pack(tmp_path: Path, *, constraint: dict, fold: dict) -> Path:
    pack_dir = tmp_path / "pack"
    (pack_dir / "folds").mkdir(parents=True)
    doc = {
        "pack_id": "test/refs/0.1.0",
        "obligations": [{"id": "cap", "statement": "Checks the weekly cap.", "check": "caps"}],
        "action_semantics": [
            {"action_type": "payment.make", "action_class": "money.transfer", "required_fields": ["amount_minor"]}
        ],
        "constraints": [constraint],
        "folds": [fold],
    }
    (pack_dir / "pack.yaml").write_text(yaml.safe_dump(doc, sort_keys=False))
    return pack_dir


def _by_ref():
    return {"wicket_ref": "caps/1.0.0", "digest": CAPS.digest, "scope": ["developer"]}


def _fold_by_ref():
    return {"fold_ref": "spend.weekly/1.0.0", "digest": SPEND.digest}


def test_refs_resolve_to_the_built_in_definitions(tmp_path):
    pack = load_pack_dir(_pack(tmp_path, constraint=_by_ref(), fold=_fold_by_ref()))
    assert pack.constraints == (CAPS.definition,)
    assert pack.folds == (SPEND.definition,)
    assert pack.canonical_dict()["constraints"][0]["digest"] == CAPS.digest
    assert pack.canonical_dict()["folds"][0]["digest"] == SPEND.digest


def test_a_ref_digests_exactly_like_an_inline_copy(tmp_path):
    by_ref = load_pack_dir(_pack(tmp_path / "a", constraint=_by_ref(), fold=_fold_by_ref()))
    copy_dir = tmp_path / "b"
    inline = dict(CAPS.definition.canonical_dict(), scope=["developer"])
    pack_dir = _pack(copy_dir, constraint=inline, fold={"file": "folds/spend.yaml"})
    (pack_dir / "folds" / "spend.yaml").write_text(yaml.safe_dump(SPEND.definition.canonical_dict()))
    assert by_ref.definition_digest() == load_pack_dir(pack_dir).definition_digest()


@pytest.mark.parametrize("key", ["wicket", "fold"])
def test_a_ref_at_the_wrong_digest_is_refused(tmp_path, key):
    constraint, fold = _by_ref(), _fold_by_ref()
    (constraint if key == "wicket" else fold)["digest"] = "0" * 64
    with pytest.raises(PackDefinitionError) as exc:
        load_pack_dir(_pack(tmp_path, constraint=constraint, fold=fold))
    assert exc.value.reason == CATALOG_REF_DIGEST_MISMATCH


def test_an_unknown_ref_is_refused(tmp_path):
    constraint = dict(_by_ref(), wicket_ref="caps/9.9.9")
    with pytest.raises(PackDefinitionError) as exc:
        load_pack_dir(_pack(tmp_path, constraint=constraint, fold=_fold_by_ref()))
    assert exc.value.reason == UNKNOWN_CATALOG_REF
