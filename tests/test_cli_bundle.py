# SPDX-License-Identifier: Apache-2.0
"""`capsule bundle` golden-output tests: the produced slice verifies standalone,
transitively includes cited chain parents, and the permalink fragment
decodes back to the same bundle -- never sent anywhere but the URL fragment."""
from __future__ import annotations

import base64
import json
from pathlib import Path

from capsule_ledger.cli.main import main

FIXTURE_LEDGER = Path(__file__).parent / "fixtures" / "sample_ledger.jsonl"
APPROVE_ID = "705955419ca6f944a75db77ae2a59844fdd99d355866c6c1dbc4ebe655c024c7"
CONFIRM_ID = "94c877c7ff0240cf7dafe2067f7016e5412d59b05f9eefa4baf90fc792f16142"


def _decode_fragment(url: str) -> dict:
    fragment = url.split("#", 1)[1]
    padded = fragment + "=" * (-len(fragment) % 4)
    return json.loads(base64.urlsafe_b64decode(padded))


def test_bundle_all_records_verify_and_permalink_decodes(tmp_path, capsys):
    out_path = tmp_path / "bundle.json"
    rc = main(["bundle", "--ledger", str(FIXTURE_LEDGER), "--out", str(out_path)])
    assert rc == 0
    stdout = capsys.readouterr().out
    assert "all verify" in stdout
    assert "verify: https://verify.agentactioncapsule.org/bundle#" in stdout

    bundle = json.loads(out_path.read_text())
    assert len(bundle["records"]) == 4
    assert all(v["ok"] for v in bundle["verification"].values())

    permalink_line = next(line for line in stdout.splitlines() if line.startswith("verify: "))
    decoded = _decode_fragment(permalink_line[len("verify: "):])
    assert decoded["bundle_version"] == bundle["bundle_version"]
    assert len(decoded["records"]) == len(bundle["records"])


def test_bundle_transitively_includes_cited_chain_parent(tmp_path, capsys):
    out_path = tmp_path / "bundle.json"
    rc = main(
        ["bundle", "--ledger", str(FIXTURE_LEDGER), "--verdict", "confirmed", "--out", str(out_path)]
    )
    assert rc == 0
    bundle = json.loads(out_path.read_text())

    ids = {r["capsule_id"] for r in bundle["records"]}
    # Only 1 record matches --verdict confirmed, but it cites APPROVE_ID as
    # its chain parent, so a self-contained bundle must pull that in too.
    assert CONFIRM_ID in ids
    assert APPROVE_ID in ids
    assert len(bundle["records"]) == 2
    assert bundle["range"] == [1, 4]
    assert all(v["ok"] for v in bundle["verification"].values())


def test_bundle_flags_a_tampered_record_in_the_slice(tmp_path, capsys):
    capsules = [json.loads(line) for line in FIXTURE_LEDGER.read_text().splitlines() if line.strip()]
    capsules[0] = dict(capsules[0])
    capsules[0]["operator"] = "tampered-corp"
    tampered_ledger = tmp_path / "tampered.jsonl"
    with open(tampered_ledger, "w") as fh:
        for c in capsules:
            fh.write(json.dumps(c) + "\n")

    out_path = tmp_path / "bundle.json"
    rc = main(["bundle", "--ledger", str(tampered_ledger), "--out", str(out_path)])
    assert rc == 1
    stdout = capsys.readouterr().out
    assert "VERIFICATION FAILURE in this slice" in stdout

    bundle = json.loads(out_path.read_text())
    assert bundle["verification"][APPROVE_ID]["ok"] is False
    codes = {f["code"] for f in bundle["verification"][APPROVE_ID]["findings"]}
    assert "capsule_id_mismatch" in codes
