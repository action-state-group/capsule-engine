# SPDX-License-Identifier: Apache-2.0
"""The everyday pack's text says what is detected and recorded, never what is
prevented, and makes no claim that a reader can re-run or check a verdict.

Scanned: every text file in ``catalog/everyday/`` except the fixture ledger.
The ledger holds sealed records whose registered field values (for example
``severity: "blocking"``, method ``agent_action_capsule.verify``) are spec
tokens, not prose this pack wrote.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from capsule_engine.packs.loader import load_pack_dir

PACK_DIR = Path(__file__).parent.parent / "capsule_engine" / "packs" / "catalog" / "everyday"
SCANNED = sorted(p for p in PACK_DIR.rglob("*") if p.is_file() and p.name != "mini_ledger.jsonl")

FORBIDDEN = re.compile(
    r"\b(prevent\w*|stop\w*|block\w*|catch\w*|scam\w*|protect\w*|prove\w*)\b"
    r"|\b(re-?run|check|verify)\b[^.\n]{0,20}\byourself\b",
    re.IGNORECASE,
)
# Process vocabulary that has no place in shipped pack files.
PROCESS_WORDS = re.compile(
    r"\b(tranche\w*|brief\w*|batch\w*|inbox|outbox|lane|lanes|held|Sherlock)\b"
    r"|\b(this|the) item\b|\bu\d{1,4}\b|\bP[1-9]\b|\bPM-\w+",
    re.IGNORECASE,
)
PROCESS_WORDS_CASED = re.compile(r"\b(EM|PM)\b")
PERMITTED_OPENING = re.compile(r"^(Records|Detects|Checks|Flags|Requires out-of-band consent|Produces)\b")


def test_the_scan_covers_the_pack_text():
    assert {p.name for p in SCANNED} >= {"pack.yaml", "AI-BOOTSTRAP.md", "decision_table.yaml"}


@pytest.mark.parametrize("path", SCANNED, ids=lambda p: p.name)
def test_no_forbidden_claim_wording(path):
    hits = [(n, m.group(0)) for n, line in enumerate(path.read_text().splitlines(), 1) for m in FORBIDDEN.finditer(line)]
    assert hits == []


@pytest.mark.parametrize("path", SCANNED, ids=lambda p: p.name)
def test_no_process_vocabulary(path):
    text = path.read_text()
    hits = [m.group(0) for m in PROCESS_WORDS.finditer(text)]
    hits += [m.group(0) for m in PROCESS_WORDS_CASED.finditer(text)]
    assert hits == []


def test_process_pattern_matches_what_it_must():
    for text in ("tranche 1", "the item", "u66", "P2 lands", "PM-Pack", "held sub-part"):
        assert PROCESS_WORDS.search(text), text
    assert PROCESS_WORDS_CASED.search("the EM said")
    assert not PROCESS_WORDS.search("the first release of this pack")


def test_forbidden_pattern_matches_what_it_must():
    for text in ("this prevents fraud", "it blocks a scam", "Re-run this yourself", "verify it yourself"):
        assert FORBIDDEN.search(text), text
    assert not FORBIDDEN.search("Detects a payment over a watched rail and records the rail it read.")


def test_every_statement_opens_with_a_permitted_verb():
    for obligation in load_pack_dir(PACK_DIR).obligations:
        assert PERMITTED_OPENING.match(obligation.statement), obligation.id
