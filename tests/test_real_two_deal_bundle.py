# SPDX-License-Identifier: Apache-2.0
"""A real two-deal bundle from capsulectl, replayed under everyday 0.3.4.

``fixtures/real-two-deal/`` holds what capsulectl wrote, byte for byte: two
deals on one throwaway profile, each a purchase of the same item from the same
synthetic merchant, checked and paid for the same amount (``build.sh`` and
``README.md`` there). Each deal is exported twice: the user's own copy
(``capsulectl bundle --deal``) and the counterparty's shared copy
(``capsulectl disclose --deal --share counterparty``). Every check is followed
by its ``counterparty_profile`` companion.

The engine replays the two own copies, deal 1 then deal 2, under the installed
pack. Only a check states an act, so only the two checks get a decision: the
companions, the deal's other records and the reports get none, and no rule
fires on them. Deal 1's check asks (a first-time merchant), and its sealed
approval and executed action carry it out, so deal 2's check reads the
merchant as seen. ``expected_decisions.json`` is that replay's decision for
every check, in sorted canonical JSON, compared byte for byte here and handed
to the Go plugin's differential. Regenerate it with
``python -m tests.test_real_two_deal_bundle``.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import tempfile
from collections.abc import Callable, Mapping
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
from capsule_engine.packs.schema import PackDefinition
from capsule_engine.report.replay import ReplayResult, load_disclosed, load_records, load_withheld, replay

FIXTURE = Path(__file__).parent / "fixtures" / "real-two-deal"
OWN = (FIXTURE / "deal-1.bundle.json", FIXTURE / "deal-2.bundle.json")
SHARES = (FIXTURE / "deal-1.counterparty.bundle.json", FIXTURE / "deal-2.counterparty.bundle.json")
EXPECTED = FIXTURE / "expected_decisions.json"
FIXTURE_SHA256 = {
    "deal-1.bundle.json": "22ee3f9771d8fad6917a50be8ff93894ad24e0a219c220d9d255bfe66327736d",
    "deal-1.counterparty.bundle.json": "b9121ea5ea42251b25ae329af60f93a9bded09fb12e63044b0c10c7af1e82808",
    "deal-2.bundle.json": "b9a6a26daa75d69f7e608c67abfa93072c2b26680c65660ee8e599cf1a9b7a36",
    "deal-2.counterparty.bundle.json": "810a9d1f4d43f1b55a0db4cd51673e431b5ac169ee3543c293b23963848de298",
}
PACK = load_pack_dir(Path(capsule_engine.__file__).parent / "packs" / "catalog" / "everyday")
FROZEN_0_3_3 = Path(__file__).parent / "fixtures" / "packs" / "everyday-0.3.3"
PACK_ID = "asg/everyday/0.3.4"
PACK_DIGEST = "cd98aaec5acf8cea86f91fc21dd7df6b36a67c7b55340489b66e6ce88b85117b"
PRODUCER = {"commit": "831afeeb9fd76b7196486a9af38ca455b1230791", "name": "capsulectl", "version": "v0.1.0-rc13-6-g831afee"}
TAXONOMY = "6"
R02 = "r02-ordinary-purchase"
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
    return _replay(tuple(load_records(OWN)), load_disclosed(OWN), load_withheld(OWN))


def _replay(records: tuple[dict, ...], disclosed: dict[str, dict], withheld: frozenset[str],
            pack: PackDefinition = PACK) -> ReplayResult:
    with tempfile.TemporaryDirectory() as tmp:
        installed = install_pack(pack, project_dir=Path(tmp) / "replay-project", mode="observe")
        resolved = installed.resolved
        return replay(
            list(records),
            caps_fold=resolved.caps_fold(),
            caps_minor=resolved.caps_minor(),
            per_action_minor=resolved.per_action_minor(),
            per_action_reads=resolved.per_action_reads(),
            manifest_digest=resolved.manifest_digest,
            disclosed=disclosed,
            withheld=withheld,
            wickets=resolved.configured_wickets(RUNNABLE_CHECKS),
            pack=installed.pack,
        )


def _record_type(capsule_id: str) -> str | None:
    for path in OWN:
        block = _disclosed_records(path).get(capsule_id)
        if block is not None:
            return block.get("record_type")
    return None


def decisions_document(result: ReplayResult | None = None) -> DecisionsDocument:
    """The replay's decision for every record of the two own copies that
    gets one (each check), in replay order: of ``result``, by default the
    replay of the fixture as sealed."""
    decisions: list[Decision] = []
    for sourced in (result or _replayed()).decisions:
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


def test_every_deal_record_was_sealed_by_the_pinned_capsulectl():
    for path in OWN:
        for block in _disclosed_records(path).values():
            assert block["producer"] == PRODUCER


def test_every_check_seals_taxonomy_6():
    for path in OWN:
        for capsule_id in _of_type(path, "check"):
            assert _json(path)["disclosures"][capsule_id]["agent_input"]["body"]["taxonomy_version"] == TAXONOMY


def test_the_pack_is_everyday_0_3_4_at_its_digest():
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


def test_deal_1s_check_asks_about_a_first_time_merchant():
    (check,) = _checks(1)
    decision = _decision(check)
    assert decision["outcome"] == ESCALATE
    assert (decision["rules"][R02], decision["rules"][R06]) == ("fail", "fail")


def test_r02_and_r06_recognise_the_merchant_on_deal_2():
    (check,) = _checks(2)
    rules = _decision(check)["rules"]
    assert (rules[R02], rules[R06]) == ("pass", "pass")


def _seen_on_deal_2(result: ReplayResult):
    (check,) = _checks(2)
    (sourced,) = [s for s in result.decisions if s.record["capsule_id"] == check]
    (seen,) = [c for c in sourced.decision.constraints if c.id == "counterparty_seen_before"]
    return seen


def test_r06_counts_deal_1s_carried_out_act_under_the_profile_fingerprint():
    (payee,) = _profile_payees()
    seen = _seen_on_deal_2(_replayed())
    assert seen.evidence["fold_key"]["value"] == LOCAL_ONLY_TARGET_PREFIX + payee
    assert (seen.result, seen.evidence["prior_count"]) == ("pass", 1)


def test_under_0_3_3_deal_2_still_reads_a_first_time_merchant():
    """counterparty_seen_before/2.0.0, which 0.3.3 cites, counts no carried-out
    act: the fix is the new version, and the old one keeps its meaning."""
    result = _replay(tuple(load_records(OWN)), load_disclosed(OWN), load_withheld(OWN), load_pack_dir(FROZEN_0_3_3))
    seen = _seen_on_deal_2(result)
    assert (seen.result, seen.evidence["prior_count"]) == ("fail", 0)


def test_the_carried_out_act_is_never_counted_as_spend():
    """The record the replay writes for deal 1's act is counted by
    counterparty.seen_before/3.0.0 only: deal 2's caps reads no spend."""
    (check,) = _checks(2)
    (sourced,) = [s for s in _replayed().decisions if s.record["capsule_id"] == check]
    (caps,) = [c for c in sourced.decision.constraints if c.id == "caps"]
    assert caps.evidence["weekly_spend_minor"] == 0


# -- the same deals, changed: an act that was not carried out is not seen -------------


Change = Callable[[dict], dict | None]


def _deals_changed(changes: Mapping[str, Change], *, deals: tuple[int, ...] = (1,)) -> ReplayResult:
    """The replay with the records of each ``record_type`` in ``changes``, in
    the numbered ``deals``, passed through its function: a changed record, or
    ``None`` to drop it. Every later record of those deals that names a
    changed one by digest (``prev``, ``refs``) is re-pointed and re-sealed, so
    the chain stays whole. A re-sealed capsule binds its new record by digest;
    its signature no longer verifies, and the replay reads only the binding."""
    disclosed = dict(load_disclosed(OWN))
    changing = {capsule_id for n in deals for capsule_id in _disclosed_records(OWN[n - 1])}
    moved: dict[str, str] = {}
    records = []
    for capsule in load_records(OWN):
        capsule_id = capsule["capsule_id"]
        sealed = disclosed.get(capsule_id)
        if sealed is None or "x-deal-v0" not in sealed or capsule_id not in changing:
            records.append(capsule)
            continue
        text = json.dumps(sealed)
        for old, new in moved.items():
            text = text.replace(old, new)
        changed = json.loads(text)
        change = changes.get(changed["x-deal-v0"]["record_type"])
        if change is not None:
            changed = change(changed)
        if changed is None:
            disclosed.pop(capsule_id)
            continue
        if changed != sealed:
            moved[json_digest(sealed)] = json_digest(changed)
            attestation = {"compute_attestation": {"agent_input_digest": json_digest(changed)}}
            capsule = {**capsule, "model_attestation": attestation}
            disclosed[capsule_id] = changed
        records.append(capsule)
    return _replay(tuple(records), disclosed, load_withheld(OWN))


def _with_body(**fields: object) -> Change:
    return lambda record: {**record, "body": {**record["body"], **fields}}


def _without_body(field: str) -> Change:
    return lambda record: {**record, "body": {k: v for k, v in record["body"].items() if k != field}}


def _unseen(result: ReplayResult) -> bool:
    seen = _seen_on_deal_2(result)
    return (seen.result, seen.evidence["prior_count"]) == ("fail", 0)


def test_the_unchanged_chain_rewritten_is_still_seen():
    """The rewrite itself keeps the chain: changing nothing, deal 2 still
    reads the merchant as seen."""
    seen = _seen_on_deal_2(_deals_changed({}))
    assert (seen.result, seen.evidence["prior_count"]) == ("pass", 1)


def test_an_approval_that_declines_leaves_the_merchant_unseen():
    """Deal 1's approval sealed as declined, its action step still there and
    pointing at it: the chain is whole but says no."""
    assert _unseen(_deals_changed({"approval": _with_body(choice="decline", proceed=False)}))


def test_deal_1s_approval_was_sealed_under_the_users_standing_intent():
    (approval_id,) = _of_type(OWN[0], "approval")
    assert load_disclosed(OWN)[approval_id]["body"]["approver"] == "standing_intent"


def test_an_approval_the_user_gave_counts_the_act_as_seen():
    seen = _seen_on_deal_2(_deals_changed({"approval": _with_body(approver="user")}))
    assert (seen.result, seen.evidence["prior_count"]) == ("pass", 1)


@pytest.mark.parametrize("approver", ["agent_card", "", None, "User", ["user"], {"user": True}])
def test_an_approval_no_user_gave_leaves_the_merchant_unseen(approver):
    """An approval on the agent's own card certifies no one's consent, so the
    act it approves stays unseen, as live counts it; so does any approver the
    producer does not seal for the user or the user's standing intent."""
    assert _unseen(_deals_changed({"approval": _with_body(approver=approver)}))


def test_an_approval_naming_no_approver_leaves_the_merchant_unseen():
    assert _unseen(_deals_changed({"approval": _without_body("approver")}))


def test_an_approved_act_never_executed_leaves_the_merchant_unseen():
    assert _unseen(_deals_changed({"action": lambda record: None}))


def test_a_purchase_the_guard_refused_leaves_the_merchant_unseen():
    """Deal 1's check names a class with no taxonomy row: the gate fails
    closed and the check is refused. Its approval and action step stay,
    re-pointed at it, and still count for nothing."""
    result = _deals_changed({"check": _with_body(action_class="money.unlisted")})
    (check,) = [s for s in result.decisions if s.record["capsule_id"] in _checks(1)]
    assert check.decision.outcome == DENY
    assert check.action.target == _seen_on_deal_2(result).evidence["fold_key"]["value"]
    assert _unseen(result)


def test_an_action_step_its_capsule_did_not_seal_carries_nothing_out():
    (action_id,) = _of_type(OWN[0], "action")
    records = load_records(OWN)
    disclosed = dict(load_disclosed(OWN))
    disclosed[action_id] = {**disclosed[action_id], "note": "not what the capsule sealed"}
    assert _unseen(_replay(tuple(records), disclosed, load_withheld(OWN)))


# -- records that state no act --------------------------------------------------------


def test_only_the_checks_get_a_decision():
    decided = [d["capsule_id"] for d in decisions_document()["decisions"]]
    assert decided == [*_checks(1), *_checks(2)]


def test_every_other_record_is_replayed_without_a_decision():
    """The companions, baselines, verdicts, approvals, action steps and both
    reports of each deal: no rule fires on them, the gate included."""
    undecided = {r["capsule_id"] for r in _replayed().undecided}
    every = {r["capsule_id"] for p in OWN for r in _json(p)["records"]}
    assert undecided == every - {*_checks(1), *_checks(2)}
    kinds = {_record_type(c) or _json_type(c) for c in undecided}
    assert kinds == {"baseline", "counterparty_profile", "verdict", "approval", "action", "deal_report", None}


def _json_type(capsule_id: str) -> str | None:
    """A disclosed non-deal record's sealed ``type``; ``None`` when no own
    copy discloses it (each deal's counterparty report)."""
    for path in OWN:
        record = _json(path)["disclosures"].get(capsule_id, {}).get("agent_input")
        if record is not None:
            return record.get("type")
    return None


def test_the_withheld_records_are_the_counterparty_reports():
    for own, share in zip(OWN, SHARES, strict=True):
        (withheld,) = load_withheld([own])
        assert _json(share)["disclosures"][withheld]["agent_input"]["type"] == "deal_report"


# -- the counterparty shares -----------------------------------------------------------


def test_no_share_discloses_a_companion_or_carries_its_fingerprint():
    for share in SHARES:
        text = share.read_text(encoding="utf-8")
        assert _of_type(share, "counterparty_profile") == {}
        for payee in _profile_payees():
            assert payee not in text


def test_a_share_lists_the_companion_only_as_a_withheld_step():
    """What capsulectl's share does carry: the companion's sealed capsule (its
    digests and signature, no record) and one withheld step under the neutral
    kind ``private`` (capsule-cli #170): the share never names the record type."""
    for own, share in zip(OWN, SHARES, strict=True):
        (companion,) = _of_type(own, "counterparty_profile")
        shared = _json(share)
        assert companion in {r["capsule_id"] for r in shared["records"]}
        assert companion not in shared["disclosures"]
        text = share.read_text(encoding="utf-8")
        assert "counterparty_profile" not in text
        assert re.search(r'"capsule_id":"' + companion + r'","kind":"private","line":"[^"]*","n":\d+,"withheld":true', text)


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


# -- the same deals, sealed at another taxonomy version ---------------------------------


def _restated_at(version: str) -> ReplayResult:
    """Both deals with every check sealed at taxonomy ``version``."""
    return _deals_changed({"check": _with_body(taxonomy_version=version)}, deals=(1, 2))


def _summary(result: ReplayResult) -> list[tuple[str, str, list[str]]]:
    return [
        (s.record["capsule_id"], s.decision.outcome, sorted(c.id for c in s.decision.constraints if c.result == "fail"))
        for s in result.decisions
    ]


def test_the_deals_sealed_at_taxonomy_5_replay_under_5():
    """Each check is evaluated, never held, and gets the decision it gets at 6.
    money.purchase is the same row in 5 as in 6, so this cannot tell which of
    the two tables a check was evaluated under; ``test_record_taxonomy_gate``
    replays a class whose row differs."""
    at_5 = _restated_at("5")
    assert _summary(at_5) == _summary(_replayed())
    assert all(s.decision.taxonomy_mismatch is None for s in at_5.decisions)
    assert all("taxonomy_mismatch" not in (c.evidence or {}) for s in at_5.decisions for c in s.decision.constraints)


def test_the_deals_sealed_at_a_version_not_carried_are_held_each_still_decided():
    at_1 = _restated_at("1")
    assert [s.record["capsule_id"] for s in at_1.decisions] == _checks(1) + _checks(2)
    mismatch = {"record_taxonomy_version": "1", "engine_taxonomy_version": TAXONOMY}
    assert all(s.decision.taxonomy_mismatch == mismatch for s in at_1.decisions)
    first, second = (s.decision for s in at_1.decisions)
    # Neither the new merchant nor the caps is evaluated, so the first check is
    # refused for its version; dedupe is an integrity check, so the repeat in
    # deal 2 still asks.
    assert (first.outcome, first.verdict) == ("deny", "not_evaluable")
    held = {c.id for c in first.constraints if (c.evidence or {}).get("taxonomy_mismatch") == mismatch}
    evaluated = {"dedupe", "verify_before_dispatch", "credential_pattern"}
    assert held == {c.id for c in first.constraints} - evaluated
    assert (second.verdict, [c.id for c in second.constraints if c.result == "fail"]) == ("escalate", ["dedupe"])


def test_a_held_deal_is_never_seen_by_the_next():
    """Deal 1 sealed at a version the engine does not carry is refused, so its
    executed act is not carried out in the replay: deal 2, at its own version,
    meets the merchant as new."""
    result = _deals_changed({"check": _with_body(taxonomy_version="1")})
    first, second = (s.decision for s in result.decisions)
    assert (first.outcome, first.verdict) == ("deny", "not_evaluable")
    assert second.taxonomy_mismatch is None
    seen = _seen_on_deal_2(result)
    assert (seen.result, seen.evidence["prior_count"]) == ("fail", 0)
