# SPDX-License-Identifier: Apache-2.0
"""The tau2-airline reference summary carries a coverage footnote on every
number it renders -- no number without the range it was counted over.

Each dataset row's numbers cite one footnote: that dataset's own coverage
statement (``EvaluationTrace.coverage_statement()`` rendered by
``cli.format.format_coverage_footnote``), computed by a real catalog fold
over the dataset's sealed records. No capture boundary is declared for this
replay, so every footnote must say so visibly rather than omit it.

The count check enumerates the rendered numbers itself and requires the
footnoted count to equal the raw count, and it is shown failing on a
rendered number with its marker stripped.
"""
from __future__ import annotations

import contextlib
import io
import re

import pytest

from capsule_engine.examples import tau2_airline_reference as ref

# A rendered number: digits that stand alone, not part of an identifier
# (dataset names like ``tau2-gpt-4-1``, capsule hex, file paths, quoted ids,
# ``key=value`` fragments) and not the digits inside a ``[k]`` marker.
_NUMBER = re.compile(r"(?<![\w.\-\"'/\[:=])\d+(?![\w.\-\"'/\]:])")
_FOOTNOTED_NUMBER = re.compile(r"(?<![\w.\-\"'/\[:=])\d+ \[(\d+)\]")
_FOOTNOTE_LINE = re.compile(r"^\[(\d+)\] (.+)$")


def _render(tmp_path) -> str:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert ref.main(["--all", "--out-dir", str(tmp_path)]) == 0
    return out.getvalue()


def _split(report: str) -> tuple[list[str], dict[str, str]]:
    body: list[str] = []
    footnotes: dict[str, str] = {}
    for line in report.splitlines():
        match = _FOOTNOTE_LINE.match(line)
        if match:
            assert match.group(1) not in footnotes, f"footnote [{match.group(1)}] defined twice"
            footnotes[match.group(1)] = match.group(2)
        else:
            body.append(line)
    return body, footnotes


def _unfootnoted_numbers(body: list[str]) -> int:
    """Raw rendered numbers minus the ones carrying a ``[k]`` marker."""
    text = "\n".join(body)
    return len(_NUMBER.findall(text)) - len(_FOOTNOTED_NUMBER.findall(text))


@pytest.fixture(scope="module")
def report(tmp_path_factory) -> str:
    return _render(tmp_path_factory.mktemp("tau2-out"))


def test_every_rendered_number_carries_a_coverage_footnote(report):
    body, footnotes = _split(report)
    text = "\n".join(body)
    raw = _NUMBER.findall(text)
    matched = _FOOTNOTED_NUMBER.findall(text)
    # 4 numbers per dataset row + the capsules-written line per dataset.
    assert len(raw) == 5 * len(ref.DATASETS)
    assert len(matched) == len(raw)
    assert set(matched) == set(footnotes), "every marker resolves to a footnote, and every footnote is cited"


def test_one_footnote_per_dataset_naming_it(report):
    _, footnotes = _split(report)
    assert len(footnotes) == len(ref.DATASETS)
    assert sorted(text.split(":", 1)[0] for text in footnotes.values()) == sorted(ref.DATASETS)


def test_absent_capture_boundary_renders_unknown_never_omitted(report):
    _, footnotes = _split(report)
    assert len(footnotes) == len(ref.DATASETS)  # never vacuously green on zero footnotes
    for text in footnotes.values():
        assert "range-complete through " in text
        assert "captured under boundary: unknown" in text
        assert "reconciled: none" in text


def test_the_footnote_range_and_capsule_count_come_from_the_sealed_records(tmp_path):
    result = ref.run_dataset("tau2-o4-mini", ref.DATASETS["tau2-o4-mini"], store_dir=str(tmp_path / "store"))
    trace = ref.coverage_trace(result)
    assert trace.result == len(result.records)
    assert trace.range_ == (0, len(result.records) - 1)
    assert trace.coverage_statement() == {
        "range": [0, len(result.records) - 1],
        "capture": "unknown",
        "reconciled": None,
    }


def test_mutant_a_number_without_its_marker_fails_the_count(report):
    body, _ = _split(report)
    assert _unfootnoted_numbers(body) == 0
    marker = next(m for m in _FOOTNOTED_NUMBER.finditer("\n".join(body)))
    stripped = "\n".join(body).replace(marker.group(0), marker.group(0).split(" ", 1)[0], 1)
    assert _unfootnoted_numbers(stripped.splitlines()) == 1
