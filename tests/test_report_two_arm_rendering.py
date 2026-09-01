# SPDX-License-Identifier: Apache-2.0
"""``render_report_html``'s ``arm`` parameter ("full" vs "guards-only"):
one codebase, one flag, never a fork -- ported from capsule-ledger's
test_two_arm_packaging.py's "report rendering" section during the
[ldg-ledger-scope-re-extraction] RESIDUALS pass. That file's CLI-registration
tests (which verbs "full"/"guards-only" register) stayed in capsule-ledger,
since packaging.py and the CLI arm mechanism are core there; this is the
report-rendering half, which only ever lived here (capsule-engine's
guard_cmds.py is the live ``capsule guard dry-run`` consumer)."""
from __future__ import annotations

from pathlib import Path

from capsule_engine.report import build_dry_run_report, render_report_html

FIXTURES = Path(__file__).parent / "fixtures"
NANDA = FIXTURES / "nanda_transaction_ledger.jsonl"


def test_render_report_html_default_arm_is_full(caps_fold):
    report = build_dry_run_report([str(NANDA)], caps_fold=caps_fold, since="7d")
    html, _ = render_report_html(report)
    assert 'data-arm="full"' in html
    assert '[data-arm="guards-only"]' not in html


def test_render_report_html_guards_only_hides_evidence_chrome(caps_fold):
    report = build_dry_run_report([str(NANDA)], caps_fold=caps_fold, since="7d")
    html, _ = render_report_html(report, arm="guards-only")
    assert 'data-arm="guards-only"' in html
    assert '[data-arm="guards-only"] .share-row' in html
    assert '[data-arm="guards-only"] .row-fp' in html


def test_render_report_html_verify_js_is_byte_identical_across_arms(caps_fold):
    """The evidence-hiding mechanism must never touch verify.js itself --
    only the surrounding static shell -- so the JS/Python digest-parity
    tests in test_report_dry_run.py keep meaning what they say in both arms."""
    report = build_dry_run_report([str(NANDA)], caps_fold=caps_fold, since="7d")
    full_html, _ = render_report_html(report, arm="full")
    guards_html, _ = render_report_html(report, arm="guards-only")

    def _script_body(html: str) -> str:
        start = html.index("<script>\n") + len("<script>\n")
        end = html.index("</script>", start)
        return html[start:end]

    assert _script_body(full_html) == _script_body(guards_html)
