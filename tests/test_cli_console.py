# SPDX-License-Identifier: Apache-2.0
"""`capsule console`: CLI wiring + a real HTTP round trip against the real
`LedgerAPI` over a fixture ledger (`tests/fixtures/sample_ledger.jsonl`)."""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from capsule_ledger.cli.main import _build_parser
from capsule_ledger.console.server import build_server

FIXTURE_LEDGER = Path(__file__).parent / "fixtures" / "sample_ledger.jsonl"
APPROVE_ID = "705955419ca6f944a75db77ae2a59844fdd99d355866c6c1dbc4ebe655c024c7"
CONFIRM_ID = "94c877c7ff0240cf7dafe2067f7016e5412d59b05f9eefa4baf90fc792f16142"


def test_console_registered_as_a_full_arm_verb():
    parser = _build_parser(arm="full")
    args = parser.parse_args(["console", "--ledger", str(FIXTURE_LEDGER)])
    assert args.command == "console"
    assert args.host == "127.0.0.1"  # local only, by default


def test_console_not_registered_in_guards_only_arm():
    parser = _build_parser(arm="guards-only")
    with pytest.raises(SystemExit):
        parser.parse_args(["console", "--ledger", str(FIXTURE_LEDGER)])


def _start_server(ledger_path: str):
    """Build and serve on the *same* thread throughout -- matching real CLI
    usage (`console_cmd.run` calls `build_server` then `serve_forever` on
    one thread) and avoiding a cross-thread sqlite3 connection, which
    `LedgerStore` does not support."""
    box: dict = {}
    ready = threading.Event()

    def _serve():
        server = build_server(ledger_path, host="127.0.0.1", port=0)
        box["server"] = server
        ready.set()
        try:
            server.serve_forever()
        finally:
            # `server_close()` (which closes the LedgerStore's sqlite3
            # connection) must run on the same thread that built the
            # server -- sqlite3 connections aren't shareable across
            # threads. Matches real CLI usage: `console_cmd.run` calls
            # `build_server`/`serve_forever`/`server_close` all on one
            # thread, so this is test-fixture plumbing only, not a
            # production behavior difference.
            server.server_close()

    thread = threading.Thread(target=_serve, daemon=True)
    thread.start()
    assert ready.wait(timeout=5), "console server did not start"
    return box["server"], thread


def _stop_server(server, thread) -> None:
    server.shutdown()
    thread.join(timeout=5)


@pytest.fixture
def running_server():
    server, thread = _start_server(str(FIXTURE_LEDGER))
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        _stop_server(server, thread)


def _get(url: str):
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def _get_json(url: str):
    status, body = _get(url)
    return status, json.loads(body)


def test_console_serves_static_shell_and_component_library(running_server):
    status, body = _get(running_server + "/")
    assert status == 200
    assert b"capsule console" in body
    assert b"http://" not in body
    assert b"https://" not in body

    for path in ("/tokens.css", "/components.css", "/console.css", "/console.js"):
        status, body = _get(running_server + path)
        assert status == 200
        assert len(body) > 0


def test_console_checkpoint_endpoint_reflects_the_real_ledger(running_server):
    status, data = _get_json(running_server + "/api/checkpoint")
    assert status == 200
    assert data["checkpoint"] == 4
    assert "verifies offline" in data["line"]
    assert data["line"].startswith(f"checkpoint #{data['checkpoint']}")


def test_console_records_endpoint_lists_real_ledger_data(running_server):
    status, data = _get_json(running_server + "/api/records")
    assert status == 200
    assert data["total"] == 4
    assert data["shown"] == 4
    ids = {r["capsule_id"] for r in data["records"]}
    assert APPROVE_ID in ids
    assert CONFIRM_ID in ids
    assert data["cli_echo"] == "≡ capsule log"


def test_console_records_endpoint_applies_filters_and_echoes_them(running_server):
    status, data = _get_json(running_server + "/api/records?verdict=blocked")
    assert status == 200
    assert data["shown"] == 1
    assert data["cli_echo"] == "≡ capsule log --verdict blocked"


def test_console_record_detail_shows_identity_verdict_checks_and_folds(running_server):
    status, detail = _get_json(running_server + "/api/records/" + APPROVE_ID)
    assert status == 200
    assert detail["capsule_id"] == APPROVE_ID
    assert detail["sealed"]["capsule_id"] == APPROVE_ID
    assert detail["disposition"]["verdict_class"] == "executed"
    assert detail["verify"]["ok"] is True
    assert detail["fold_strip"], "expected at least one live fold value"
    assert detail["cli_echo"] == f"≡ capsule show {APPROVE_ID}"

    # CONFIRM_ID cites APPROVE_ID as its chain parent (relation "confirms")
    # -- the reverse edge ("cited by") must show up on APPROVE_ID's own view.
    cited_by_ids = {c["capsule_id"] for c in detail["chain"]["cited_by"]}
    assert CONFIRM_ID in cited_by_ids


def test_console_record_detail_shows_what_it_cites(running_server):
    status, detail = _get_json(running_server + "/api/records/" + CONFIRM_ID)
    assert status == 200
    assert detail["chain"]["cites"]["capsule_id"] == APPROVE_ID
    assert detail["chain"]["cites"]["relation"] == "confirms"
    assert detail["chain"]["cites"]["found"] is True


def test_console_record_detail_404_for_unknown_capsule(running_server):
    status, data = _get_json(running_server + "/api/records/does-not-exist")
    assert status == 404
    assert "error" in data


def test_console_tampered_record_fails_real_verification(running_server, tmp_path):
    capsules = [json.loads(line) for line in FIXTURE_LEDGER.read_text().splitlines() if line.strip()]
    capsules[0] = dict(capsules[0])
    capsules[0]["operator"] = "tampered-corp"
    tampered_ledger = tmp_path / "tampered.jsonl"
    with open(tampered_ledger, "w") as fh:
        for c in capsules:
            fh.write(json.dumps(c) + "\n")

    server, thread = _start_server(str(tampered_ledger))
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        status, detail = _get_json(base + "/api/records/" + APPROVE_ID)
        assert status == 200
        assert detail["verify"]["ok"] is False
        codes = {f["code"] for f in detail["verify"]["findings"]}
        assert "capsule_id_mismatch" in codes
    finally:
        _stop_server(server, thread)
