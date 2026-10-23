# SPDX-License-Identifier: Apache-2.0
"""``format_coverage_footnote`` (T2R rev9(a),
`terms-to-report-design-2026-08-25.md`): the per-number coverage footnote a
report attaches to every rendered fold result -- "a report cannot render a
number without its coverage statement." Pinned literal format, same
discipline as `format_envelope_line` (`test_console_product_laws.py`)."""
from __future__ import annotations

from capsule_engine.cli.format import format_coverage_footnote


def test_unknown_capture_and_no_reconciliation_render_visibly():
    footnote = format_coverage_footnote({"range": [0, 9], "capture": "unknown", "reconciled": None})
    assert footnote == "range-complete through 9 · captured under boundary: unknown · reconciled: none"


def test_declared_capture_and_reconciliation_render_their_values():
    footnote = format_coverage_footnote(
        {
            "range": [0, 9],
            "capture": {"boundary": "ledger-write", "rule": "every accepted capsule"},
            "reconciled": {"source": "vendor-invoice-export", "interval": "2026-08-01/2026-08-31"},
        }
    )
    assert footnote == (
        "range-complete through 9 · captured under boundary ledger-write, rule every accepted capsule · "
        "reconciled against vendor-invoice-export over interval 2026-08-01/2026-08-31"
    )


def test_declared_capture_with_no_reconciliation():
    footnote = format_coverage_footnote(
        {"range": [0, 9], "capture": {"boundary": "ledger-write", "rule": "every accepted capsule"}, "reconciled": None}
    )
    assert footnote.endswith("· reconciled: none")
    assert "captured under boundary: unknown" not in footnote
