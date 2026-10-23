# SPDX-License-Identifier: Apache-2.0
"""``scripts/generate_go_packs.py``: the Go module's embedded pack tree is what
the generator writes from this repo's catalog, and a missing current version is
written exactly as the hand-vendored copy before it was."""
from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GENERATOR = ROOT / "scripts" / "generate_go_packs.py"


def _generator():
    spec = importlib.util.spec_from_file_location("generate_go_packs", GENERATOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def gen_copy(tmp_path, monkeypatch):
    """The generator, pointed at a copy of go/."""
    gen = _generator()
    shutil.copytree(ROOT / "go" / "packs", tmp_path / "go" / "packs")
    (tmp_path / "go" / "pack").mkdir()
    shutil.copyfile(ROOT / "go" / "pack" / "registry_gen.go", tmp_path / "go" / "pack" / "registry_gen.go")
    monkeypatch.setattr(gen, "GO", tmp_path / "go")
    monkeypatch.setattr(gen, "PACKS", tmp_path / "go" / "packs")
    monkeypatch.setattr(gen, "EMBED_GO", tmp_path / "go" / "packs" / "embed.go")
    monkeypatch.setattr(gen, "REGISTRY_GO", tmp_path / "go" / "pack" / "registry_gen.go")
    (tmp_path / "go" / "guard" / "testdata").mkdir(parents=True)
    shutil.copyfile(gen.DEFINITIONS_JSON, tmp_path / "go" / "guard" / "testdata" / "engine-definitions.json")
    monkeypatch.setattr(gen, "DEFINITIONS_JSON", tmp_path / "go" / "guard" / "testdata" / "engine-definitions.json")
    return gen


def _files(directory: Path) -> dict[str, bytes]:
    return {p.relative_to(directory).as_posix(): p.read_bytes() for p in directory.rglob("*") if p.is_file()}


def test_the_tree_is_what_the_generator_writes():
    assert _generator().main(["--check"]) == 0


@pytest.mark.parametrize("name", ["everyday", "seller"])
def test_a_missing_current_version_is_written_as_it_was_vendored(gen_copy, tmp_path, name):
    version = gen_copy.load_pack_dir(gen_copy.CATALOG / name).pack_id.rsplit("/", 1)[1]
    target = gen_copy.PACKS / f"{name}-{version}"
    vendored = tmp_path / "vendored"
    shutil.copytree(target, vendored)
    shutil.rmtree(target)
    assert gen_copy.main(["--check"]) == 1
    assert gen_copy.main([]) == 0
    written, before = _files(target), _files(vendored)
    assert sorted(written) == sorted(before)
    assert {k: v for k, v in written.items() if k != "PROVENANCE.json"} == {
        k: v for k, v in before.items() if k != "PROVENANCE.json"
    }
    new, old = json.loads(written["PROVENANCE.json"]), json.loads(before["PROVENANCE.json"])
    for key in ("pack_id", "engine_definition_digest", "wicket_digests", "fold_digests"):
        assert new[key] == old[key], key
    assert new["catalog_only"] == {k: old.get("catalog_only", {}).get(k, {}) for k in ("wicket_digests", "fold_digests")}
    assert gen_copy.main(["--check"]) == 0


def test_an_edited_definition_is_found(gen_copy):
    held = next((gen_copy.PACKS / "seller-0.1.5" / "wickets").glob("*.yaml"))
    held.write_bytes(held.read_bytes() + b"\n# edited\n")
    assert gen_copy.main(["--check"]) == 1


def test_a_hand_edited_registry_is_found(gen_copy):
    gen_copy.REGISTRY_GO.write_text(gen_copy.REGISTRY_GO.read_text() + "\n// edited\n")
    assert gen_copy.main(["--check"]) == 1


def test_the_taxonomy_is_written_from_the_engines(gen_copy):
    vendored = gen_copy.PACKS / "engine" / "action_taxonomy.json"
    vendored.write_bytes(vendored.read_bytes() + b"\n")
    (gen_copy.PACKS / "engine" / "PROVENANCE.json").unlink()
    assert gen_copy.main(["--check"]) == 1
    assert gen_copy.main([]) == 0
    assert vendored.read_bytes() == gen_copy.TAXONOMY.read_bytes()
    provenance = json.loads((gen_copy.PACKS / "engine" / "PROVENANCE.json").read_text())
    assert provenance["taxonomy_version"] == gen_copy.TAXONOMY_VERSION
    assert provenance["jcs_digest"] == gen_copy.TAXONOMY_DIGEST
    assert gen_copy.main(["--check"]) == 0
