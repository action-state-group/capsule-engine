# SPDX-License-Identifier: Apache-2.0
"""``scripts/conformance_vectors.py``: the checker finds an edited input or
expectation, and the coverage gate finds a cited definition no case runs.
That the vectors are what this engine decides today is the ``vectors`` job of
``.github/workflows/conformance.yml``."""
from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "scripts" / "conformance_vectors.py"


def _tool():
    spec = importlib.util.spec_from_file_location("conformance_vectors", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def tool_copy(tmp_path, monkeypatch):
    """The tool, pointed at a copy of the vectors holding one small set."""
    tool = _tool()
    vectors = tmp_path / "v0"
    vectors.mkdir()
    for name in ("KNOWN-DIFFERENCES.json", "COVERAGE-EXEMPT.json", "everyday-actions.json"):
        shutil.copyfile(tool.VECTORS / name, vectors / name)
    monkeypatch.setattr(tool, "VECTORS", vectors)
    monkeypatch.setattr(tool, "coverage", lambda sets, strings: [])
    return tool


def test_an_edited_expectation_is_found(tool_copy):
    path = tool_copy.VECTORS / "everyday-actions.json"
    data = json.loads(path.read_text())
    data["cases"][0]["expected"]["steps"][0]["outcome"] = "allow" if data["cases"][0]["expected"]["steps"][0]["outcome"] != "allow" else "deny"
    path.write_text(json.dumps(data))
    assert tool_copy.main(["--check"]) == 1
    assert tool_copy.main(["--write"]) == 0
    assert tool_copy.main(["--check"]) == 0


def test_an_input_that_is_not_the_one_pinned_is_found(tool_copy):
    path = tool_copy.VECTORS / "everyday-actions.json"
    data = json.loads(path.read_text())
    data["cases"][0]["input_sha256"] = "0" * 64
    path.write_text(json.dumps(data))
    assert tool_copy.main(["--check"]) == 1


def _fold_digests(tool) -> set[str]:
    out = set()
    for directory in tool._current().values():
        out |= set(json.loads((tool.PACKS / directory / "PROVENANCE.json").read_text())["fold_digests"].values())
    return out


def test_the_coverage_gate_finds_a_definition_no_case_runs():
    tool = _tool()
    sets = [json.loads(p.read_text()) for p in sorted(tool.VECTORS.glob("*.json")) if not p.name[0].isupper()]
    folds = _fold_digests(tool)
    assert tool.coverage(sets, folds) == []
    kept = [{**s, "cases": [c for c in s["cases"] if "upfront" not in c["case"]]} for s in sets]
    missing = tool.coverage(kept, folds)
    assert any("upfront_amount" in m for m in missing), missing


def test_a_fold_counts_only_when_a_case_ran_it():
    tool = _tool()
    sets = [json.loads(p.read_text()) for p in sorted(tool.VECTORS.glob("*.json")) if not p.name[0].isupper()]
    missing = tool.coverage(sets, set())
    # One finding per current pack and fold it cites that no entry exempts:
    # two packs citing one fold are two findings, and inference's fold is
    # exempt for that pack alone (COVERAGE-EXEMPT.json).
    exempt = json.loads((tool.VECTORS / "COVERAGE-EXEMPT.json").read_text())["exempt"]
    cited = sum(
        1
        for name, d in tool._current().items()
        for fid in json.loads((tool.PACKS / d / "PROVENANCE.json").read_text())["fold_digests"]
        if not any(e["definition"] == fid and e.get("pack", name) == name for e in exempt)
    )
    assert sum("no case runs the fold" in m for m in missing) == cited, missing


def _one_case(tool, set_name: str, case_name: str) -> Path:
    """A copy of one case of ``set_name`` as the only set beside the tool's
    copied ones."""
    data = json.loads((_tool().VECTORS / set_name).read_text())
    data["cases"] = [c for c in data["cases"] if c["case"] == case_name]
    path = tool.VECTORS / set_name
    path.write_text(json.dumps(data, indent=1) + "\n")
    return path


def test_a_frozen_case_keeps_its_pack(tool_copy):
    path = _one_case(tool_copy, "undecided-act-everyday-0.3.5.json", "held")
    assert json.loads(path.read_text())["cases"][0]["frozen"] is True
    assert tool_copy.main(["--check"]) == 0
    assert tool_copy.main(["--write"]) == 0
    (case,) = json.loads(path.read_text())["cases"]
    assert case["pack"] == "everyday-0.3.5"


def test_an_unflagged_case_on_an_older_pack_is_moved_to_the_current_one(tool_copy, capsys):
    path = _one_case(tool_copy, "undecided-act-everyday-0.3.5.json", "held")
    data = json.loads(path.read_text())
    del data["cases"][0]["frozen"]
    path.write_text(json.dumps(data))
    assert tool_copy.main(["--check"]) == 1
    assert "not the current" in capsys.readouterr().err
    assert tool_copy.main(["--write"]) == 0
    (case,) = json.loads(path.read_text())["cases"]
    assert case["pack"] == tool_copy._current()["everyday"]


def test_a_case_frozen_on_a_pack_that_is_not_embedded_is_found(tool_copy, capsys):
    path = _one_case(tool_copy, "undecided-act-everyday-0.3.5.json", "held")
    data = json.loads(path.read_text())
    data["cases"][0]["pack"] = "everyday-0.0.1"
    path.write_text(json.dumps(data))
    assert tool_copy.main(["--check"]) == 1
    assert "not an embedded pack" in capsys.readouterr().err


def test_seeding_with_a_pack_freezes_every_case_on_it(tool_copy):
    out = tool_copy.seed(ROOT / "conformance-vectors" / "guard" / "v0" / "inputs" / "caps-held", "everyday-0.3.5")
    assert out.name == "caps-held-everyday-0.3.5.json"
    cases = json.loads(out.read_text())["cases"]
    assert cases and all(c["pack"] == "everyday-0.3.5" and c["frozen"] is True for c in cases)
    with pytest.raises(ValueError):
        tool_copy.seed(ROOT / "conformance-vectors" / "guard" / "v0" / "inputs" / "caps-held", "everyday-0.0.1")
