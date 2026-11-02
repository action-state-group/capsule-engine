# SPDX-License-Identifier: Apache-2.0
"""One pack format (#252): live packs are authored as ``pack-source.yaml``
(``scope: {kind: live}``), and ``pack.yaml`` is a deprecated read path.

The compiled form (evidencebook-skills' pack_compile.py) is checked against
this engine by ``scripts/check_pack_sources.py`` in CI; these tests cover the
loader: the source-only fields stay out of the digest, a wrong one is
refused, and no live pack is added as ``pack.yaml``.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

from capsule_engine.packs import PackDefinitionError, is_pack_dir, load_pack_dir, pack_file
from capsule_engine.packs.errors import (
    AMBIGUOUS_PACK_SOURCE,
    CONSTRAINT_CHECK_MISMATCH,
    INVALID_PACK_SOURCE_SCOPE,
    INVALID_TIER,
    MALFORMED_PACK,
    PACK_NOT_FOUND,
    PACK_VERSION_MISMATCH,
)

ROOT = Path(__file__).resolve().parents[2]
CATALOG = ROOT / "python" / "capsule_engine" / "packs" / "catalog"
SELLER = CATALOG / "seller"

def test_no_live_pack_is_added_as_pack_yaml():
    assert not list(CATALOG.glob("*/pack.yaml"))
    assert {p.parent.name for p in CATALOG.glob("*/pack-source.yaml")} == {p.name for p in CATALOG.iterdir() if p.is_dir()}
    for root in (ROOT / "go" / "packs", ROOT / "python" / "tests" / "fixtures" / "packs"):
        for d in root.iterdir():
            if re.match(r"^(everyday|seller|inference)-\d", d.name):
                assert pack_file(d).name == "pack-source.yaml", d


def _copy(tmp_path: Path) -> Path:
    dst = tmp_path / "seller"
    shutil.copytree(SELLER, dst)
    return dst


def _edit(pack_dir: Path, old: str, new: str) -> None:
    path = pack_dir / "pack-source.yaml"
    text = path.read_text()
    assert old in text, old
    path.write_text(text.replace(old, new, 1))


def _as_pack_yaml(pack_dir: Path) -> None:
    """Turn the source back into the pack.yaml it was moved from."""
    lines, out, prev = (pack_dir / "pack-source.yaml").read_text().splitlines(keepends=True), [], ""
    for line in lines:
        cited_check = line.strip().startswith("check:") and prev.strip().startswith("- wicket_ref:")
        prev = line
        if line.startswith("pack_version:") or line in ("scope:\n", "  kind: live\n") or cited_check:
            continue
        out.append(line)
    (pack_dir / "pack-source.yaml").unlink()
    (pack_dir / "pack.yaml").write_text("".join(out))


def test_the_source_fields_are_not_in_the_digest(tmp_path):
    source = load_pack_dir(SELLER)
    legacy_dir = _copy(tmp_path)
    _as_pack_yaml(legacy_dir)
    assert pack_file(legacy_dir).name == "pack.yaml" and is_pack_dir(legacy_dir)
    assert load_pack_dir(legacy_dir).definition_digest() == source.definition_digest()
    assert source.definition_digest() == "5e112a7f5136e2193adb177605dfad10b7b0753a2de93ed72e446695b7acc56a"


@pytest.mark.parametrize(
    ("old", "new", "reason"),
    [
        ("  kind: live", "  kind: after_the_fact", INVALID_PACK_SOURCE_SCOPE),
        ("scope:\n  kind: live\n", "", INVALID_PACK_SOURCE_SCOPE),
        ("pack_version: 0.1.6", "pack_version: 0.1.7", PACK_VERSION_MISMATCH),
        ("pack_version: 0.1.6\n", "", PACK_VERSION_MISMATCH),
        (
            "wicket_ref: dedupe/1.0.0\n    check: dedupe",
            "wicket_ref: dedupe/1.0.0\n    check: price_floor",
            CONSTRAINT_CHECK_MISMATCH,
        ),
        ("wicket_ref: dedupe/1.0.0\n    check: dedupe\n", "wicket_ref: dedupe/1.0.0\n", CONSTRAINT_CHECK_MISMATCH),
        ("    default_disposition: NEVER\n", "    default_disposition: NEVER\n    requirement_tier: optional\n", INVALID_TIER),
    ],
)
def test_a_wrong_source_field_is_refused(tmp_path, old, new, reason):
    pack_dir = _copy(tmp_path)
    _edit(pack_dir, old, new)
    with pytest.raises(PackDefinitionError) as exc:
        load_pack_dir(pack_dir)
    assert exc.value.reason == reason


def test_a_known_requirement_tier_loads_at_the_same_digest(tmp_path):
    pack_dir = _copy(tmp_path)
    _edit(pack_dir, "    default_disposition: NEVER\n", "    default_disposition: NEVER\n    requirement_tier: informational\n")
    assert load_pack_dir(pack_dir).definition_digest() == load_pack_dir(SELLER).definition_digest()


def test_a_pack_yaml_with_source_fields_is_refused(tmp_path):
    pack_dir = _copy(tmp_path)
    (pack_dir / "pack-source.yaml").rename(pack_dir / "pack.yaml")
    with pytest.raises(PackDefinitionError) as exc:
        load_pack_dir(pack_dir)
    assert exc.value.reason == MALFORMED_PACK


def test_a_directory_with_both_files_is_refused(tmp_path):
    pack_dir = _copy(tmp_path)
    shutil.copyfile(pack_dir / "pack-source.yaml", pack_dir / "pack.yaml")
    with pytest.raises(PackDefinitionError) as exc:
        load_pack_dir(pack_dir)
    assert exc.value.reason == AMBIGUOUS_PACK_SOURCE


def test_a_directory_with_neither_file_is_not_a_pack(tmp_path):
    assert not is_pack_dir(tmp_path)
    with pytest.raises(PackDefinitionError) as exc:
        load_pack_dir(tmp_path)
    assert exc.value.reason == PACK_NOT_FOUND
