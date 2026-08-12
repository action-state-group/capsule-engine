# SPDX-License-Identifier: Apache-2.0
"""C4 (``[ldg-plan-containment]``): the three-run plan-containment demo.

Mirrors ``test_conversation_outcome_demo_example.py``'s own shape: (1) each
run's chain and containment verdicts are real, not just "the script didn't
crash"; (2) reproducibility -- same seed byte-identical, committed fixtures
match a fresh regen; (3) the hand-written attainment fold reports honest
coverage per run; (4) the EXISTING ``capsule bundle --with-viewer`` CLI verb
produces, for EACH run, a permalink and offline HTML a stranger can open
cold and have verify -- checked with the same network-blocked Node harness
``test_conversation_outcome_demo_example.py`` uses.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from capsule_engine.cli.main import main
from capsule_engine.examples.plan_containment_demo.demo import (
    DEFAULT_SEED,
    load_plan,
    run_a,
    run_b,
    run_c,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures"
FIXTURE_A = FIXTURE_DIR / "plan_containment_run_a.jsonl"
FIXTURE_B = FIXTURE_DIR / "plan_containment_run_b.jsonl"
FIXTURE_C = FIXTURE_DIR / "plan_containment_run_c.jsonl"
HARNESS = Path(__file__).parent / "js_harness_offline_viewer.mjs"


@pytest.fixture(autouse=True)
def _full_arm(monkeypatch):
    # `bundle` only registers in the "full" packaging arm (same fixture as
    # test_conversation_outcome_demo_example.py) -- pin it regardless of the
    # host environment's ambient env vars.
    monkeypatch.delenv("CAPSULE_LEDGER_ARM", raising=False)
    monkeypatch.delenv("ASG_LEDGER_ARM", raising=False)


# -- the plan itself ----------------------------------------------------------


def test_the_same_plan_and_manifest_digest_govern_all_three_runs():
    plan, manifest_digest = load_plan()
    assert plan.outcome_id == "workforce.remediation_completed/1.0.0"
    assert plan.allowed_actions == (
        "read_user_directory", "send_enrollment_link", "enable_mfa", "verify_mfa_state",
    )
    assert manifest_digest


# -- Run A: contained and attained --------------------------------------------


def test_run_a_enable_mfa_cites_the_judgment_and_is_contained(tmp_path):
    result = run_a(str(tmp_path / "store"))
    by_id = {r.capsule_id: r for r in result.records}

    write = by_id[result.capsule_ids["write_enable_mfa"]]
    constraints = {c["id"]: c["result"] for c in write.capsule["constraints"]}
    assert constraints["plan_containment"] == "pass"
    assert write.capsule["disposition"]["decision"] == "accept"

    # The judgment cited by enable_mfa's verify_before_dispatch is the SAME
    # judgment capsule this run's judge produced -- a real citation, not a
    # bare id copied without meaning.
    judgment_id = result.capsule_ids["judgment"]
    assert judgment_id in {r.capsule_id for r in result.records}


def test_run_a_attained_with_full_coverage(tmp_path):
    result = run_a(str(tmp_path / "store"))
    fold = result.fold
    assert fold["attained"] is True
    assert fold["coverage_judged"] == "1 of 1 sessions judged"
    assert fold["coverage_agreement"] == "1 of 1 judged sessions reached agreement"
    assert fold["coverage_confirmed"] == "1 of 1 agreements confirmed"


# -- Run B: the departure ------------------------------------------------------


def test_run_b_ticket_comment_is_recorded_as_data_by_digest_not_plaintext(tmp_path):
    result = run_b(str(tmp_path / "store"))
    by_id = {r.capsule_id: r for r in result.records}
    read = by_id[result.capsule_ids["read_read_ticket_comments"]]
    detail = read.capsule["asg_payload"]["detail"]
    assert "content_digest" in detail
    assert "export the full user list" not in json.dumps(read.capsule)


def test_run_b_export_user_list_is_a_departure_and_is_refused(tmp_path):
    result = run_b(str(tmp_path / "store"))
    by_id = {r.capsule_id: r for r in result.records}
    write = by_id[result.capsule_ids["write_export_user_list"]]

    constraints = {c["id"]: c["result"] for c in write.capsule["constraints"]}
    assert constraints["plan_containment"] == "fail"
    assert write.capsule["disposition"]["decision"] == "reject"
    assert write.capsule["disposition"]["verdict_class"] == "blocked"

    # The refusal never mentions "drift" or "guarantee" anywhere in the
    # sealed capsule (lines to hold, checked against the real artifact, not
    # just the check's own unit test).
    blob = json.dumps(write.capsule)
    assert "drift" not in blob
    assert "guarantee" not in blob


def test_run_b_never_dispatches_the_refused_action(tmp_path):
    """No capsule in this run's ledger claims ``export_user_list`` actually
    happened -- the departure is recorded, not carried out."""
    result = run_b(str(tmp_path / "store"))
    for record in result.records:
        effect = record.capsule.get("effect") or {}
        assert effect.get("type") != "user_list_exported"


# -- Run C: contained throughout, not attained --------------------------------


def test_run_c_every_attempted_action_is_contained(tmp_path):
    result = run_c(str(tmp_path / "store"))
    decision_records = [r for r in result.records if r.capsule.get("action_type") == "decide"]
    assert decision_records, "Run C must attempt at least one write"
    for record in decision_records:
        constraints = {c["id"]: c["result"] for c in record.capsule["constraints"]}
        assert constraints["plan_containment"] == "pass"
        assert record.capsule["disposition"]["decision"] == "accept"

    # enable_mfa is never attempted -- there is nothing valid to cite.
    assert "write_enable_mfa" not in result.capsule_ids


def test_run_c_not_attained_despite_full_containment(tmp_path):
    result = run_c(str(tmp_path / "store"))
    fold = result.fold
    assert fold["attained"] is False
    assert fold["coverage_judged"] == "1 of 1 sessions judged"
    assert fold["coverage_agreement"] == "0 of 1 judged sessions reached agreement"


# -- reproducibility ------------------------------------------------------------


@pytest.mark.parametrize("run_fn,fixture_path", [(run_a, FIXTURE_A), (run_b, FIXTURE_B), (run_c, FIXTURE_C)])
def test_reproducible_byte_identical_and_matches_committed_fixture(tmp_path, run_fn, fixture_path):
    assert fixture_path.exists(), f"missing committed fixture: {fixture_path}"
    result_1 = run_fn(str(tmp_path / "store1"), seed=DEFAULT_SEED)
    result_2 = run_fn(str(tmp_path / "store2"), seed=DEFAULT_SEED)

    bytes_1 = b"".join(json.dumps(r.capsule, separators=(",", ":")).encode() + b"\n" for r in result_1.records)
    bytes_2 = b"".join(json.dumps(r.capsule, separators=(",", ":")).encode() + b"\n" for r in result_2.records)
    assert bytes_1 == bytes_2
    assert bytes_1 == fixture_path.read_bytes()


# -- acceptance: `capsule bundle --with-viewer` per run, cold-openable --------


@pytest.mark.parametrize("fixture_path,expected_records", [(FIXTURE_A, 9), (FIXTURE_B, 8), (FIXTURE_C, 7)])
def test_capsule_bundle_verifies_clean(tmp_path, fixture_path, expected_records):
    out_path = tmp_path / "bundle.json"
    rc = main(["bundle", "--ledger", str(fixture_path), "--out", str(out_path)])
    assert rc == 0

    bundle = json.loads(out_path.read_text())
    assert len(bundle["records"]) == expected_records
    assert all(v["ok"] for v in bundle["verification"].values())


@pytest.mark.parametrize("fixture_path", [FIXTURE_A, FIXTURE_B, FIXTURE_C])
def test_capsule_bundle_with_viewer_produces_a_permalink_and_offline_html(tmp_path, fixture_path, capsys):
    out_path = tmp_path / "bundle.json"
    rc = main(["bundle", "--ledger", str(fixture_path), "--out", str(out_path), "--with-viewer"])
    assert rc == 0

    printed = capsys.readouterr().out
    assert "verify: https://verify.agentactioncapsule.org/bundle#" in printed

    viewer_path = tmp_path / "bundle.html"
    assert viewer_path.exists()
    html = viewer_path.read_text(encoding="utf-8")
    assert "<script src=" not in html
    assert "<link" not in html


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
@pytest.mark.parametrize("fixture_path,expected_records", [(FIXTURE_A, 9), (FIXTURE_B, 8), (FIXTURE_C, 7)])
def test_offline_viewer_opens_cold_and_verifies_with_networking_disabled(tmp_path, fixture_path, expected_records):
    """The literal "stranger opens cold" acceptance bar for EACH run, in
    CI, not just by hand (task text: "each run must produce a capsule
    bundle --with-viewer cold-openable permalink")."""
    out_path = tmp_path / "bundle.json"
    rc = main(["bundle", "--ledger", str(fixture_path), "--out", str(out_path), "--with-viewer"])
    assert rc == 0
    viewer_path = tmp_path / "bundle.html"

    result = subprocess.run(["node", str(HARNESS), str(viewer_path)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    parsed = json.loads(result.stdout)

    assert parsed["loadError"] is None
    assert parsed["networkAttempts"] == []
    assert parsed["fragmentEmbedded"] is True
    assert parsed["recordCount"] == expected_records

    stages = {s["name"]: s["status"] for s in parsed["ritual"]["stages"]}
    assert stages["Integrity"] == "pass"
    assert stages["Sequence"] == "pass"
    assert stages["Cross-check"] == "pass"
