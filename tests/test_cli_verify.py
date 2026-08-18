# SPDX-License-Identifier: Apache-2.0
"""`capsule verify` golden-output tests, including the exit-code discipline and
the negative path (a genuinely tampered record must be *caught*, not just
structurally exercised -- the recurring "check that never rejects anything"
defect class)."""
from __future__ import annotations

import json
from pathlib import Path

from capsule_ledger.cli.main import main

FIXTURE_LEDGER = Path(__file__).parent / "fixtures" / "sample_ledger.jsonl"
APPROVE_ID = "705955419ca6f944a75db77ae2a59844fdd99d355866c6c1dbc4ebe655c024c7"


def test_verify_ok_exits_zero(capsys):
    rc = main(["verify", APPROVE_ID, "--ledger", str(FIXTURE_LEDGER)])
    assert rc == 0
    out = capsys.readouterr().out
    assert f"✓ verifies · {APPROVE_ID}" in out
    assert out.rstrip().endswith(f"≡ capsule verify {APPROVE_ID}")


def test_verify_json_flag(capsys):
    rc = main(["verify", APPROVE_ID, "--ledger", str(FIXTURE_LEDGER), "--json"])
    assert rc == 0
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is True
    assert result["capsule_id"] == APPROVE_ID


def test_verify_not_found_is_exit_code_2(capsys):
    rc = main(["verify", "deadbeef", "--ledger", str(FIXTURE_LEDGER)])
    assert rc == 2
    err = capsys.readouterr().err
    assert "no such capsule" in err


def test_verify_requires_capsule_id_or_bundle(capsys):
    rc = main(["verify"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "capsule_id is required unless --bundle" in err


def test_verify_catches_a_tampered_record_exit_code_1(tmp_path, capsys):
    """The mutant-verify test: a record altered after sealing must FAIL
    verification and exit 1, not silently pass."""
    capsules = [json.loads(line) for line in FIXTURE_LEDGER.read_text().splitlines() if line.strip()]
    capsules[0] = dict(capsules[0])
    capsules[0]["operator"] = "tampered-corp"  # capsule_id no longer matches recomputed digest

    tampered_ledger = tmp_path / "tampered.jsonl"
    with open(tampered_ledger, "w") as fh:
        for c in capsules:
            fh.write(json.dumps(c) + "\n")

    rc = main(["verify", APPROVE_ID, "--ledger", str(tampered_ledger)])
    assert rc == 1
    out = capsys.readouterr().out
    assert f"✗ verification failed · {APPROVE_ID}" in out
    assert "capsule_id_mismatch" in out


def test_verify_bundle_offline_round_trips_clean(tmp_path, capsys):
    bundle_path = tmp_path / "bundle.json"
    rc = main(["bundle", "--ledger", str(FIXTURE_LEDGER), "--out", str(bundle_path)])
    assert rc == 0
    capsys.readouterr()

    rc = main(["verify", "--bundle", str(bundle_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "verifies clean" in out
    assert "4 record(s)" in out


def test_verify_bundle_catches_tampering_after_the_fact(tmp_path, capsys):
    bundle_path = tmp_path / "bundle.json"
    rc = main(["bundle", "--ledger", str(FIXTURE_LEDGER), "--out", str(bundle_path)])
    assert rc == 0
    capsys.readouterr()

    bundle = json.loads(bundle_path.read_text())
    bundle["records"][0]["operator"] = "tampered-corp"
    bundle_path.write_text(json.dumps(bundle))

    rc = main(["verify", "--bundle", str(bundle_path)])
    assert rc == 1
    out = capsys.readouterr().out
    assert "verification FAILED" in out
    assert "✗ verification failed" in out


def test_verify_bundle_missing_file_is_exit_code_2(capsys):
    rc = main(["verify", "--bundle", "/no/such/file.json"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "cannot read bundle" in err
