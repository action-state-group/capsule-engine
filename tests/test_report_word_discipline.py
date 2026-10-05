# SPDX-License-Identifier: Apache-2.0
"""Word-discipline gate over RENDERED PROSE: no unqualified
"contemporaneous", "non-repudiation", "witnessed" (bare), "complete history",
or "trust ladder" in text a person reads -- rendered strings and docs. A
registration claim is written as "registered <= T", never "contemporaneous".

Scope: every ``.py``, ``.md``, ``.html`` and ``.js`` file under
``capsule_engine/``, plus ``docs/`` and ``README.md``. Within those files
only rendered prose is checked:

* Python: string literals, including f-string text. Docstrings, comments
  and identifiers are source, not rendered output.
* Markdown: text outside fenced code blocks and inline code spans.
* HTML: text outside tags, ``<style>``, and ``<script>`` (whose string
  literals are checked as JavaScript).
* JavaScript: string literals.

A banned word wrapped in quotes or backticks names the wire value rather
than claiming the property, and is allowed: that covers a literal that is
the word alone (``"contemporaneous"``, a provenance mode; ``"witnessed"``, a
grade enum member) as well as a mention inside a sentence (``grade
'witnessed'``). "Bare" witnessed means no hyphenated qualifier:
"log-witnessed" and "continuity-witnessed" say what witnessed it.

Wire vocabulary that must never trip this gate: the ``contemporaneous``
provenance mode (``packs/backfill_coverage.py`` ``MODE_CONTEMPORANEOUS``),
``contemporaneous_count`` (``report/coverage.py``), and the ``witnessed``
grade value of the Evidence Result schema (``report/result.py``,
``report/result_from_folds.py``). Those files are scanned, not exempted:
the tests below show the gate sees their occurrences and classifies them as
wire, and show it failing on planted prose in each file format.
"""
from __future__ import annotations

import ast
import io
import re
import tokenize
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

SCANNED_PATHS = (
    REPO_ROOT / "capsule_engine",
    REPO_ROOT / "docs",
    REPO_ROOT / "README.md",
)

_SCANNED_SUFFIXES = {".py", ".md", ".html", ".js"}

BANNED_PATTERNS = {
    "contemporaneous": re.compile(r"\bcontemporaneous\b", re.IGNORECASE),
    "non-repudiation": re.compile(r"\bnon-repudiation\b", re.IGNORECASE),
    "witnessed (bare)": re.compile(r"(?<![\w-])witnessed\b", re.IGNORECASE),
    "complete history": re.compile(r"\bcomplete history\b", re.IGNORECASE),
    "trust ladder": re.compile(r"\btrust ladder\b", re.IGNORECASE),
}

_QUOTES = "'\"`"
_JS_STRING = re.compile(r"'(?:[^'\\\n]|\\.)*'|\"(?:[^\"\\\n]|\\.)*\"|`(?:[^`\\]|\\.)*`")
_HTML_BLOCK = re.compile(r"<(script|style)\b[^>]*>(.*?)</\1\s*>", re.IGNORECASE | re.DOTALL)
_HTML_TAG = re.compile(r"<[^>]*>")
_MD_FENCE = re.compile(r"^(```|~~~).*?^\1[^\n]*$", re.MULTILINE | re.DOTALL)
_MD_CODE_SPAN = re.compile(r"`[^`\n]+`")


@dataclass(frozen=True)
class Occurrence:
    path: Path
    label: str
    line: int
    rendered_prose: bool


def _docstring_starts(tree: ast.Module) -> set[tuple[int, int]]:
    starts = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            first = node.body[0] if node.body else None
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                starts.add((first.lineno, first.col_offset))
    return starts


def _python_prose(text: str) -> list[tuple[int, int]]:
    line_starts = [0] + [m.end() for m in re.finditer("\n", text)]
    docstrings = _docstring_starts(ast.parse(text))
    prose_types = {tokenize.STRING}
    if hasattr(tokenize, "FSTRING_MIDDLE"):
        prose_types.add(tokenize.FSTRING_MIDDLE)
    regions = []
    for tok in tokenize.generate_tokens(io.StringIO(text).readline):
        if tok.type not in prose_types:
            continue
        if tok.type == tokenize.STRING and tok.start in docstrings:
            continue
        start = line_starts[tok.start[0] - 1] + tok.start[1]
        end = line_starts[tok.end[0] - 1] + tok.end[1]
        regions.append((start, end))
    return regions


def _js_prose(text: str, offset: int = 0) -> list[tuple[int, int]]:
    return [(offset + m.start(), offset + m.end()) for m in _JS_STRING.finditer(text)]


def _html_prose(text: str) -> list[tuple[int, int]]:
    regions = []
    masked = list(text)
    for block in _HTML_BLOCK.finditer(text):
        if block.group(1).lower() == "script":
            regions.extend(_js_prose(block.group(2), block.start(2)))
        masked[block.start():block.end()] = "<" * (block.end() - block.start())
    for tag in _HTML_TAG.finditer("".join(masked)):
        masked[tag.start():tag.end()] = "<" * (tag.end() - tag.start())
    for run in re.finditer(r"[^<]+", "".join(masked)):
        regions.append((run.start(), run.end()))
    return regions


def _markdown_prose(text: str) -> list[tuple[int, int]]:
    code = [(m.start(), m.end()) for m in _MD_FENCE.finditer(text)]
    code += [(m.start(), m.end()) for m in _MD_CODE_SPAN.finditer(text)]
    regions, cursor = [], 0
    for start, end in sorted(code):
        if start > cursor:
            regions.append((cursor, start))
        cursor = max(cursor, end)
    regions.append((cursor, len(text)))
    return regions


_PROSE_EXTRACTORS = {".py": _python_prose, ".md": _markdown_prose, ".html": _html_prose, ".js": _js_prose}


def _quoted(text: str, start: int, end: int) -> bool:
    return start > 0 and end < len(text) and text[start - 1] in _QUOTES and text[end] == text[start - 1]


def _iter_scanned_files(paths):
    for path in paths:
        if path.is_dir():
            yield from sorted(p for p in path.rglob("*") if p.is_file() and p.suffix in _SCANNED_SUFFIXES)
        elif path.is_file() and path.suffix in _SCANNED_SUFFIXES:
            yield path


def classify_occurrences(paths=None) -> list[Occurrence]:
    """Every raw match of every banned pattern in the scanned files, each
    classified once: rendered prose, or not (source, wire value, code span,
    quoted mention)."""
    occurrences: list[Occurrence] = []
    for path in _iter_scanned_files(SCANNED_PATHS if paths is None else paths):
        text = path.read_text(encoding="utf-8", errors="ignore")
        regions = _PROSE_EXTRACTORS[path.suffix](text)
        for label, pattern in BANNED_PATTERNS.items():
            for match in pattern.finditer(text):
                in_prose = any(start <= match.start() and match.end() <= end for start, end in regions)
                occurrences.append(
                    Occurrence(
                        path=path,
                        label=label,
                        line=text.count("\n", 0, match.start()) + 1,
                        rendered_prose=in_prose and not _quoted(text, match.start(), match.end()),
                    )
                )
    return occurrences


def find_word_discipline_violations(paths=None) -> list[tuple[Path, str, int]]:
    """(file, banned_label, line_number) for every banned word in rendered
    prose -- the same function the planted-violation tests call."""
    return [(o.path, o.label, o.line) for o in classify_occurrences(paths) if o.rendered_prose]


def _raw_match_count(paths=None) -> int:
    total = 0
    for path in _iter_scanned_files(SCANNED_PATHS if paths is None else paths):
        text = path.read_text(encoding="utf-8", errors="ignore")
        total += sum(len(pattern.findall(text)) for pattern in BANNED_PATTERNS.values())
    return total


# -- the real tree -----------------------------------------------------------


def test_rendered_prose_and_docs_are_clean():
    violations = find_word_discipline_violations()
    assert violations == [], f"retired vocabulary in rendered prose: {violations}"


def test_every_raw_match_is_classified_exactly_once():
    """Enumerates: the classified rows equal the raw regex rows, so nothing
    the patterns hit escapes classification, and the gate is not green
    because it saw nothing."""
    occurrences = classify_occurrences()
    assert len(occurrences) == _raw_match_count()
    assert len(occurrences) > 0


_WIRE_FILES = {
    "packs/backfill_coverage.py": "contemporaneous",
    "report/coverage.py": "contemporaneous",
    "report/result.py": "witnessed (bare)",
    "report/result_from_folds.py": "witnessed (bare)",
}


def test_real_wire_constants_are_scanned_and_pass():
    """The files holding the live wire values are IN scope, the gate sees
    their occurrences, and every one is classified as not rendered prose."""
    occurrences = classify_occurrences()
    for relative, label in _WIRE_FILES.items():
        path = REPO_ROOT / "capsule_engine" / relative
        hits = [o for o in occurrences if o.path == path and o.label == label]
        assert hits, f"{relative}: no {label!r} occurrence seen -- the wire-constant check would be vacuous"
        assert [o for o in hits if o.rendered_prose] == [], f"{relative}: wire value classified as prose"


# -- planted cases: the gate must go red on prose and stay green on wire ------

_MUTANT_SENTENCES = {
    "contemporaneous": "this verdict is contemporaneous with the action",
    "non-repudiation": "this offers non-repudiation of the claim",
    "witnessed (bare)": "this checkpoint was witnessed by the anchor",
    "complete history": "the ledger shows a complete history of the range",
    "trust ladder": "read the trust ladder before the map",
}

_PLANTED_WIRE_PY = '''\
"""Docstring prose is source, not rendered: contemporaneous, witnessed."""
MODE_CONTEMPORANEOUS = "contemporaneous"
VALID_GRADES = ("self-attested", "witnessed", "countersigned")


def summary(record, contemporaneous_count: int) -> str:
    # comments are not rendered either: witnessed, contemporaneous
    if record["provenance_mode"]["mode"] == MODE_CONTEMPORANEOUS and record["grade"] == "witnessed":
        return f"{contemporaneous_count} record(s) with grade 'witnessed'"
    return "registered <= T"
'''


def _plant(tmp_path, suffix: str, sentence: str) -> Path:
    wrappers = {
        ".py": f"detail = {sentence!r}\n",
        ".md": f"# Notes\n\n{sentence}.\n",
        ".html": f"<div class=\"x\"><span>{sentence}</span></div>\n",
        ".js": f"node.textContent = {sentence!r};\n",
    }
    path = tmp_path / f"planted{suffix}"
    path.write_text(wrappers[suffix], encoding="utf-8")
    return path


def test_gate_goes_red_on_each_retired_term_in_each_prose_format(tmp_path):
    assert set(_MUTANT_SENTENCES) == set(BANNED_PATTERNS)  # every banned label has a planted case
    for suffix in sorted(_PROSE_EXTRACTORS):
        for label, sentence in _MUTANT_SENTENCES.items():
            planted = _plant(tmp_path, suffix, sentence)
            violations = find_word_discipline_violations([planted])
            assert [v[1] for v in violations] == [label], f"{suffix}: gate did not go red for {label!r}"
            planted.unlink()


def test_gate_stays_green_on_planted_wire_constants_then_red_on_one_prose_line(tmp_path):
    wire = tmp_path / "wire.py"
    wire.write_text(_PLANTED_WIRE_PY, encoding="utf-8")
    occurrences = classify_occurrences([wire])
    # 2 docstring + 2 comment + 4 quoted (3 literals that are the word alone, 1 mention)
    assert len(occurrences) == _raw_match_count([wire]) == 8
    assert find_word_discipline_violations([wire]) == []

    wire.write_text(_PLANTED_WIRE_PY + 'DETAIL = "this record is contemporaneous with the action"\n', encoding="utf-8")
    assert [v[1] for v in find_word_discipline_violations([wire])] == ["contemporaneous"]


def test_gate_goes_red_on_a_retired_term_in_f_string_text(tmp_path):
    """Rendered detail strings are often f-strings; their literal text is
    prose like any other string."""
    planted = tmp_path / "planted_fstring.py"
    planted.write_text('detail = f"{n} contemporaneous + {m} backfilled record(s)"\n', encoding="utf-8")
    assert [v[1] for v in find_word_discipline_violations([planted])] == ["contemporaneous"]


def test_code_spans_and_fences_in_markdown_are_wire_mentions(tmp_path):
    doc = tmp_path / "doc.md"
    # The span and the fence each hold the word UNQUOTED, so only the
    # code-span and fence handling keeps them out of prose.
    doc.write_text(
        "The mode is set with `mode: contemporaneous`.\n\n```python\nGRADE = GRADES[1]  # the witnessed grade\n```\n",
        encoding="utf-8",
    )
    assert len(classify_occurrences([doc])) == 2
    assert find_word_discipline_violations([doc]) == []


def test_qualified_witnessed_is_not_flagged(tmp_path):
    """The bare-word rule must not punish the qualified forms used in place
    of unqualified "witnessed"."""
    sample = tmp_path / "sample.md"
    sample.write_text("this range is continuity-witnessed; the time is log-witnessed", encoding="utf-8")
    assert find_word_discipline_violations([sample]) == []
