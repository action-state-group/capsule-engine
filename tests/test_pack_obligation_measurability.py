# SPDX-License-Identifier: Apache-2.0
"""``Obligation.measurability`` / ``evidence_instrument``: optional, additive,
closed-set, the same values an outcome uses. A declared-not-measured
obligation cites no check and must name its instrument; a measured one cites
a check and names none."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from capsule_engine.packs.errors import (
    INVALID_EVIDENCE_INSTRUMENT,
    INVALID_MEASURABILITY,
    MISSING_EVIDENCE_INSTRUMENT,
    MISSING_REQUIRED_FIELD,
    PackDefinitionError,
)
from capsule_engine.packs.loader import load_pack_dir

CATALOG = Path(__file__).parent.parent / "capsule_engine" / "packs" / "catalog"
PAYMENTS_SAFETY_DIGEST = "81278051db5ca3755956b7adeaea727d333d12a0c14c51c7e030f15ea8b9666f"
INSTRUMENT = {"kind": "structured_field", "field": "task_authority_ref"}


def _copy_payments_safety(tmp_path: Path, *, drop: tuple[str, ...] = (), **obligation_fields) -> Path:
    pack_dir = tmp_path / "pack"
    shutil.copytree(CATALOG / "payments-safety", pack_dir)
    doc = yaml.safe_load((pack_dir / "pack.yaml").read_text())
    for key in drop:
        del doc["obligations"][0][key]
    doc["obligations"][0].update(obligation_fields)
    (pack_dir / "pack.yaml").write_text(yaml.safe_dump(doc, sort_keys=False))
    return pack_dir


def _refused(pack_dir: Path) -> str:
    with pytest.raises(PackDefinitionError) as exc:
        load_pack_dir(pack_dir)
    return exc.value.reason


def test_an_unchanged_pack_digests_as_before_and_emits_neither_field(tmp_path):
    pack = load_pack_dir(_copy_payments_safety(tmp_path))
    assert pack.definition_digest() == PAYMENTS_SAFETY_DIGEST
    assert {"measurability", "evidence_instrument"}.isdisjoint(pack.canonical_dict()["obligations"][0])


def test_an_explicit_measured_is_the_default_and_digests_as_before(tmp_path):
    pack = load_pack_dir(_copy_payments_safety(tmp_path, measurability="measured"))
    assert pack.definition_digest() == PAYMENTS_SAFETY_DIGEST


def test_a_declared_not_measured_obligation_cites_no_check_and_emits_its_instrument(tmp_path):
    pack = load_pack_dir(
        _copy_payments_safety(tmp_path, drop=("check",), measurability="declared_not_measured",
                              evidence_instrument=INSTRUMENT)
    )
    obligation = pack.obligations[0]
    assert (obligation.check, obligation.measurability) == (None, "declared_not_measured")
    entry = pack.canonical_dict()["obligations"][0]
    assert "check" not in entry
    assert entry["measurability"] == "declared_not_measured"
    assert entry["evidence_instrument"] == INSTRUMENT


def test_declared_not_measured_without_an_instrument_is_refused(tmp_path):
    pack_dir = _copy_payments_safety(tmp_path, drop=("check",), measurability="declared_not_measured")
    assert _refused(pack_dir) == MISSING_EVIDENCE_INSTRUMENT


def test_declared_not_measured_that_also_cites_a_check_is_refused(tmp_path):
    pack_dir = _copy_payments_safety(tmp_path, measurability="declared_not_measured", evidence_instrument=INSTRUMENT)
    assert _refused(pack_dir) == INVALID_MEASURABILITY


def test_a_measured_obligation_naming_an_instrument_is_refused(tmp_path):
    assert _refused(_copy_payments_safety(tmp_path, evidence_instrument=INSTRUMENT)) == INVALID_MEASURABILITY


def test_a_measured_obligation_without_a_check_is_refused(tmp_path):
    assert _refused(_copy_payments_safety(tmp_path, drop=("check",))) == MISSING_REQUIRED_FIELD


@pytest.mark.parametrize("value", ["not_measured", "MEASURED", "", 1])
def test_a_measurability_outside_the_closed_set_is_refused(tmp_path, value):
    assert _refused(_copy_payments_safety(tmp_path, measurability=value)) == INVALID_MEASURABILITY


def test_an_instrument_of_an_unknown_kind_is_refused_and_names_the_obligation(tmp_path):
    pack_dir = _copy_payments_safety(
        tmp_path, drop=("check",), measurability="declared_not_measured",
        evidence_instrument={"kind": "free_text", "field": "x"},
    )
    with pytest.raises(PackDefinitionError, match=r"obligations\[") as exc:
        load_pack_dir(pack_dir)
    assert exc.value.reason == INVALID_EVIDENCE_INSTRUMENT
