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
    assert sum("no case runs the fold" in m for m in missing) == len(_fold_digests(tool)), missing
