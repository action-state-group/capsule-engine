# SPDX-License-Identifier: Apache-2.0
"""`capsule bundle` golden-output tests: the produced slice verifies standalone,
transitively includes cited chain parents, and the permalink fragment
decodes back to the same bundle -- never sent anywhere but the URL fragment."""
from __future__ import annotations

import base64
import json
from pathlib import Path

from capsule_engine.cli.format import cli_echo_leaks_absolute_path
from capsule_engine.cli.main import main

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


# [ldg-demo-artifact-path-leak] `cli_echo` is rendered on the public bundle
# permalink and the offline viewer -- verbatim, on a page a stranger opens.
# It leaked the operator's home directory (`/Users/intangible/...`) via a raw
# `--out` echo. This is the actual string that leaked (`_work/capsule-ledger/
# mvp-exit-demo/bundle.json`, 2026-08-12), pinned so the guard's positive
# case is the real historical bug, not just a synthetic one.
LEAKED_CLI_ECHO = (
    "≡ capsule bundle --out /Users/intangible/dev/asg/_work/capsule-ledger/"
    "mvp-exit-demo/bundle.json"
)


def test_guard_flags_the_historical_leaked_cli_echo():
    assert cli_echo_leaks_absolute_path(LEAKED_CLI_ECHO) is True


def test_guard_passes_a_relative_cli_echo():
    assert cli_echo_leaks_absolute_path("≡ capsule bundle --out bundle.json") is False


def test_bundle_cli_echo_never_leaks_absolute_out_path(tmp_path, monkeypatch, capsys):
    # tmp_path is always absolute -- this is the exact shape that produced
    # the historical leak (an absolute --out), so this is the regression
    # guard for the CLI's own behavior, not just the pure function above.
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    out_path = workdir / "bundle.json"

    rc = main(["bundle", "--ledger", str(FIXTURE_LEDGER), "--out", str(out_path)])
    assert rc == 0

    bundle = json.loads(out_path.read_text())
    assert cli_echo_leaks_absolute_path(bundle["cli_echo"]) is False

    stdout = capsys.readouterr().out
    printed_echo = stdout.rstrip("\n").splitlines()[-1]
    assert printed_echo == bundle["cli_echo"]
    assert cli_echo_leaks_absolute_path(printed_echo) is False
