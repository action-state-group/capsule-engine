# SPDX-License-Identifier: Apache-2.0
"""C5 (``[ldg-plan-containment]``): the two-lane demo page renders, embeds a
real in-browser-recomputable refusal digest, and holds the vocabulary lines
(never "guarantee"/"drift", no competitor names)."""
from __future__ import annotations

import shutil
import subprocess

import pytest

from capsule_engine.examples.plan_containment_demo.demo import load_plan, run_a, run_b, run_c
from capsule_engine.examples.plan_containment_demo.page import _JCS_AND_SHA256_JS, render_demo_page


def _all_results(tmp_path):
    return {
        "run-a": run_a(str(tmp_path / "a")),
        "run-b": run_b(str(tmp_path / "b")),
        "run-c": run_c(str(tmp_path / "c")),
    }


def test_page_renders_all_three_runs(tmp_path):
    plan, manifest_digest = load_plan()
    results = _all_results(tmp_path)
    page = render_demo_page(plan, manifest_digest, results)

    assert "Run A -- the good path" in page
    assert "Run B -- the departure" in page
    assert "Run C -- the honest one" in page
    assert plan.definition_digest() in page
    assert manifest_digest in page


def test_page_is_self_contained_no_network(tmp_path):
    plan, manifest_digest = load_plan()
    page = render_demo_page(plan, manifest_digest, _all_results(tmp_path))
    assert "<script src=" not in page
    assert "<link" not in page
    assert "http://" not in page and "https://" not in page


def test_page_embeds_the_refusal_evidence_and_a_recompute_check(tmp_path):
    plan, manifest_digest = load_plan()
    results = _all_results(tmp_path)
    page = render_demo_page(plan, manifest_digest, results)

    b_capsule_id = results["run-b"].capsule_ids["write_export_user_list"]
    b_record = next(r for r in results["run-b"].records if r.capsule_id == b_capsule_id)
    evidence_digest = next(c["evidence_digest"] for c in b_record.capsule["constraints"] if c["id"] == "plan_containment")

    assert "export_user_list" in page
    assert evidence_digest in page
    assert "jsonDigest" in page
    assert "recheck-run-b" in page


def test_page_holds_the_vocabulary_lines(tmp_path):
    plan, manifest_digest = load_plan()
    page = render_demo_page(plan, manifest_digest, _all_results(tmp_path))

    assert "guarantee" not in page.lower()
    assert "drift" not in page.lower()
    # No competitor naming anywhere on this public-repo surface.
    assert "varonis" not in page.lower()


def test_run_a_shows_enable_mfa_in_plan_and_attained(tmp_path):
    plan, manifest_digest = load_plan()
    results = _all_results(tmp_path)
    page = render_demo_page(plan, manifest_digest, results)

    a_start = page.index('id="run-a"')
    a_end = page.index('id="run-b"')
    section = page[a_start:a_end]
    assert "enable_mfa -- step 3 of plan" in section
    assert "attained</strong>" in section and "not attained</strong>" not in section


def test_run_c_shows_full_containment_but_not_attained(tmp_path):
    plan, manifest_digest = load_plan()
    results = _all_results(tmp_path)
    page = render_demo_page(plan, manifest_digest, results)

    c_start = page.index('id="run-c"')
    section = page[c_start:]
    assert "not attained</strong>" in section
    assert "badge fail" not in section  # every containment check in Run C passes


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_in_browser_recompute_actually_matches_via_node(tmp_path):
    """Not a string-presence check: this actually RUNS the embedded
    recompute JS (the same jsonDigest() the page calls in a real browser)
    against Run B's real evidence and asserts it lands on the digest the
    sealed capsule actually committed to. Regression coverage for a real
    bug caught by opening this page in a browser: the JS recompute was
    hashing the evidence object directly, while ``json_digest`` on the
    Python side hashes ``JCS(normalize(v))`` -- normalize() drops the
    null-valued ``step_index`` field first. Recomputing without that
    normalization step silently produced a MISMATCH on every real refusal
    evidence object (any departure with no step index), i.e. it would have
    made this page assert "the evidence doesn't match" about honestly
    unmodified data -- the opposite of what the recompute trick exists to
    prove."""
    results = {"run-b": run_b(str(tmp_path / "b"))}
    result = results["run-b"]
    capsule_id = result.capsule_ids["write_export_user_list"]
    record = next(r for r in result.records if r.capsule_id == capsule_id)
    evidence = result.constraint_evidence["write_export_user_list"]
    want_digest = next(c["evidence_digest"] for c in record.capsule["constraints"] if c["id"] == "plan_containment")

    assert evidence.get("step_index") is None, "this test's whole point is a null-valued field"

    script = f"""
    {_JCS_AND_SHA256_JS}
    var evidence = {__import__("json").dumps(evidence)};
    console.log(jsonDigest(evidence));
    """
    script_path = tmp_path / "recompute.mjs"
    script_path.write_text(script, encoding="utf-8")
    result_proc = subprocess.run(["node", str(script_path)], capture_output=True, text=True, timeout=10)
    assert result_proc.returncode == 0, result_proc.stderr
    got_digest = result_proc.stdout.strip()

    assert got_digest == want_digest
