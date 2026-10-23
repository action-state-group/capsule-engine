# SPDX-License-Identifier: Apache-2.0
"""Run every case in ``tests/fixtures/contract-cases/`` (the Evidence Contract
edge-case library) against ``contract_validate`` / ``contract_diff``, and keep
the library itself honest: its size floor, its negatives, its index, and that
it is exactly what ``scripts/generate_contract_cases.py`` produces.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import jsonschema
import pytest

from capsule_engine.packs.contract_diff import ContractDiffError, contract_pin, diff_contracts, main
from capsule_engine.packs.contract_validate import explain_evidence_contract, validate_evidence_contract

REPO_ROOT = Path(__file__).parent.parent
CASES_DIR = REPO_ROOT / "tests" / "fixtures" / "contract-cases"
GENERATOR = REPO_ROOT / "scripts" / "generate_contract_cases.py"

INDEX = json.loads((CASES_DIR / "index.json").read_text())


def _load(entry: dict) -> dict:
    return json.loads((CASES_DIR / entry["file"]).read_text())


def _run_diff(case: dict) -> dict:
    """The diff outcome in the case file's own ``expect`` vocabulary."""
    try:
        validate_evidence_contract(case["a"])
        validate_evidence_contract(case["b"])
        result = diff_contracts(case["a"], case["b"])
    except jsonschema.exceptions.ValidationError:
        return {"error": "invalid_contract"}
    except ContractDiffError:
        return {"error": "duplicate_requirement_id"}
    return {"breaking": result.breaking, "changes": [{"path": c.path, "kind": c.kind} for c in result.changes]}


@pytest.mark.parametrize("entry", INDEX, ids=lambda e: e["id"])
def test_case(entry):
    case = _load(entry)
    expect = case["expect"]
    if case["kind"] == "validate":
        issues = explain_evidence_contract(case["contract"])
        if expect["valid"]:
            assert issues == []
        else:
            found = [(i.path, i.keyword) for i in issues]
            assert (expect["error"]["path"], expect["error"]["keyword"]) in found, found
    elif case["kind"] == "diff":
        unvalidated = expect.get("unvalidated")
        assert _run_diff(case) == {k: v for k, v in expect.items() if k != "unvalidated"}
        if unvalidated is not None:
            raw = diff_contracts(case["a"], case["b"])
            assert {"breaking": raw.breaking,
                    "changes": [{"path": c.path, "kind": c.kind} for c in raw.changes]} == unvalidated
    else:
        assert contract_pin(case["contract"]) == expect


def test_library_floor_and_negatives():
    assert len(INDEX) >= 100
    negatives = [e for e in INDEX if e["negative"]]
    assert len(negatives) >= 50
    for kind in ("validate", "diff"):
        assert any(e["kind"] == kind and e["negative"] for e in INDEX), kind
        assert any(e["kind"] == kind and not e["negative"] for e in INDEX), kind


def test_every_case_has_a_rationale_and_a_unique_id():
    ids = [e["id"] for e in INDEX]
    assert len(ids) == len(set(ids))
    for entry in INDEX:
        assert entry["rationale"].strip(), entry["id"]
        assert _load(entry)["rationale"] == entry["rationale"]


def test_index_lists_every_case_file():
    on_disk = {p.name for p in CASES_DIR.glob("*.json")} - {"index.json"}
    assert on_disk == {e["file"] for e in INDEX}


def test_sha256sums_covers_every_file():
    """Copies of this library pin the digest of SHA256SUMS; it must list every
    other file in the directory with its current digest."""
    import hashlib

    listed = {}
    for line in (CASES_DIR / "SHA256SUMS").read_text().splitlines():
        digest, name = line.split("  ", 1)
        listed[name] = digest
    actual = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in CASES_DIR.iterdir() if p.name != "SHA256SUMS"}
    assert listed == actual


def test_diff_inputs_are_valid_unless_the_case_is_about_invalid_input():
    for entry in INDEX:
        case = _load(entry)
        if case["kind"] == "diff" and case["expect"].get("error") != "invalid_contract":
            validate_evidence_contract(case["a"])
            validate_evidence_contract(case["b"])


def test_key_order_does_not_change_the_pin():
    by_id = {e["id"]: _load(e) for e in INDEX}
    assert by_id["pin-base"]["expect"] == by_id["pin-base-key-order"]["expect"]


def test_committed_library_matches_the_generator(tmp_path):
    spec = importlib.util.spec_from_file_location("generate_contract_cases", GENERATOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.write(tmp_path)
    generated = {p.name: p.read_text() for p in tmp_path.iterdir()}
    committed = {p.name: p.read_text() for p in CASES_DIR.iterdir()}
    assert generated == committed, "tests/fixtures/contract-cases is stale: run scripts/generate_contract_cases.py"


# -- the CLI entry point -------------------------------------------------------


def _write_pair(tmp_path, case_id):
    case = _load(next(e for e in INDEX if e["id"] == case_id))
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    a.write_text(json.dumps(case["a"]))
    b.write_text(json.dumps(case["b"]))
    return str(a), str(b)


@pytest.mark.parametrize(
    ("case_id", "code"),
    [
        ("diff-identical", 0),
        ("diff-freshness-lengthened", 0),
        ("diff-freshness-shortened", 1),
        ("diff-bad-a-invalid", 2),
        ("diff-bad-duplicate-requirement-id", 2),
    ],
)
def test_cli_exit_codes(tmp_path, capsys, case_id, code):
    assert main(list(_write_pair(tmp_path, case_id))) == code


def test_cli_json_names_both_contracts_by_ref_and_digest(tmp_path, capsys):
    a, b = _write_pair(tmp_path, "diff-source-changed")
    assert main([a, b, "--json"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["breaking"] is True
    assert out["a"]["contract_ref"] == "ec:example-fixture:2026-09-30@1"
    assert out["b"]["contract_ref"] == "ec:example-fixture:2026-09-30@2"
    assert out["a"]["contract_digest"]["digest_alg"] == "SHA-256"
    assert out["changes"] == [
        {"path": "requirements[req-a]/evidence_requirements/required_sources", "kind": "changed",
         "severity": "breaking",
         "reason": "required sources: source-one replaced by source-two; evidence for the old members may not count"},
        {"path": "version", "kind": "version_changed", "severity": "non_breaking", "reason": "version 1 -> 2"},
    ]


def test_cli_missing_file_is_exit_2(tmp_path, capsys):
    assert main([str(tmp_path / "nope.json"), str(tmp_path / "nope.json")]) == 2
