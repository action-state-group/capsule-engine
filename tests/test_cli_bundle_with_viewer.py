# SPDX-License-Identifier: Apache-2.0
"""`capsule bundle --with-viewer`: a self-contained offline recipient viewer
written alongside the ordinary bundle.json, per the bundle-with-viewer design.

Acceptance, checked directly rather than assumed:
* the bundle's own JSON/fragment wire format is byte-identical whether or
  not --with-viewer is passed (the viewer rides alongside, never inside);
* the produced HTML has zero external resource references (no <script
  src>, no <link>, no @import) -- nothing to fetch, on either end;
* the produced HTML genuinely opens and verifies with every network
  primitive (fetch, XMLHttpRequest, WebSocket) hard-blocked -- proven via a
  Node harness that runs the file's own literal inline <script> content in
  file order, exactly as a browser parsing the file would, not a
  reimplementation of its logic; and that this is a real check, not a
  pass-through, is itself proven by two mutants: a tampered record the
  ritual must reject, and a forced network call the block must reject.
"""
from __future__ import annotations

import base64
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

import capsule_ledger.cli.bundle_cmd as bundle_cmd
from capsule_ledger.cli.main import main

FIXTURE_LEDGER = Path(__file__).parent / "fixtures" / "sample_ledger.jsonl"
HARNESS = Path(__file__).parent / "js_harness_offline_viewer.mjs"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not available")


class _FixedDatetime(datetime):
    """Freezes ``bundle_cmd``'s ``datetime.now()`` so two separate CLI
    invocations produce byte-identical output -- the only thing that would
    otherwise differ run-to-run is the wall-clock ``created_at`` field,
    which has nothing to do with --with-viewer."""

    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 1, 1, tzinfo=timezone.utc)


def _run_js_harness(html_path: Path) -> dict:
    result = subprocess.run(
        ["node", str(HARNESS), str(html_path)],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    return json.loads(result.stdout)


def _embedded_fragment(html: str) -> str:
    marker = 'window.__BUNDLE_FRAGMENT_B64U__="'
    start = html.index(marker) + len(marker)
    end = html.index('"', start)
    return html[start:end]


def test_wire_format_byte_identical_with_or_without_viewer(tmp_path, monkeypatch):
    monkeypatch.setattr(bundle_cmd, "datetime", _FixedDatetime)
    out_path = tmp_path / "bundle.json"

    rc = main(["bundle", "--ledger", str(FIXTURE_LEDGER), "--out", str(out_path)])
    assert rc == 0
    without_viewer_bytes = out_path.read_bytes()

    rc = main(["bundle", "--ledger", str(FIXTURE_LEDGER), "--out", str(out_path), "--with-viewer"])
    assert rc == 0
    with_viewer_bytes = out_path.read_bytes()

    assert with_viewer_bytes == without_viewer_bytes


def test_with_viewer_writes_self_contained_html_alongside_json(tmp_path):
    out_path = tmp_path / "bundle.json"
    rc = main(["bundle", "--ledger", str(FIXTURE_LEDGER), "--out", str(out_path), "--with-viewer"])
    assert rc == 0

    viewer_path = tmp_path / "bundle.html"
    assert viewer_path.exists()
    html = viewer_path.read_text(encoding="utf-8")

    # No external resource references at all -- nothing to fetch on open.
    assert "<script src=" not in html
    assert "<link" not in html
    assert "@import" not in html

    fragment = _embedded_fragment(html)
    assert fragment != "@@BUNDLE_FRAGMENT@@"
    bundle = json.loads(out_path.read_bytes())
    payload = json.dumps(bundle, separators=(",", ":"), sort_keys=True).encode("utf-8")
    expected_fragment = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    assert fragment == expected_fragment


def test_viewer_out_overrides_default_path(tmp_path):
    out_path = tmp_path / "bundle.json"
    viewer_path = tmp_path / "custom-viewer.html"
    rc = main([
        "bundle", "--ledger", str(FIXTURE_LEDGER), "--out", str(out_path),
        "--viewer-out", str(viewer_path),
    ])
    assert rc == 0
    assert viewer_path.exists()
    assert not (tmp_path / "bundle.html").exists()


def test_offline_viewer_opens_and_verifies_with_networking_disabled(tmp_path):
    out_path = tmp_path / "bundle.json"
    rc = main(["bundle", "--ledger", str(FIXTURE_LEDGER), "--out", str(out_path), "--with-viewer"])
    assert rc == 0
    viewer_path = tmp_path / "bundle.html"

    result = _run_js_harness(viewer_path)
    assert result["loadError"] is None
    assert result["networkAttempts"] == []
    assert result["fragmentEmbedded"] is True
    assert result["recordCount"] == 4

    stages = {s["name"]: s["status"] for s in result["ritual"]["stages"]}
    assert stages["Integrity"] == "pass"
    # Only 1 of this fixture's 4 records declares a chain parent -- partial
    # coverage, honestly "skip" per the re-vendored viewer's three-valued
    # Sequence logic (scitt-cose #32). The pre-#32 viewer collapsed this to
    # a false "pass". The completeness certificate this branch adds is a
    # bundle-range attestation and does not change per-record chain coverage,
    # so Sequence stays an honest "skip".
    assert stages["Sequence"] == "skip"
    # capsule bundle now attaches an MMR completeness certificate for the
    # bundle's own record range -- genuinely verified here, not skipped.
    assert stages["Completeness"] == "pass"
    assert stages["Cross-check"] == "pass"


def test_offline_viewer_rejects_a_tampered_embedded_fragment(tmp_path):
    """Mutant proof the harness is a real check, not a pass-through: hand-
    corrupt the embedded fragment's chain link and confirm the Sequence
    stage genuinely flips to fail."""
    out_path = tmp_path / "bundle.json"
    rc = main(["bundle", "--ledger", str(FIXTURE_LEDGER), "--out", str(out_path), "--with-viewer"])
    assert rc == 0
    viewer_path = tmp_path / "bundle.html"
    html = viewer_path.read_text(encoding="utf-8")

    fragment = _embedded_fragment(html)
    padded = fragment + "=" * (-len(fragment) % 4)
    bundle = json.loads(base64.urlsafe_b64decode(padded))
    tampered = dict(bundle["records"][-1])
    tampered["chain"] = {**tampered["chain"], "parent_capsule_id": "f" * 64}
    bundle["records"][-1] = tampered
    raw = json.dumps(bundle, separators=(",", ":"), sort_keys=True).encode("utf-8")
    tampered_fragment = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    tampered_html = html.replace(fragment, tampered_fragment)
    tampered_path = tmp_path / "tampered.html"
    tampered_path.write_text(tampered_html, encoding="utf-8")

    result = _run_js_harness(tampered_path)
    assert result["loadError"] is None
    stages = {s["name"]: s["status"] for s in result["ritual"]["stages"]}
    assert stages["Sequence"] == "fail"


def test_offline_viewer_harness_itself_catches_a_forced_network_call(tmp_path):
    """Mutant proof the network block is real: inject a fetch() call into a
    copy of the harness and confirm it throws instead of silently
    succeeding -- the same defense the real harness relies on."""
    out_path = tmp_path / "bundle.json"
    rc = main(["bundle", "--ledger", str(FIXTURE_LEDGER), "--out", str(out_path), "--with-viewer"])
    assert rc == 0
    viewer_path = tmp_path / "bundle.html"

    mutant = tmp_path / "harness_mutant.mjs"
    src = HARNESS.read_text(encoding="utf-8").replace(
        "let loadError = null;",
        'let loadError = null;\n'
        'try { fetch("http://example.invalid"); } catch (e) { console.error(String(e)); process.exit(3); }\n',
    )
    mutant.write_text(src, encoding="utf-8")

    result = subprocess.run(
        ["node", str(mutant), str(viewer_path)],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 3
    assert "network attempted via fetch" in result.stderr
