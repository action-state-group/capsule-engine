# SPDX-License-Identifier: Apache-2.0
"""A real two-deal bundle from capsulectl v0.1.0-rc13, replayed under everyday 0.3.3.

``fixtures/real-two-deal/`` holds what capsulectl wrote, byte for byte: two
deals on one throwaway profile, each a purchase of the same item from the same
synthetic merchant, checked and paid for the same amount (``build.sh`` and
``README.md`` there). Each deal is exported twice: the user's own copy
(``capsulectl bundle --deal``) and the counterparty's shared copy
(``capsulectl disclose --deal --share counterparty``). Every check is followed
by its ``counterparty_profile`` companion.

The engine replays the two own copies, deal 1 then deal 2, under the installed
pack. ``expected_decisions.json`` is that replay's decision for every record,
in sorted canonical JSON, compared byte for byte here and handed to the Go
plugin's differential. Regenerate it with
``python -m tests.test_real_two_deal_bundle``.

Two of the expected results do not hold, and each is pinned with a strict
xfail rather than written into the expectation:

- r06 FAILS on deal 2's check. The merchant reads as new because a replay has
  no accepted earlier act: deal 1's check escalates, and no sealed approval or
  action is replayed as its acceptance.
- The companion records are DENIED, not read as not applicable. Under the
  pack, ``action_class_gate`` fails closed on a record with no action class,
  as it does on every record that states no act.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import tempfile
from functools import cache
from pathlib import Path
from typing import TypedDict

import pytest
from agent_action_capsule import json_digest

import capsule_engine
from capsule_engine.cli.main import main as cli_main
from capsule_engine.guards.capsule import DENY, ESCALATE, LOCAL_ONLY_TARGET_PREFIX
from capsule_engine.guards.checks import RUNNABLE_CHECKS
from capsule_engine.packs import install_pack, load_pack_dir
from capsule_engine.packs.obligation_results import obligation_results
from capsule_engine.report.replay import ReplayResult, load_disclosed, load_records, replay

FIXTURE = Path(__file__).parent / "fixtures" / "real-two-deal"
OWN = (FIXTURE / "deal-1.bundle.json", FIXTURE / "deal-2.bundle.json")
SHARES = (FIXTURE / "deal-1.counterparty.bundle.json", FIXTURE / "deal-2.counterparty.bundle.json")
EXPECTED = FIXTURE / "expected_decisions.json"
FIXTURE_SHA256 = {
    "deal-1.bundle.json": "8eaed7e3e6e762d2528c17ee6cf2b1186f6f301c3340f8e91fff1bd0f85f38a3",
    "deal-1.counterparty.bundle.json": "b9576f2cfb1a2d61469ad5f94daa1057788bfb4dae76efff041eb7cd24e200f0",
    "deal-2.bundle.json": "fe97fde684e8d38a07f51157b8f73fcf609c511c8f29dd50aa6e5025d3797e2b",
    "deal-2.counterparty.bundle.json": "181079ccc08f27eb2cd39af48f5913050d34bfb9b961d7cf7e577eb23d997685",
}
PACK = load_pack_dir(Path(capsule_engine.__file__).parent / "packs" / "catalog" / "everyday")
PACK_ID = "asg/everyday/0.3.3"
PACK_DIGEST = "d153219b9b5f7ad8eb4805eb718aab81a5dfa29a6a1d977301559fc167268752"
PRODUCER = {"commit": "7eb05ac1c277f4a7dad1ece8b1a1aec2df7eac11", "name": "capsulectl", "version": "v0.1.0-rc13"}
R06 = "r06-new-merchant"
R27 = "r27-no-commitment-beyond-task-bounds"
# What the inputs name in clear. A sealed record carries the merchant only as
# fingerprints; the user's own copy says it in its report, and nothing else does.
MERCHANT_IN_CLEAR = ("Example Merchant", "example-merchant.test")
EVERY_CLEAR_STRING = (*MERCHANT_IN_CLEAR, "ORDER-SYN-0001", "ORDER-SYN-0002")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+")
HANDLE = re.compile(r"(^|[\s\"'(])@[A-Za-z0-9_]{2,}")
SOCIAL_URL = re.compile(r"(twitter\.com|x\.com|github\.com|linkedin\.com|t\.me)/[A-Za-z0-9_.-]+", re.IGNORECASE)
LOCAL_PATH = re.compile(r"/(Users|home|private)/[A-Za-z0-9_.-]+")


class Decision(TypedDict):
    capsule_id: str
    record_type: str | None
    verb: str
    action_class: str | None
    target: str | None
    ignored_inputs: list[str]
    outcome: str
    failing_checks: list[str]
    rules: dict[str, str]


class DecisionsDocument(TypedDict):
    pack: dict[str, str]
    producer: dict[str, str]
    replayed: list[str]
    decisions: list[Decision]


# Reads the vendored bundles: the test's decoding boundary.
def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _disclosed_records(path: Path) -> dict[str, dict]:
    """Each disclosed deal record's ``x-deal-v0`` block, by capsule id."""
    found = {}
    for capsule_id, members in _json(path)["disclosures"].items():
        block = (members.get("agent_input") or {}).get("x-deal-v0")
        if isinstance(block, dict):
            found[capsule_id] = block
    return found


def _of_type(path: Path, record_type: str) -> dict[str, dict]:
    return {k: v for k, v in _disclosed_records(path).items() if v.get("record_type") == record_type}


@cache
def _replayed() -> ReplayResult:
    with tempfile.TemporaryDirectory() as tmp:
        installed = install_pack(PACK, project_dir=Path(tmp) / "replay-project", mode="observe")
        resolved = installed.resolved
        return replay(
            load_records(OWN),
            caps_fold=resolved.caps_fold(),
            caps_minor=resolved.caps_minor(),
            per_action_minor=resolved.per_action_minor(),
            per_action_reads=resolved.per_action_reads(),
            manifest_digest=resolved.manifest_digest,
            disclosed=load_disclosed(OWN),
            wickets=resolved.configured_wickets(RUNNABLE_CHECKS),
            pack=installed.pack,
        )


def _record_type(capsule_id: str) -> str | None:
    for path in OWN:
        block = _disclosed_records(path).get(capsule_id)
        if block is not None:
            return block.get("record_type")
    return None


def decisions_document() -> DecisionsDocument:
    """The replay's decision for every record of the two own copies, in replay order."""
    decisions: list[Decision] = []
    for sourced in _replayed().decisions:
        rules = {r.obligation_id: r.result for r in obligation_results(PACK, sourced.decision.constraints)}
        decisions.append({
            "capsule_id": sourced.record["capsule_id"],
            "record_type": _record_type(sourced.record["capsule_id"]),
            "verb": sourced.action.verb,
            "action_class": sourced.action.action_class,
            "target": sourced.action.target,
            "ignored_inputs": list(sourced.action.ignored_inputs),
            "outcome": sourced.decision.outcome,
            "failing_checks": sorted(c.id for c in sourced.decision.constraints if c.result == "fail"),
            "rules": rules,
        })
    return {
        "pack": {"pack_id": PACK.pack_id, "definition_digest": PACK.definition_digest()},
        "producer": PRODUCER,
        "replayed": [p.name for p in OWN],
        "decisions": decisions,
    }


def canonical(document: DecisionsDocument) -> str:
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"


def _decision(capsule_id: str) -> Decision:
    (found,) = [d for d in decisions_document()["decisions"] if d["capsule_id"] == capsule_id]
    return found


def _checks(n: int) -> list[str]:
    return list(_of_type(OWN[n - 1], "check"))


def _profile_payees() -> set[str]:
    return {b["counterparty_profile"]["ids"]["payee"] for p in OWN for b in _of_type(p, "counterparty_profile").values()}


# -- the fixture is capsulectl's bytes ---------------------------------------------


def test_the_fixture_is_the_bytes_capsulectl_wrote():
    for name, digest in FIXTURE_SHA256.items():
        assert hashlib.sha256((FIXTURE / name).read_bytes()).hexdigest() == digest, name


def test_every_deal_record_was_sealed_by_capsulectl_rc13():
    for path in OWN:
        for block in _disclosed_records(path).values():
            assert block["producer"] == PRODUCER


def test_the_pack_is_everyday_0_3_3_at_its_digest():
    assert PACK.pack_id == PACK_ID
    assert PACK.definition_digest() == PACK_DIGEST


def test_the_two_deals_are_one_profile_paying_one_merchant():
    deal_ids = {block["deal_id"] for p in OWN for block in _disclosed_records(p).values()}
    assert len(deal_ids) == 2
    assert len(_profile_payees()) == 1
    per_deal = {b["counterparty"]["ids"]["payee"] for p in OWN for b in _of_type(p, "check").values()}
    assert len(per_deal) == 2


# -- the replay ----------------------------------------------------------------


def test_the_replay_matches_expected_decisions_byte_for_byte():
    assert canonical(decisions_document()) == EXPECTED.read_text(encoding="utf-8")


def test_the_replay_is_deterministic():
    first = canonical(decisions_document())
    _replayed.cache_clear()
    assert canonical(decisions_document()) == first


def test_each_check_is_keyed_on_its_companions_profile_fingerprint():
    (payee,) = _profile_payees()
    for n in (1, 2):
        (check,) = _checks(n)
        decision = _decision(check)
        assert decision["target"] == LOCAL_ONLY_TARGET_PREFIX + payee
        assert decision["ignored_inputs"] == []


def test_the_identical_act_in_deal_2_escalates_on_r27():
    (check,) = _checks(2)
    decision = _decision(check)
    assert decision["outcome"] == ESCALATE
    assert decision["rules"][R27] == "fail"
    (sourced,) = [s for s in _replayed().decisions if s.record["capsule_id"] == check]
    (dedupe,) = [c for c in sourced.decision.constraints if c.id == "dedupe"]
    assert dedupe.evidence["repeat"] == "other_deal"


def test_deal_1s_check_repeats_nothing():
    (check,) = _checks(1)
    assert _decision(check)["rules"][R27] == "pass"


@pytest.mark.xfail(strict=True, reason="r06 FAILS on deal 2: the replay has no accepted earlier act; "
                   "deal 1's check escalates and no sealed approval or action is replayed as its acceptance")
def test_r06_recognises_the_merchant_on_deal_2():
    (check,) = _checks(2)
    assert _decision(check)["rules"][R06] == "pass"


def test_r06_reads_the_profile_fingerprint_and_finds_no_accepted_act():
    (payee,) = _profile_payees()
    (check,) = _checks(2)
    (sourced,) = [s for s in _replayed().decisions if s.record["capsule_id"] == check]
    (seen,) = [c for c in sourced.decision.constraints if c.id == "counterparty_seen_before"]
    assert seen.evidence["fold_key"]["value"] == LOCAL_ONLY_TARGET_PREFIX + payee
    assert seen.evidence["prior_count"] == 0


@pytest.mark.xfail(strict=True, reason="the companion is DENIED: action_class_gate fails closed on a record "
                   "with no action class under the pack")
def test_the_companions_read_not_applicable():
    for path in OWN:
        for capsule_id in _of_type(path, "counterparty_profile"):
            decision = _decision(capsule_id)
            assert decision["outcome"] != DENY
            assert set(decision["rules"].values()) <= {"pass", "n/a"}


def test_the_companions_state_no_act():
    for path in OWN:
        for capsule_id in _of_type(path, "counterparty_profile"):
            decision = _decision(capsule_id)
            assert decision["target"] is None
            assert decision["action_class"] is None
            assert decision["failing_checks"] == ["action_class_gate"]


# -- the counterparty shares -----------------------------------------------------------


def test_no_share_discloses_a_companion_or_carries_its_fingerprint():
    for share in SHARES:
        text = share.read_text(encoding="utf-8")
        assert _of_type(share, "counterparty_profile") == {}
        for payee in _profile_payees():
            assert payee not in text


def test_a_share_lists_the_companion_only_as_a_withheld_step():
    """What capsulectl's share does carry: the companion's sealed capsule (its
    digests and signature, no record) and one withheld step naming its kind."""
    for own, share in zip(OWN, SHARES, strict=True):
        (companion,) = _of_type(own, "counterparty_profile")
        shared = _json(share)
        assert companion in {r["capsule_id"] for r in shared["records"]}
        assert companion not in shared["disclosures"]
        text = share.read_text(encoding="utf-8")
        assert text.count("counterparty_profile") == 1
        assert re.search(r'"capsule_id":"' + companion + r'","kind":"counterparty_profile","line":"[^"]*","n":\d+,"withheld":true', text)


def test_the_engine_refuses_to_share_a_profile_keyed_decision(tmp_path, capsys):
    out = tmp_path / "report.html"
    rc = cli_main(["guard", "dry-run", *(arg for p in OWN for arg in ("--ledger", str(p))), "--since", "all",
                   "--out", str(out), "--share", "--no-manifest"])
    printed = capsys.readouterr()
    assert rc == 1
    assert not out.exists()
    assert "target" in printed.err
    for payee in _profile_payees():
        assert payee not in printed.out + printed.err


def test_the_engine_refuses_to_bundle_a_profile_keyed_decision(tmp_path, capsys):
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text("".join(json.dumps(s.decision.capsule) + "\n" for s in _replayed().decisions))
    out = tmp_path / "bundle.json"
    rc = cli_main(["bundle", "--ledger", str(ledger), "--out", str(out)])
    printed = capsys.readouterr()
    assert rc == 2
    assert not out.exists()
    assert "2 record(s)" in printed.err
    for payee in _profile_payees():
        assert payee not in printed.out + printed.err


# -- never enters ----------------------------------------------------------------


def _fixture_files() -> list[Path]:
    return sorted(p for p in FIXTURE.rglob("*") if p.is_file())


def test_no_email_handle_profile_url_or_local_path_anywhere_in_the_fixture():
    files = _fixture_files()
    assert len(files) >= 10
    for path in files:
        text = path.read_text(encoding="utf-8")
        for pattern in (EMAIL, HANDLE, SOCIAL_URL, LOCAL_PATH):
            assert pattern.search(text) is None, f"{path.name}: {pattern.pattern}"
        if path.suffix == ".json":
            assert "@" not in text, path.name


def test_the_operator_is_the_synthetic_profile():
    for path in (*OWN, *SHARES):
        assert {r["operator"] for r in _json(path)["records"]} == {"synthetic-two-deal"}


def test_no_share_names_the_merchant_or_the_orders_in_clear():
    for share in SHARES:
        text = share.read_text(encoding="utf-8")
        for clear in EVERY_CLEAR_STRING:
            assert clear.lower() not in text.lower(), (share.name, clear)


def _paths_holding(value: object, needle: str, path: tuple[str, ...] = ()) -> list[tuple[str, ...]]:
    if isinstance(value, dict):
        return [p for k, v in value.items() for p in _paths_holding(v, needle, (*path, k))]
    if isinstance(value, list):
        return [p for i, v in enumerate(value) for p in _paths_holding(v, needle, (*path, str(i)))]
    return [path] if isinstance(value, str) and needle.lower() in value.lower() else []


def test_the_own_copy_names_the_merchant_only_in_the_users_report():
    """A deal record seals the merchant as fingerprints; only the report capsule
    of the user's own copy carries the user's words and what was done."""
    for own in OWN:
        bundle = _json(own)
        for clear in EVERY_CLEAR_STRING:
            for path in _paths_holding(bundle, clear):
                assert path[0] == "disclosures" and path[2:4] == ("agent_input", "report"), (own.name, clear, path)
        for block in _disclosed_records(own).values():
            for clear in EVERY_CLEAR_STRING:
                assert _paths_holding(block, clear) == []


def test_every_check_seals_the_merchant_as_fingerprints():
    for own in OWN:
        for block in _of_type(own, "check").values():
            assert block["counterparty"]["fp_alg"] == "hmac-sha256-deal-key"
            assert all(re.fullmatch(r"[0-9a-f]{64}", v) for v in block["counterparty"]["ids"].values())


def test_the_companion_names_its_check_by_digest():
    for own in OWN:
        (check_id,) = _of_type(own, "check")
        check_record = _json(own)["disclosures"][check_id]["agent_input"]
        (companion,) = _of_type(own, "counterparty_profile").values()
        (ref,) = companion["refs"]
        assert ref["rel"] == "about"
        assert json_digest(check_record) in json.dumps(ref)


if __name__ == "__main__":
    EXPECTED.write_text(canonical(decisions_document()), encoding="utf-8")
    sys.stdout.write(f"wrote {EXPECTED}\n")
