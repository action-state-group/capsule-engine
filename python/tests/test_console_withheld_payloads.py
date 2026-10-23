# SPDX-License-Identifier: Apache-2.0
"""J.9.5: a payload the record commits to only by digest renders as withheld
with its digest, never as a blank field (#170, after the resolve-at-read
payload store was removed)."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from capsule_engine.console.api import _withheld_payloads

CONSOLE_JS = (Path(__file__).parent.parent / "capsule_engine" / "console" / "console.js").read_text(encoding="utf-8")
REASON = "a" * 64
EVIDENCE = "b" * 64


def test_reason_and_each_check_evidence_are_listed_as_withheld_with_their_digest():
    capsule = {
        "disposition": {"verdict_class": "blocked", "reason_digest": REASON},
        "constraints": [
            {"id": "caps", "result": "fail", "evidence_digest": EVIDENCE},
            {"id": "dedupe", "result": "pass"},
        ],
    }
    assert _withheld_payloads(capsule) == [
        {"label": "reason", "check_id": None, "digest": REASON, "state": "withheld"},
        {"label": "evidence", "check_id": "caps", "digest": EVIDENCE, "state": "withheld"},
    ]


def test_a_capsule_without_digests_lists_no_payloads():
    assert _withheld_payloads({}) == []
    assert _withheld_payloads({"disposition": None, "constraints": None}) == []
    assert _withheld_payloads({"disposition": {"reason_digest": ""}, "constraints": [{"id": "x"}]}) == []


def _function_source(name: str) -> str:
    match = re.search(rf"  function {name}\(.*?\n  \}}\n", CONSOLE_JS, re.S)
    assert match, f"{name} not found in console.js"
    return match.group(0)


# The browser's textContent -> innerHTML round trip that console.js's escapeHTML relies on.
_DOCUMENT_STUB = """
var document = {createElement: function () {
  var el = {};
  Object.defineProperty(el, "textContent", {set: function (v) {
    el.innerHTML = v.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }});
  return el;
}};
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available to run console.js")
def test_console_renders_each_payload_as_withheld_text_beside_its_full_digest():
    hostile = '<img src=x onerror="alert(1)">'
    capsule = {
        "disposition": {"reason_digest": REASON},
        "constraints": [{"id": hostile, "evidence_digest": EVIDENCE}],
    }
    script = (
        _DOCUMENT_STUB
        + _function_source("escapeHTML")
        + _function_source("withheldPayloadsHTML")
        + "process.stdout.write(withheldPayloadsHTML(JSON.parse(process.argv[1])));"
    )
    result = subprocess.run(
        ["node", "-e", script, json.dumps(_withheld_payloads(capsule))],
        capture_output=True, text=True, timeout=30, check=True,
    )
    lines = result.stdout.split("</div>")[:-1]
    assert len(lines) == 2
    assert lines[0].endswith(f"reason — withheld · digest {REASON}")
    assert lines[1].endswith(f"evidence · &lt;img src=x onerror=\"alert(1)\"&gt; — withheld · digest {EVIDENCE}")
    assert "<img" not in result.stdout
    assert "verify-stage" not in result.stdout


def test_console_inspector_appends_the_payloads_section_without_a_verify_stage():
    assert "verify-stage" not in _function_source("withheldPayloadsHTML")
    render = _function_source("renderInspector")
    assert "withheldPayloadsHTML(detail.payloads)" in render
    assert "inspector.appendChild(payloadsSection)" in render
