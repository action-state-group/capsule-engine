# SPDX-License-Identifier: Apache-2.0
"""Countersign pack modules (capsule-engine#160): method freeze and declared count."""
from __future__ import annotations

import json

import pytest

from capsule_engine.cli.main import main
from capsule_engine.countersign import PROFILE_IDS, CountersignPackModule, DisclosureRefused, register_all

FREEZE = "a" * 64


def _rec(kind, ts, cid, refs=()):
    r = {"action_id": f"{kind}-{cid[:4]}", "action_type": kind, "timestamp": ts, "capsule_id": cid}
    if refs:
        r["references"] = [{"type": "agent-action-capsule", "digest_alg": "SHA-256", "digest": d} for d in refs]
    return r


def _bundle(freeze_ts="2026-11-01T00:00:00Z", judged_refs=(FREEZE,), freeze=True, count=True, count_ts="2026-11-01T00:00:01Z"):
    recs = []
    if freeze:
        recs.append(_rec("method_freeze", freeze_ts, FREEZE))
    if count:
        recs.append(_rec("declared_count", count_ts, "b" * 64))
    recs.append(_rec("evaluation-report", "2026-11-02T00:00:00Z", "c" * 64, judged_refs))
    recs.append(_rec("evaluation-report", "2026-11-03T00:00:00Z", "d" * 64, judged_refs))
    return {"records": recs}


def _by_name(results):
    return {r.name: r for r in results}


def test_freeze_sealed_before_window_and_referenced_is_established():
    r = _by_name(CountersignPackModule().check(_bundle(), "asg/outcomes/1"))
    assert r["method freeze"].result == "established"
    assert r["declared count"].result == "not checked"


def test_freeze_sealed_after_window_failed():
    r = _by_name(CountersignPackModule().check(_bundle(freeze_ts="2026-11-02T12:00:00Z"), "asg/outcomes/1"))
    assert r["method freeze"].result == "failed"


def test_judgment_without_freeze_reference_failed():
    r = _by_name(CountersignPackModule().check(_bundle(judged_refs=()), "asg/outcomes/1"))
    assert r["method freeze"].result == "failed"
    assert "2 of 2" in r["method freeze"].detail


def test_absent_records_not_present():
    r = _by_name(CountersignPackModule().check(_bundle(freeze=False, count=False), "asg/outcomes/1"))
    assert r["method freeze"].result == "not present"
    assert r["declared count"].result == "not present"


def test_count_sealed_after_window_is_failed():
    r = _by_name(CountersignPackModule().check(_bundle(count_ts="2026-11-02T06:00:00Z"), "asg/outcomes/1"))
    assert r["declared count"].result == "failed"


def test_explicit_window_start_governs():
    from capsule_engine.countersign.modules import parse_window_start

    m = CountersignPackModule(parse_window_start("2026-10-31T00:00:00Z"))
    assert _by_name(m.check(_bundle(), "asg/outcomes/1"))["method freeze"].result == "failed"


def test_no_window_derivable_is_not_checked():
    r = _by_name(CountersignPackModule().check({"records": [_rec("method_freeze", "2026-11-01T00:00:00Z", FREEZE)]}, "asg/outcomes/1"))
    assert r["method freeze"].result == "not checked"


def test_vocabulary_never_pass_fail_or_coverage():
    for b in (_bundle(), _bundle(judged_refs=()), _bundle(freeze=False, count=False)):
        for r in CountersignPackModule().check(b, "asg/outcomes/1"):
            assert r.result not in ("pass", "fail")
            assert "coverage" not in (r.name + r.detail).lower()


def test_register_all_registers_every_profile_id():
    seen = {}

    class Reg:
        def register(self, pid, mod):
            seen[pid] = mod

    register_all(Reg())
    assert set(seen) == set(PROFILE_IDS)


def test_cli_preflight(tmp_path, capsys):
    f = tmp_path / "b.json"
    f.write_text(json.dumps(_bundle()))
    assert main(["packs", "preflight", "--bundle", str(f), "--profile", "asg/outcomes/1"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert {c["name"]: c["result"] for c in doc["checks"]}["method freeze"] == "established"
    f.write_text(json.dumps(_bundle(judged_refs=())))
    assert main(["packs", "preflight", "--bundle", str(f), "--profile", "asg/outcomes/1"]) == 1
    capsys.readouterr()
    assert main(["packs", "preflight", "--bundle", str(f), "--profile", "nope/v1"]) == 2


def _disclosed(payload, span=(1, 4), tamper=False):
    from agent_action_capsule.bundle import json_digest

    b = _bundle()
    rec = next(r for r in b["records"] if r["action_type"] == "declared_count")
    rec["model_attestation"] = {"compute_attestation": {"agent_input_digest": json_digest({"declared_count": 0} if tamper else payload)}}
    b["disclosures"] = {rec["capsule_id"]: {"agent_input": payload}}
    b["completeness_certificate"] = {"first_seq": span[0], "last_seq": span[1]}
    return b


def _count(b):
    return _by_name(CountersignPackModule().check(b, "asg/outcomes/1"))["declared count"]


def test_disclosed_count_matches_span():
    assert _count(_disclosed({"declared_count": 4})).result == "match"


def test_disclosed_count_differs_from_span():
    assert _count(_disclosed({"declared_count": 3})).result == "mismatch"


def test_disclosure_not_matching_sealed_digest_is_refused():
    with pytest.raises(DisclosureRefused, match="digest"):
        _count(_disclosed({"declared_count": 4}, tamper=True))


def test_disclosed_payload_must_be_closed_shape():
    with pytest.raises(DisclosureRefused):
        _count(_disclosed({"declared_count": 4, "note": "x"}))


def test_duplicate_declared_count_is_failed():
    b = _bundle()
    b["records"].append(_rec("declared_count", "2026-11-01T00:00:02Z", "e" * 64))
    assert _count(b).result == "failed"


def test_cli_preflight_refuses_a_forged_disclosure(tmp_path, capsys):
    f = tmp_path / "b.json"
    f.write_text(json.dumps(_disclosed({"declared_count": 4}, tamper=True)))
    assert main(["packs", "preflight", "--bundle", str(f), "--profile", "asg/outcomes/1"]) == 2
    assert "disclosure refused" in capsys.readouterr().err


def test_disclosed_without_certificate_is_not_checked():
    b = _disclosed({"declared_count": 4})
    del b["completeness_certificate"]
    assert _count(b).result == "not checked"


def test_count_with_no_window_is_not_checked_even_when_disclosed():
    b = _disclosed({"declared_count": 4})
    b["records"] = [r for r in b["records"] if r["action_type"] != "evaluation-report"]
    assert _count(b).result == "not checked"


def test_disclosure_with_no_sealed_digest_is_not_checked():
    b = _disclosed({"declared_count": 4})
    next(r for r in b["records"] if r["action_type"] == "declared_count").pop("model_attestation")
    assert _count(b).result == "not checked"


def test_reference_of_another_type_does_not_cite_the_freeze():
    b = _bundle()
    for r in b["records"]:
        for ref in r.get("references", []):
            ref["type"] = "document"
    assert _by_name(CountersignPackModule().check(b, "asg/outcomes/1"))["method freeze"].result == "failed"


def test_chain_parent_cites_the_freeze():
    b = _bundle(judged_refs=())
    for r in b["records"]:
        if r["action_type"] == "evaluation-report":
            r["chain"] = {"parent_capsule_id": FREEZE}
    assert _by_name(CountersignPackModule().check(b, "asg/outcomes/1"))["method freeze"].result == "established"


def test_module_reads_a_bundle_object_with_records_and_raw():
    raw = _disclosed({"declared_count": 4})

    class Obj:
        records = raw["records"]

    Obj.raw = raw
    assert _count(Obj()).result == "match"


def test_cli_preflight_rejects_a_non_bundle(tmp_path, capsys):
    f = tmp_path / "b.json"
    for bad in ("null", "{}", '{"records": "x"}'):
        f.write_text(bad)
        assert main(["packs", "preflight", "--bundle", str(f), "--profile", "asg/outcomes/1"]) == 2


def test_unreadable_timestamp_and_zero_judgments_are_not_checked():
    b = _bundle()
    next(r for r in b["records"] if r["action_type"] == "method_freeze")["timestamp"] = "garbage"
    assert _by_name(CountersignPackModule().check(b, "asg/outcomes/1"))["method freeze"].result == "not checked"
    from capsule_engine.countersign.modules import parse_window_start

    only = {"records": [_rec("method_freeze", "2026-11-01T00:00:00Z", FREEZE)]}
    r = CountersignPackModule(parse_window_start("2026-11-02T00:00:00Z")).check(only, "asg/outcomes/1")
    assert _by_name(r)["method freeze"].result == "not checked"


def test_malformed_second_system_digest_is_refused():
    with pytest.raises(DisclosureRefused):
        _count(_disclosed({"declared_count": 4, "second_system_digest": "x"}))
    with pytest.raises(DisclosureRefused):
        _count(_disclosed({"declared_count": 4, "second_system_digest": None}))
    assert _count(_disclosed({"declared_count": 4, "second_system_digest": "a" * 64})).result == "match"


def test_honest_declaration_counts_every_record_in_the_range_setup_included():
    # 2 judgments + the freeze + the declaration itself = 4 records in seq 1..4
    assert _count(_disclosed({"declared_count": 4}, span=(1, 4))).result == "match"
    # a producer who declares only the 2 judgments reads mismatch
    assert _count(_disclosed({"declared_count": 2}, span=(1, 4))).result == "mismatch"


def test_non_object_disclosure_is_refused_not_crashed():
    from agent_action_capsule.bundle import json_digest

    b = _disclosed({"declared_count": 4})
    rec = next(r for r in b["records"] if r["action_type"] == "declared_count")
    rec["model_attestation"]["compute_attestation"]["agent_input_digest"] = json_digest("x")
    b["disclosures"][rec["capsule_id"]]["agent_input"] = "x"
    with pytest.raises(DisclosureRefused):
        _count(b)
