# SPDX-License-Identifier: Apache-2.0
"""Registry-pin verification (``packs/pins.py``): fail closed on a missing
or mismatched pin, standing in for a live capsule-registry fetch until that
registry repo exists (registry-architecture-and-namespace-2026-08-10.md §6)."""
from __future__ import annotations

from pathlib import Path

import pytest

from capsule_ledger.packs.errors import RegistryPinError
from capsule_ledger.packs.loader import load_pack_dir
from capsule_ledger.packs.pins import load_pins_file, verify_pins

PAYMENTS_SAFETY_DIR = Path(__file__).parent.parent / "capsule_ledger" / "packs" / "catalog" / "payments-safety"


def _real_pack():
    return load_pack_dir(PAYMENTS_SAFETY_DIR)


def _real_pins() -> dict[str, str]:
    pack = _real_pack()
    pins = {pack.pack_id: pack.definition_digest()}
    for fold in pack.folds:
        pins[fold.fold_id] = fold.definition_digest()
    return pins


def test_verify_pins_passes_with_correct_pins():
    verify_pins(_real_pack(), _real_pins())  # must not raise


def test_verify_pins_fails_closed_on_mismatched_pack_digest():
    pins = _real_pins()
    pack = _real_pack()
    pins[pack.pack_id] = "f" * 64
    with pytest.raises(RegistryPinError) as exc_info:
        verify_pins(pack, pins)
    assert exc_info.value.reason == "pin_digest_mismatch"
    assert pack.pack_id in str(exc_info.value)


def test_verify_pins_fails_closed_on_mismatched_fold_digest():
    pins = _real_pins()
    pack = _real_pack()
    pins[pack.folds[0].fold_id] = "f" * 64
    with pytest.raises(RegistryPinError) as exc_info:
        verify_pins(pack, pins)
    assert exc_info.value.reason == "pin_digest_mismatch"


def test_verify_pins_fails_closed_on_missing_pack_pin():
    pack = _real_pack()
    pins = {f.fold_id: f.definition_digest() for f in pack.folds}  # pack itself omitted
    with pytest.raises(RegistryPinError) as exc_info:
        verify_pins(pack, pins)
    assert exc_info.value.reason == "pin_not_found"
    assert pack.pack_id in str(exc_info.value)


def test_verify_pins_fails_closed_on_missing_fold_pin():
    pack = _real_pack()
    pins = {pack.pack_id: pack.definition_digest()}  # folds omitted
    with pytest.raises(RegistryPinError) as exc_info:
        verify_pins(pack, pins)
    assert exc_info.value.reason == "pin_not_found"


def test_verify_pins_never_partially_trusts_an_empty_pins_mapping():
    with pytest.raises(RegistryPinError) as exc_info:
        verify_pins(_real_pack(), {})
    assert exc_info.value.reason == "pin_not_found"


def test_load_pins_file_round_trips(tmp_path):
    real = _real_pins()
    path = tmp_path / "pins.yaml"
    path.write_text("\n".join(f'"{k}": "{v}"' for k, v in real.items()))
    loaded = load_pins_file(path)
    assert loaded == real


@pytest.mark.parametrize(
    "content,expected_reason",
    [
        ("not: a\nmapping: [1, 2]\nextra: - broken: yaml: [", "malformed_pins_file"),
        ("- just\n- a\n- list\n", "malformed_pins_file"),
        ('"": "%s"' % ("a" * 64), "malformed_pins_file"),  # empty key
        ('"pack/1.0.0": "too-short"', "malformed_pins_file"),
        ('"pack/1.0.0": 12345', "malformed_pins_file"),  # not a string at all
    ],
)
def test_load_pins_file_must_fail_cases(tmp_path, content, expected_reason):
    path = tmp_path / "pins.yaml"
    path.write_text(content)
    with pytest.raises(RegistryPinError) as exc_info:
        load_pins_file(path)
    assert exc_info.value.reason == expected_reason
