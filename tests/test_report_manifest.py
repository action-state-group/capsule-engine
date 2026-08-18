# SPDX-License-Identifier: Apache-2.0
"""`capsule guard dry-run` reports "evaluated under manifest <digest>" (task
acceptance point 4), and ``build_dry_run_report``/``replay`` thread a
resolved manifest's digest through to the real ``GuardEngine`` that
produces every row's capsule."""
from __future__ import annotations

from pathlib import Path

from capsule_ledger.cli.main import main
from capsule_ledger.report import build_dry_run_report

FIXTURES = Path(__file__).parent / "fixtures"
NANDA = FIXTURES / "nanda_transaction_ledger.jsonl"

EXPECTED_DEFAULT_DIGEST = "0e99f3ee3a6ebf3ee93aa464f27e8fcd1a401ccc45460eb267efde327f5c218c"


def test_report_manifest_digest_field_is_none_by_default(caps_fold):
    report = build_dry_run_report([str(NANDA)], caps_fold=caps_fold, since="7d")
    assert report.manifest_digest is None


def test_report_manifest_digest_is_populated_when_passed_through(caps_fold, resolved_manifest):
    report = build_dry_run_report(
        [str(NANDA)], caps_fold=caps_fold, since="7d", manifest_digest=resolved_manifest.manifest_digest
    )
    assert report.manifest_digest == resolved_manifest.manifest_digest

    # And it's not just a schema field: the underlying capsules the replay
    # actually produced carry it too.
    populated = [row.capsule for _, row in report.held_rows if "manifest_digest" in row.capsule.get("asg_payload", {})]
    assert populated, "expected at least one held row's capsule to cite the manifest digest"
    assert all(c["asg_payload"]["manifest_digest"] == resolved_manifest.manifest_digest for c in populated)


def test_cli_guard_dry_run_prints_evaluated_under_manifest(tmp_path, capsys):
    out_path = tmp_path / "report.html"
    rc = main(["guard", "dry-run", "--ledger", str(NANDA), "--since", "7d", "--out", str(out_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert f"evaluated under manifest {EXPECTED_DEFAULT_DIGEST}" in out


def test_cli_guard_dry_run_no_manifest_flag_skips_the_line(tmp_path, capsys):
    out_path = tmp_path / "report.html"
    rc = main(["guard", "dry-run", "--ledger", str(NANDA), "--since", "7d", "--out", str(out_path), "--no-manifest"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "evaluated under manifest" not in out
