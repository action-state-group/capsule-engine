# SPDX-License-Identifier: Apache-2.0
"""J.9.5: a payload the record commits to only by digest renders as withheld
with its digest, never as a blank field (#170, after the resolve-at-read
payload store was removed)."""
from __future__ import annotations

import re
from pathlib import Path

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


def test_console_renders_each_payload_as_withheld_beside_its_digest_and_never_as_a_verify_failure():
    match = re.search(r"function withheldPayloadsHTML\(payloads\) \{.*?\n  \}\n", CONSOLE_JS, re.S)
    assert match, "withheldPayloadsHTML not found in console.js"
    body = match.group(0)
    assert "withheld" in body and "escapeHTML(p.digest)" in body
    assert "verify-stage" not in body
    assert "withheldPayloadsHTML(detail.payloads)" in CONSOLE_JS
