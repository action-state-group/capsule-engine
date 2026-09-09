# SPDX-License-Identifier: Apache-2.0
"""Word-discipline gate (manifesto v2.1 / `consistency-realignment-2026-09-06.md`,
[ldg-map-vocab-coverage-statement] build item 5): no unqualified
"contemporaneous", "non-repudiation", "witnessed" (bare), "complete history",
or "trust ladder" in the report/render surfaces and docs this vocabulary
realignment touches.

Scope, deliberately narrow: the modules that render fold/report output for a
human (``cli/format.py``, ``console/api.py``, ``report/``,
``packs/measurability_report.py``) plus ``docs/`` and ``README.md`` -- not a
repo-wide sweep. Pre-existing, unrelated uses of "witnessed" as a *field
name* mirroring ``capsule_emit``'s own chain-segment API
(``folds/retention_continuity.py``) and the standalone design-system gallery
(``console/gallery.html``) are a different, tracked concern -- see the
[ldg-map-vocab-coverage-statement] outbox entry, not silently rewritten here.

RED-before-green (QUEUE_PROTOCOL §7): the scan is proven able to fail before
it is trusted to pass.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

SCANNED_PATHS = (
    REPO_ROOT / "capsule_engine" / "cli" / "format.py",
    REPO_ROOT / "capsule_engine" / "console" / "api.py",
    REPO_ROOT / "capsule_engine" / "report",
    REPO_ROOT / "capsule_engine" / "packs" / "measurability_report.py",
    REPO_ROOT / "docs",
    REPO_ROOT / "README.md",
)

_SCANNED_SUFFIXES = {".py", ".md", ".html"}

# "witnessed (bare)" per the inbox item's own wording: qualified forms this
# realignment introduces (e.g. "continuity-witnessed") are not the overclaim
# being caught here.
BANNED_PATTERNS = {
    "contemporaneous": re.compile(r"\bcontemporaneous\b", re.IGNORECASE),
    "non-repudiation": re.compile(r"\bnon-repudiation\b", re.IGNORECASE),
    "witnessed (bare)": re.compile(r"(?<!continuity-)\bwitnessed\b", re.IGNORECASE),
    "complete history": re.compile(r"\bcomplete history\b", re.IGNORECASE),
    "trust ladder": re.compile(r"\btrust ladder\b", re.IGNORECASE),
}


def _iter_scanned_files(paths):
    for path in paths:
        if path.is_dir():
            yield from sorted(p for p in path.rglob("*") if p.is_file())
        elif path.is_file():
            yield path


def find_word_discipline_violations(paths=None) -> list[tuple[Path, str, int]]:
    """(file, banned_label, line_number) for every hit in ``paths`` (defaults
    to ``SCANNED_PATHS``) -- the same function the mutant test below calls
    against an injected file, so the production scan and the mutant proof
    share one code path."""
    violations: list[tuple[Path, str, int]] = []
    for path in _iter_scanned_files(SCANNED_PATHS if paths is None else paths):
        if path.suffix not in _SCANNED_SUFFIXES:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for label, pattern in BANNED_PATTERNS.items():
            for match in pattern.finditer(text):
                line_no = text.count("\n", 0, match.start()) + 1
                violations.append((path, label, line_no))
    return violations


def test_report_surfaces_and_docs_are_clean():
    violations = find_word_discipline_violations()
    assert violations == [], f"banned vocabulary found: {violations}"


_MUTANT_SENTENCES = {
    "contemporaneous": "this verdict is contemporaneous with the action",
    "non-repudiation": "this offers non-repudiation of the claim",
    "witnessed (bare)": "this checkpoint was witnessed by the anchor",
    "complete history": "the ledger shows a complete history of the range",
    "trust ladder": "read the trust ladder before the map",
}


def test_gate_flips_red_on_each_banned_phrase_then_recovers_green(tmp_path):
    assert set(_MUTANT_SENTENCES) == set(BANNED_PATTERNS)  # every banned label has a mutant case
    for label, sentence in _MUTANT_SENTENCES.items():
        mutant = tmp_path / "mutant.md"
        mutant.write_text(sentence)
        violations = find_word_discipline_violations([mutant])
        assert violations, f"gate did not flip red for {label!r} -- the check cannot fail"
        assert any(v[1] == label for v in violations)
        mutant.unlink()

    # green again against the real, unmutated surfaces
    assert find_word_discipline_violations() == []


def test_qualified_continuity_witnessed_is_not_flagged(tmp_path):
    """The bare-word rule must not punish the correct, qualified form this
    realignment recommends in place of unqualified "witnessed"."""
    sample = tmp_path / "sample.md"
    sample.write_text("this range is continuity-witnessed by the checkpoint-aware anchor")
    assert find_word_discipline_violations([sample]) == []
