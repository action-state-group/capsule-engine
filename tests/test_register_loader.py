# SPDX-License-Identifier: Apache-2.0
"""Obligation register loader ([batch1-obligation-register-v0-sample-compiler]):
loads the shipped sample register from data, plus must-fail validation cases --
every failure must carry an actionable message, same discipline
``test_pack_loader.py`` already holds ``packs/loader.py`` to."""
from __future__ import annotations

import copy
from pathlib import Path

import pytest

from capsule_engine.packs.schema import ClauseSpec, EvidenceInstrument
from capsule_engine.register.errors import RegisterDefinitionError
from capsule_engine.register.loader import load_register_dict, load_register_file
from capsule_engine.register.schema import ObligationRegister, RegisterRow

SAMPLE_REGISTER_PATH = (
    Path(__file__).parent.parent / "capsule_engine" / "register" / "examples" / "obligation-register-v0-sample.yaml"
)

BASE_ROW = {
    "id": "row-1",
    "statement": "The agent does the thing.",
    "source": "Policy P-1",
    "scope": "every relevant action",
    "owner": "compliance-team",
    "version": "1.0.0",
    "evidence_class": "RULE",
    "clause": {"instrument": "Policy P-1", "article": "§4"},
}

BASE_REGISTER = {"register_id": "test_pub/test-register/0.1.0", "rows": [BASE_ROW]}


def _register(**overrides):
    doc = copy.deepcopy(BASE_REGISTER)
    doc.update(overrides)
    return doc


def _row(**overrides):
    row = copy.deepcopy(BASE_ROW)
    row.update(overrides)
    return row


# --- happy path --------------------------------------------------------------


def test_loads_the_shipped_sample_register_from_data():
    register = load_register_file(SAMPLE_REGISTER_PATH)
    assert isinstance(register, ObligationRegister)
    assert register.register_id == "asg/obligation-register-sample/0.1.0"
    assert [r.id for r in register.rows] == ["sample/four-eyes/1", "EU-12-1"]


def test_row_lookup_by_id():
    register = load_register_file(SAMPLE_REGISTER_PATH)
    row = register.row("EU-12-1")
    assert row is not None
    assert row.evidence_class == "FACT"
    assert row.clause.article == "Article 12"
    assert register.row("does-not-exist") is None


def test_minimal_row_round_trips():
    register = load_register_dict(_register())
    assert len(register.rows) == 1
    row = register.rows[0]
    assert isinstance(row, RegisterRow)
    assert row.id == "row-1"
    assert isinstance(row.clause, ClauseSpec)
    assert row.clause.instrument == "Policy P-1"
    assert row.effective_from is None
    assert row.evidence_instrument is None


def test_row_with_evidence_instrument_parses():
    row_raw = _row(
        id="row-2",
        evidence_class="FACT",
        effective_from="2027-12-02",
        evidence_instrument={"kind": "structured_field", "field": "native_log_event_kind"},
    )
    register = load_register_dict(_register(rows=[row_raw]))
    row = register.rows[0]
    assert row.effective_from == "2027-12-02"
    assert isinstance(row.evidence_instrument, EvidenceInstrument)
    assert row.evidence_instrument.field == "native_log_event_kind"


def test_row_order_preserved_not_sorted():
    register = load_register_dict(
        _register(rows=[_row(id="z-row"), _row(id="a-row")])
    )
    assert [r.id for r in register.rows] == ["z-row", "a-row"]


def test_canonical_dict_and_digest_are_deterministic():
    register = load_register_dict(_register())
    d1 = register.definition_digest()
    d2 = load_register_dict(_register()).definition_digest()
    assert d1 == d2
    assert isinstance(d1, str) and len(d1) == 64


# --- must-fail validation -----------------------------------------------------


def test_register_file_not_found():
    with pytest.raises(RegisterDefinitionError) as exc:
        load_register_file("/no/such/register.yaml")
    assert exc.value.reason == "malformed_register"


def test_register_must_be_a_mapping():
    with pytest.raises(RegisterDefinitionError) as exc:
        load_register_dict(["not", "a", "mapping"])
    assert exc.value.reason == "malformed_register"


def test_missing_register_id():
    doc = _register()
    del doc["register_id"]
    with pytest.raises(RegisterDefinitionError) as exc:
        load_register_dict(doc)
    assert exc.value.reason == "missing_required_field"


def test_rows_required_and_non_empty():
    with pytest.raises(RegisterDefinitionError) as exc:
        load_register_dict(_register(rows=[]))
    assert exc.value.reason == "missing_required_field"


def test_duplicate_row_id_rejected():
    with pytest.raises(RegisterDefinitionError) as exc:
        load_register_dict(_register(rows=[_row(id="dup"), _row(id="dup")]))
    assert exc.value.reason == "duplicate_row_id"


@pytest.mark.parametrize("field_name", ["statement", "source", "scope", "owner", "version"])
def test_missing_required_row_field(field_name):
    row_raw = _row()
    del row_raw[field_name]
    with pytest.raises(RegisterDefinitionError) as exc:
        load_register_dict(_register(rows=[row_raw]))
    assert exc.value.reason == "missing_required_field"


def test_invalid_evidence_class_rejected():
    with pytest.raises(RegisterDefinitionError) as exc:
        load_register_dict(_register(rows=[_row(evidence_class="NOT-A-CLASS")]))
    assert exc.value.reason == "invalid_evidence_class"


def test_missing_clause_rejected():
    row_raw = _row()
    del row_raw["clause"]
    with pytest.raises(RegisterDefinitionError) as exc:
        load_register_dict(_register(rows=[row_raw]))
    assert exc.value.reason == "missing_required_field"


def test_malformed_clause_rejected():
    with pytest.raises(RegisterDefinitionError) as exc:
        load_register_dict(_register(rows=[_row(clause={"article": "§4"})]))  # no instrument
    assert exc.value.reason == "missing_required_field"


def test_invalid_effective_date_rejected():
    with pytest.raises(RegisterDefinitionError) as exc:
        load_register_dict(_register(rows=[_row(effective_from="not-a-date")]))
    assert exc.value.reason == "invalid_effective_date"


def test_invalid_evidence_instrument_kind_rejected():
    row_raw = _row(evidence_instrument={"kind": "not-a-kind"})
    with pytest.raises(RegisterDefinitionError) as exc:
        load_register_dict(_register(rows=[row_raw]))
    assert exc.value.reason == "invalid_evidence_instrument"
