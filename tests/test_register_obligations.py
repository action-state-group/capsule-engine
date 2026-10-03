# SPDX-License-Identifier: Apache-2.0
"""EvidenceCompiler.compile_obligations: a register -> the deterministic
obligations pack. The vectors under ``tests/fixtures/register-vectors/`` pin
input rows -> expected JCS bytes; the rest of this file checks the EU AI Act
register's compiled/excluded split, its agreement with ``register.yaml``, and
that the pack note lists exactly what the compiler produces."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from capsule_engine.packs.contract_validate import validate_requirement
from capsule_engine.register import (
    EXCLUDED_CONTESTED_CLAUSE,
    EXCLUDED_JUDGMENT_REQUIRED,
    EXCLUDED_SYSTEM_OF_RECORD_READ_REQUIRED,
    EvidenceCompiler,
    load_register_file,
)

REPO = Path(__file__).parent.parent
VECTORS = Path(__file__).parent / "fixtures" / "register-vectors"
EU_DIR = REPO / "capsule_engine" / "packs" / "catalog" / "eu-ai-act"
EU_REGISTER = EU_DIR / "obligations-register.yaml"

VECTOR_CASES = sorted(p.name for p in VECTORS.iterdir() if p.is_dir())


def _compile(path: Path):
    return EvidenceCompiler().compile_obligations(load_register_file(path))


@pytest.mark.parametrize("case", VECTOR_CASES)
def test_vector_compiles_to_the_pinned_bytes(case):
    expected = (VECTORS / case / "expected.json").read_bytes()
    assert _compile(VECTORS / case / "register.yaml").canonical_bytes() == expected
    # Twice in one process gives the same bytes too (no hidden state).
    assert _compile(VECTORS / case / "register.yaml").canonical_bytes() == expected


@pytest.mark.parametrize("case", [c for c in VECTOR_CASES if (VECTORS / c / "register.mutant.yaml").exists()])
def test_vector_mutant_compiles_to_the_same_bytes(case):
    """Key order, quoting and line wrapping in the YAML do not reach the output."""
    expected = (VECTORS / case / "expected.json").read_bytes()
    assert _compile(VECTORS / case / "register.mutant.yaml").canonical_bytes() == expected


def test_vector_cases_present():
    assert {"eu-ai-act-obligations", "one-per-class", "all-excluded"} <= set(VECTOR_CASES)


def test_eu_vector_input_is_the_catalog_register():
    assert (VECTORS / "eu-ai-act-obligations" / "register.yaml").read_bytes() == EU_REGISTER.read_bytes()


def test_one_per_class_takes_every_path():
    pack = _compile(VECTORS / "one-per-class" / "register.yaml")
    assert [c.id for c in pack.compiled] == ["fact-1", "rule-1", "confirm-1", "doc-1"]
    assert [(e.id, e.reason) for e in pack.excluded] == [
        ("judged-1", EXCLUDED_JUDGMENT_REQUIRED),
        ("state-1", EXCLUDED_SYSTEM_OF_RECORD_READ_REQUIRED),
        ("contested-1", EXCLUDED_CONTESTED_CLAUSE),
    ]


def test_all_excluded_register_compiles_to_an_empty_pack():
    pack = _compile(VECTORS / "all-excluded" / "register.yaml")
    assert pack.compiled == ()
    assert [e.id for e in pack.excluded] == ["judged-only", "state-only"]


def test_eu_register_split():
    register = load_register_file(EU_REGISTER)
    pack = EvidenceCompiler().compile_obligations(register)
    assert len(register.rows) == 36
    assert len(pack.compiled) == 26
    assert len(pack.excluded) == 10
    # Every row lands in exactly one list, in register order.
    order = [r.id for r in register.rows]
    assert sorted([c.id for c in pack.compiled] + [e.id for e in pack.excluded], key=order.index) == order
    assert pack.register_digest == register.definition_digest()


def test_eu_compiled_rows_are_deterministic_only():
    pack = _compile(EU_REGISTER)
    for contract in pack.compiled:
        assert contract.forward_verdict == "DETERMINISTIC", contract.id
        assert contract.backward_verdict == "DETERMINISTIC", contract.id
        assert contract.mode != "judged", contract.id
        assert contract.profile == "obligation", contract.id
        assert not contract.clause.contested, contract.id
        if contract.evidence_rule.startswith("DOC. "):
            assert "never graded 'compliant'" in contract.evidence_rule, contract.id


def test_eu_excluded_rows_carry_their_reason():
    register = load_register_file(EU_REGISTER)
    for excluded in _compile(EU_REGISTER).excluded:
        row = register.row(excluded.id)
        if row.evidence_class == "JUDGED":
            assert excluded.reason == EXCLUDED_JUDGMENT_REQUIRED
        elif row.evidence_class == "STATE":
            assert excluded.reason == EXCLUDED_SYSTEM_OF_RECORD_READ_REQUIRED
        else:
            assert row.clause.contested
            assert excluded.reason == EXCLUDED_CONTESTED_CLAUSE


def test_eu_compiled_pack_validates_as_contracts():
    """Each compiled requirement passes the Evidence Contract schema."""
    for contract in _compile(EU_REGISTER).compiled:
        validate_requirement(contract.canonical_dict())


def test_eu_register_agrees_with_the_trace_register():
    """The rows both registers carry are identical, so the two never disagree
    about an obligation's statement, class or clause."""
    trace = load_register_file(EU_DIR / "register.yaml")
    full = load_register_file(EU_REGISTER)
    for row in trace.rows:
        assert full.row(row.id) is not None, row.id
        assert full.row(row.id).canonical_dict() == row.canonical_dict(), row.id


def _note_ids(section: str) -> list[str]:
    note = (EU_DIR / "obligations-pack-note.md").read_text(encoding="utf-8")
    body = note.split(f"## {section}", 1)[1].split("\n## ", 1)[0]
    return re.findall(r"^\| (EU-[0-9A-Za-z-]+) \|", body, flags=re.MULTILINE)


def test_pack_note_lists_exactly_the_compiled_and_excluded_rows():
    pack = _compile(EU_REGISTER)
    assert _note_ids("Compiled") == [c.id for c in pack.compiled]
    assert _note_ids("Excluded") == [e.id for e in pack.excluded]


def test_expected_bytes_are_canonical_json():
    """expected.json is JCS: parsing and re-serialising sorted and compact
    reproduces the file exactly (no pretty-printing, no trailing newline)."""
    for case in VECTOR_CASES:
        raw = (VECTORS / case / "expected.json").read_bytes()
        again = json.dumps(json.loads(raw), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        assert again.encode("utf-8") == raw, case
