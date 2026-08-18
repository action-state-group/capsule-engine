# SPDX-License-Identifier: Apache-2.0
"""Dry-run report artifact: real replay data shape, fragment round-trip,
no-network-in-the-page, and cross-language (Python/JS) digest parity.

The last of these matters more than it looks: ``verify.js`` is a hand port
of ``agent_action_capsule.canonical``'s JCS + SHA-256, not a reimplementation
this suite trusts by inspection -- ``test_js_digest_matches_python`` actually
runs it (via Node) against a real capsule and compares to the Python
reference, and ``test_js_digest_flips_on_tamper`` confirms it rejects a
tampered capsule rather than passing everything (the "must fail its
mutants" guardrail -- a check that can't fail isn't a check).
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from agent_action_capsule import compute_capsule_id

from capsule_ledger.cli.main import main as cli_main
from capsule_ledger.report import build_dry_run_report, render_report_html
from capsule_ledger.report.render import decode_fragment, to_fragment_payload

FIXTURES = Path(__file__).parent / "fixtures"
NANDA = FIXTURES / "nanda_transaction_ledger.jsonl"
AMAURY = FIXTURES / "amaury_sample_ledger.jsonl"
SAMPLE = FIXTURES / "sample_ledger.jsonl"
JS_SOURCE = Path(__file__).parent.parent / "capsule_ledger" / "report" / "static" / "verify.js"
JS_HARNESS = Path(__file__).parent / "js_harness_capsule_digest.mjs"

CAPS_MINOR = {"money.transfer": 10_000_000}


def test_dedupe_would_have_held_from_real_nanda_replay(caps_fold):
    report = build_dry_run_report([str(NANDA)], caps_fold=caps_fold, since="7d")

    assert report.actions_replayed == 36
    by_id = {s.guard_id: s for s in report.guards}
    assert len(by_id["dedupe"].rows) == 35
    assert len(by_id["caps"].rows) == 0
    assert len(by_id["verify_before_dispatch"].rows) == 0

    row = by_id["dedupe"].rows[0]
    assert row.agent == "biz_capsule-0"
    assert "already recorded" in row.why
    assert row.cited_capsule is not None
    assert row.cited_capsule["capsule_id"] == row.cited_capsule_id


def test_caps_would_have_held_from_real_amaury_replay(caps_fold):
    report = build_dry_run_report([str(AMAURY)], caps_fold=caps_fold, since="7d", caps_minor=CAPS_MINOR)

    by_id = {s.guard_id: s for s in report.guards}
    assert len(by_id["dedupe"].rows) == 0
    assert len(by_id["caps"].rows) == 1
    assert len(by_id["verify_before_dispatch"].rows) == 0

    row = by_id["caps"].rows[0]
    assert row.amount_minor == 15_000_000
    assert row.currency == "EUR"
    assert "150,000.00" in row.why
    assert row.capsule["disposition"]["verdict_class"] == "hitl_dispatched"

    guard_id, consequential_row = report.consequential()
    assert guard_id == "caps"
    assert consequential_row is row


def test_caps_never_triggers_when_unconfigured(caps_fold):
    """Matches ``GuardEngine``'s own default: no configured cap for a class
    means the caps check is always n/a for it, never a fabricated hit."""
    report = build_dry_run_report([str(AMAURY)], caps_fold=caps_fold, since="7d", caps_minor=None)
    by_id = {s.guard_id: s for s in report.guards}
    assert len(by_id["caps"].rows) == 0


def test_since_all_replays_every_record_across_ledgers(caps_fold):
    report = build_dry_run_report(
        [str(NANDA), str(AMAURY)], caps_fold=caps_fold, since=None, caps_minor=CAPS_MINOR
    )
    assert report.actions_replayed == 40
    assert report.record_range == (1, 40)


def test_since_window_drops_older_ledger_when_anchored_to_latest(caps_fold):
    """``since`` anchors to the replayed set's own latest timestamp (never
    the wall clock) -- combining ledgers ~2 weeks apart under a 7d window
    drops the older one entirely, which is real, correct behavior, not a bug."""
    report = build_dry_run_report(
        [str(NANDA), str(AMAURY)], caps_fold=caps_fold, since="7d", caps_minor=CAPS_MINOR
    )
    assert report.actions_replayed == 4  # only amaury's records survive the window
    assert report.operator == "acme-research"


def test_dry_run_never_blocks_real_decisions(caps_fold):
    """Every decision produced by the replay is a real, signed capsule --
    dry_run mode never withholds one, matching engine.py's own contract."""
    report = build_dry_run_report([str(NANDA)], caps_fold=caps_fold, since="7d")
    for section in report.guards:
        for row in section.rows:
            assert row.capsule.get("capsule_id")
            assert row.capsule["asg_payload"]["checkpoint"]["dry_run"] is True


def test_model_note_is_omitted_by_default_and_generator_works_without_it(caps_fold):
    report = build_dry_run_report([str(AMAURY)], caps_fold=caps_fold, since="7d", caps_minor=CAPS_MINOR)
    assert report.model_note is None
    payload = to_fragment_payload(report)
    assert payload["model_note"] is None
    html, fragment = render_report_html(report)
    assert html  # renders fine with no note at all


def test_model_note_passthrough_never_fabricated(caps_fold):
    report = build_dry_run_report(
        [str(AMAURY)],
        caps_fold=caps_fold,
        since="7d",
        caps_minor=CAPS_MINOR,
        model_note="a pre-written operator quote",
        model_id="some-model",
    )
    assert report.model_note.quote == "a pre-written operator quote"
    assert report.model_note.model_id == "some-model"


def test_fragment_round_trips_and_is_the_single_source_of_truth(caps_fold):
    report = build_dry_run_report([str(NANDA)], caps_fold=caps_fold, since="7d")
    html, fragment = render_report_html(report)
    assert decode_fragment(fragment) == to_fragment_payload(report)


def test_html_carries_no_ledger_data_only_the_fragment_does(caps_fold):
    """The whole privacy property: grep the static HTML page (not the
    fragment) for anything ledger-specific -- it must not be there."""
    report = build_dry_run_report([str(NANDA)], caps_fold=caps_fold, since="7d")
    html, fragment = render_report_html(report)

    assert "biz_capsule" not in html  # the real operator/developer name
    for section in report.guards:
        for row in section.rows:
            assert row.capsule["capsule_id"] not in html

    payload = decode_fragment(fragment)
    assert payload["operator"] == "biz_capsule"


@pytest.mark.parametrize("needle", ["fetch(", "XMLHttpRequest", "googleapis", "navigator.sendBeacon"])
def test_no_network_calls_in_the_page(caps_fold, needle):
    report = build_dry_run_report([str(AMAURY)], caps_fold=caps_fold, since="7d", caps_minor=CAPS_MINOR)
    html, _ = render_report_html(report)
    assert needle not in html


def test_no_external_script_or_link_src(caps_fold):
    report = build_dry_run_report([str(AMAURY)], caps_fold=caps_fold, since="7d", caps_minor=CAPS_MINOR)
    html, _ = render_report_html(report)
    assert 'src="http' not in html
    assert "src='http" not in html
    assert "fonts.googleapis.com" not in html
    assert "fonts.gstatic.com" not in html


def test_reproducible_replay_is_a_real_precondition_for_verify(caps_fold):
    """Two independent replays of the same ledger must agree byte-for-byte
    (modulo the wall-clock generated_at) -- this is what makes ``--verify``
    a meaningful check rather than a coin flip."""
    first = to_fragment_payload(build_dry_run_report([str(NANDA)], caps_fold=caps_fold, since="7d"))
    second = to_fragment_payload(build_dry_run_report([str(NANDA)], caps_fold=caps_fold, since="7d"))
    first.pop("generated_at")
    second.pop("generated_at")
    assert first == second


def test_every_cited_capsule_actually_reverifies_with_the_reference_library(caps_fold):
    """Python-side twin of what verify.js does in the browser: recompute
    each cited capsule's own id with the *public* reference library (not
    this package's code) and confirm it matches."""
    report = build_dry_run_report([str(NANDA)], caps_fold=caps_fold, since="7d")
    checked = 0
    for _, row in report.held_rows:
        assert compute_capsule_id(row.capsule) == row.capsule["capsule_id"]
        checked += 1
        if row.cited_capsule is not None:
            assert compute_capsule_id(row.cited_capsule) == row.cited_capsule["capsule_id"]
            checked += 1
    assert checked > 0


def test_tampering_a_cited_capsule_is_detected_not_silently_passed(caps_fold):
    """The mutant: flip one byte of a real cited capsule's payload and
    confirm recompute stops matching -- a verifier that can't catch this
    isn't verifying anything."""
    report = build_dry_run_report([str(AMAURY)], caps_fold=caps_fold, since="7d", caps_minor=CAPS_MINOR)
    row = report.guards[[s.guard_id for s in report.guards].index("caps")].rows[0]
    tampered = json.loads(json.dumps(row.capsule))
    tampered["asg_payload"]["amount_minor"] = 1  # was 15_000_000

    assert compute_capsule_id(tampered) != tampered["capsule_id"]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available for JS/Python parity check")
def test_js_digest_matches_python(caps_fold):
    report = build_dry_run_report([str(AMAURY)], caps_fold=caps_fold, since="7d", caps_minor=CAPS_MINOR)
    row = next(r for s in report.guards for r in s.rows)
    capsule = row.capsule

    result = subprocess.run(
        ["node", str(JS_HARNESS), str(JS_SOURCE)],
        input=json.dumps(capsule),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == capsule["capsule_id"] == compute_capsule_id(capsule)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available for JS/Python parity check")
def test_js_digest_flips_on_tamper(caps_fold):
    report = build_dry_run_report([str(AMAURY)], caps_fold=caps_fold, since="7d", caps_minor=CAPS_MINOR)
    row = next(r for s in report.guards for r in s.rows)
    tampered = json.loads(json.dumps(row.capsule))
    tampered["asg_payload"]["amount_minor"] = 1

    result = subprocess.run(
        ["node", str(JS_HARNESS), str(JS_SOURCE)],
        input=json.dumps(tampered),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() != tampered["capsule_id"]


# -- CLI ------------------------------------------------------------------


def test_cli_dry_run_writes_report_and_verifies(tmp_path):
    out = tmp_path / "report.html"
    rc = cli_main(
        [
            "guard",
            "dry-run",
            "--ledger",
            str(AMAURY),
            "--since",
            "7d",
            "--cap",
            "money.transfer=10000000",
            "--out",
            str(out),
            "--verify",
        ]
    )
    assert rc == 0
    assert out.exists()
    html = out.read_text()
    assert "acme-research" not in html  # no ledger data server-side either


def test_cli_share_prints_full_fragment_url(tmp_path, capsys):
    out = tmp_path / "report.html"
    rc = cli_main(
        ["guard", "dry-run", "--ledger", str(NANDA), "--since", "7d", "--out", str(out), "--share"]
    )
    assert rc == 0
    printed = capsys.readouterr().out
    assert f"file://{out.resolve()}#" in printed


def test_cli_rejects_malformed_cap(tmp_path, capsys):
    out = tmp_path / "report.html"
    rc = cli_main(["guard", "dry-run", "--ledger", str(AMAURY), "--cap", "not-a-cap", "--out", str(out)])
    assert rc == 1
    assert not out.exists()


def test_cli_requires_model_id_alongside_model_note(tmp_path):
    out = tmp_path / "report.html"
    rc = cli_main(
        ["guard", "dry-run", "--ledger", str(AMAURY), "--model-note", "a quote", "--out", str(out)]
    )
    assert rc == 1
    assert not out.exists()


def test_cli_ledger_directory_binding_reads_a_real_store(tmp_path):
    """The real-deployment path: --ledger pointed at a LedgerStore directory,
    not a loose JSONL file."""
    from capsule_ledger.ledger import LedgerStore

    store_dir = tmp_path / "store"
    store = LedgerStore(store_dir)
    n = store.import_jsonl(AMAURY)
    store.close()
    assert n == 4

    out = tmp_path / "report.html"
    rc = cli_main(
        [
            "guard",
            "dry-run",
            "--ledger",
            str(store_dir),
            "--since",
            "7d",
            "--cap",
            "money.transfer=10000000",
            "--out",
            str(out),
            "--verify",
        ]
    )
    assert rc == 0
    assert out.exists()
