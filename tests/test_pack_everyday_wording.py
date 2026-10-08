# SPDX-License-Identifier: Apache-2.0
"""The everyday pack's text says what is detected and recorded, never what is
prevented; makes no claim that a reader can re-run or check a verdict; and
carries none of the process vocabulary of the people who wrote it.

Scanned: every file in ``catalog/everyday/`` except the fixture ledger. The
ledger holds sealed records whose registered field values (for example
``severity: "blocking"``, method ``agent_action_capsule.verify``) are spec
tokens, not prose this pack wrote; the one sealed record quoted in
``gold_labels.yaml`` carries the same ``severity: blocking`` line, which is
the only line shape exempted.

Every gate runs through ``_gate``, which first requires a planted violation
to be matched -- a gate that matches nothing in the pack AND nothing in its
control is broken, not clean. Matching is case-insensitive except for the
two-letter role abbreviations, which are matched in capitals only because
lower-case "pm" occurs in ordinary text ("3 pm").
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from capsule_engine.packs.loader import load_pack_dir

PACK_DIR = Path(__file__).parent.parent / "capsule_engine" / "packs" / "catalog" / "everyday"
ALL_FILES = sorted(p for p in PACK_DIR.rglob("*") if p.is_file())
SCANNED = [p for p in ALL_FILES if p.name != "mini_ledger.jsonl"]

# Lines a gate never reads: the single interim-field declaration (the one
# place a bracketed id may appear) and the sealed spec token quoted above.
DECLARATION_LINE = re.compile(r"evidence-result schema \[recomputed-tier-code-identity\]")
SEALED_TOKEN_LINE = re.compile(r"^\s*severity: blocking$")

GATES = {
    "claim_verbs": (
        re.compile(r"\b(prevent\w*|stop\w*|block\w*|catch\w*|scam\w*|protect\w*|prove\w*)\b", re.IGNORECASE),
        "This pack prevents fraud and blocks every scam.",
    ),
    "re_runnability": (
        re.compile(r"\b(re-?run|check|verify)\b[^.\n]{0,20}\byourself\b", re.IGNORECASE),
        "You can re-run this yourself, or verify it yourself.",
    ),
    "process_vocabulary": (
        re.compile(
            r"\b(tranche\w*|brief\w*|batch\w*|inbox|outbox|lanes?|held|sherlock)\b"
            r"|\b(this|the) item\b|\bu\d{1,4}\b|\bP[1-9]\b|\bPM-\w+|\brc\d+\b",
            re.IGNORECASE,
        ),
        "Tranche 1 of the brief, held for rc6 per u66 and P2.",
    ),
    "role_abbreviations": (re.compile(r"\b(EM|PM)\b"), "The EM asked the PM."),
    "bracketed_ids": (re.compile(r"\[[a-z0-9]+(?:-[a-z0-9]+)+\]", re.IGNORECASE), "see [everyday-ask-decision-token]"),
}


def _hits(pattern: re.Pattern, text: str) -> list[str]:
    return [
        m.group(0)
        for line in text.splitlines()
        if not (DECLARATION_LINE.search(line) or SEALED_TOKEN_LINE.match(line))
        for m in pattern.finditer(line)
    ]


def _gate(name: str, path: Path) -> list[str]:
    pattern, control = GATES[name]
    assert _hits(pattern, control), f"gate {name!r} does not match its own planted control -- the gate is broken"
    return _hits(pattern, path.read_text())


def test_the_scan_covers_every_pack_file_but_the_ledger():
    assert len(SCANNED) == len(ALL_FILES) - 1
    assert {p.name for p in SCANNED} >= {"pack.yaml", "AI-BOOTSTRAP.md", "decision_table.yaml", "gold_labels.yaml"}


@pytest.mark.parametrize("name", sorted(GATES))
@pytest.mark.parametrize("path", SCANNED, ids=lambda p: p.name)
def test_gate(path, name):
    assert _gate(name, path) == []


def test_exempted_lines_are_exactly_the_declaration_and_the_quoted_sealed_token():
    exempted = [
        (p.name, line.strip())
        for p in SCANNED
        for line in p.read_text().splitlines()
        if DECLARATION_LINE.search(line) or SEALED_TOKEN_LINE.match(line)
    ]
    assert sorted(name for name, _ in exempted) == ["gold_labels.yaml", "gold_labels.yaml"]


PERMITTED_OPENING = re.compile(r"^(Records|Detects|Checks|Flags|Requires out-of-band consent|Produces)\b")
# A rule no check measures says so first, so its statement cannot read as a detection.
NOT_MEASURED_OPENING = re.compile(r"^Not measured yet\. ")


def test_every_measured_statement_opens_with_a_permitted_verb():
    assert PERMITTED_OPENING.match("Prevents a payment.") is None  # control
    obligations = [o for o in load_pack_dir(PACK_DIR).obligations if o.measurability == "measured"]
    assert len(obligations) == 24
    assert [o.id for o in obligations if PERMITTED_OPENING.match(o.statement)] == [o.id for o in obligations]


def test_every_declared_not_measured_statement_says_so_first_and_no_measured_one_does():
    assert NOT_MEASURED_OPENING.match("Flags a payment.") is None  # control
    obligations = load_pack_dir(PACK_DIR).obligations
    declared = [o.id for o in obligations if o.measurability == "declared_not_measured"]
    assert len(declared) == 3
    assert [o.id for o in obligations if NOT_MEASURED_OPENING.match(o.statement)] == declared
