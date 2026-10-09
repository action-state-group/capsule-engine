# SPDX-License-Identifier: Apache-2.0
"""The payee keyed per profile: one merchant is one counterparty across a
profile's deals.

A deal check seals its payee's fingerprint keyed per deal
(``x-deal-v0.counterparty``, ``hmac-sha256-deal-key``), so the same merchant
reads as new in every deal. capsulectl also keys it per profile
(``hmac-sha256-profile-key``): it passes that block to the checker as
``record.counterparty_profile`` in external-check-input/v0, and seals it on a
companion record (``record_type`` ``counterparty_profile``) right after the
check, naming the check by digest under rel ``about``. The engine reads it
from the input live (``action_for_check_input``) and from the companion in a
replay, and keys the action's target on it; without it the target is the
per-deal one, as before.

The fixture is capsulectl's own: two deals of one profile paying one merchant
(``fixtures/payee-fp-profile/vectors.json``, from capsule-cli
``internal/cli/testdata/payee-fp-profile/vectors.json`` at commit
705819215be60dbf0c9a7f95aa8de4eac693b27d, sha256
141ec55a5d75900749392133e4dbc6b3b6bd8cb1ae7c8d6a1ceed1ac6c8a275d). Each case
is a check's sealed record and its companion, as their exact JCS bytes.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from agent_action_capsule import json_digest

from capsule_engine.cli.main import main as cli_main
from capsule_engine.folds.loader import load_definition_file as load_fold
from capsule_engine.guards import Action, GuardEngine
from capsule_engine.guards.capsule import ALLOW, ESCALATE, LOCAL_ONLY_TARGET_PREFIX, local_only_refusal
from capsule_engine.guards.checks.plan_containment import check_plan_containment
from capsule_engine.guards.plan import parse_plan_definition
from capsule_engine.guards.wickets import load_definition_file as load_wicket
from capsule_engine.report.replay import action_for_check_input, action_for_record, replay

ROOT = Path(__file__).parent.parent / "capsule_engine"
SPEND_WEEKLY = ROOT / "folds" / "catalog_defs" / "spend.weekly.yaml"
SEEN_BEFORE_V2 = ROOT / "guards" / "wickets" / "catalog_defs" / "counterparty_seen_before.v2.yaml"
VECTORS = Path(__file__).parent / "fixtures" / "payee-fp-profile" / "vectors.json"
VECTORS_SHA256 = "141ec55a5d75900749392133e4dbc6b3b6bd8cb1ae7c8d6a1ceed1ac6c8a275d"
PROFILE_ALG = "hmac-sha256-profile-key"
DAY_1 = "2026-10-07T12:00:00Z"
DAY_2 = "2026-10-08T12:00:00Z"
IGNORED = ("counterparty_profile",)


# Reads the vendored JSON vectors: the test's decoding boundary.
def _cases() -> list[dict]:
    return json.loads(VECTORS.read_text())["cases"]


def _deal(n: int) -> tuple[dict, dict, str]:
    """Deal ``n``'s pay check, its companion, and the target capsulectl expects."""
    case = _cases()[n]
    return json.loads(case["check_jcs"]), json.loads(case["companion_jcs"]), case["expected_target"]


def _capsule(record: dict, seq: int, at: str) -> dict:
    """A capsule binding ``record`` by digest, as capsulectl seals each step."""
    return {
        "capsule_id": f"{seq:064x}",
        "action_id": f"deal-0000000000000000/{seq}",
        "action_type": "fyi",
        "operator": "household-a",
        "developer": "capsulectl-deal",
        "timestamp": at,
        "model_attestation": {"compute_attestation": {"agent_input_digest": json_digest(record)}},
    }


def _with_profile(companion: dict, block: object) -> dict:
    """``companion`` carrying ``block`` in place of its counterparty_profile."""
    out = json.loads(json.dumps(companion))
    out["x-deal-v0"]["counterparty_profile"] = block
    return out


def _bundle(*steps: tuple[dict, str]) -> tuple[list[dict], dict[str, dict]]:
    """Records and disclosures for ``steps`` (record, timestamp), in order."""
    capsules = [_capsule(record, seq, at) for seq, (record, at) in enumerate(steps, start=1)]
    return capsules, {c["capsule_id"]: record for c, (record, _) in zip(capsules, steps, strict=True)}


def _result(*steps: tuple[dict, str]):
    records, disclosed = _bundle(*steps)
    return replay(records, caps_fold=load_fold(SPEND_WEEKLY), disclosed=disclosed)


def _replay(*steps: tuple[dict, str]):
    """The decisions: one per check, none for a companion."""
    return _result(*steps).decisions


def _two_deals(with_companions: bool = True):
    (check_1, companion_1, _), (check_2, companion_2, _) = _deal(0), _deal(1)
    steps = [(check_1, DAY_1), (companion_1, DAY_1), (check_2, DAY_2), (companion_2, DAY_2)]
    if not with_companions:
        steps = [steps[0], steps[2]]
    return _replay(*steps)


def _constraint(decision, check_id: str):
    return next(c for c in decision.constraints if c.id == check_id)


def _entry(record: dict, seq: int, at: str, **members: object) -> dict:
    """One external-check-input/v0 record entry: the capsule, its disclosed
    agent_input, and any envelope members beside them."""
    return {**_capsule(record, seq, at), "agent_input": record, **members}


def _profile_block(n: int) -> dict:
    return _deal(n)[1]["x-deal-v0"]["counterparty_profile"]


# -- the fixture is capsulectl's ---------------------------------------------------


def test_the_vendored_vectors_are_the_producers_bytes():
    assert hashlib.sha256(VECTORS.read_bytes()).hexdigest() == VECTORS_SHA256
    for n in (0, 1):
        check, companion, target = _deal(n)
        (ref,) = companion["x-deal-v0"]["refs"]
        assert (ref["rel"], ref["digest"]) == ("about", json_digest(check))
        assert target.startswith(LOCAL_ONLY_TARGET_PREFIX)


# -- replay: the companion keys the check ------------------------------------------


def test_replay_keys_each_check_on_its_companions_profile_fingerprint():
    decisions = _two_deals()
    checks = [d for d in decisions if d.action.states_act]
    assert [d.action.target for d in checks] == [_deal(0)[2], _deal(1)[2]]
    assert checks[0].action.target == checks[1].action.target
    # The per-deal block is carried as it was, so counterparty_list is unchanged.
    for sourced, n in zip(checks, (0, 1), strict=True):
        per_deal = _deal(n)[0]["x-deal-v0"]["counterparty"]
        assert sourced.action.counterparty_ids == per_deal["ids"]
        assert sourced.action.counterparty_fp_alg == per_deal["fp_alg"]


def test_with_the_profile_fingerprint_the_cross_deal_repeat_asks_the_approver():
    _, second = _two_deals()
    out = _constraint(second.decision, "dedupe")
    assert (out.result, second.decision.outcome) == ("fail", ESCALATE)
    assert out.evidence["repeat"] == "other_deal"


def test_without_it_each_deal_keeps_its_own_fingerprint_and_nothing_repeats():
    first, second = _two_deals(with_companions=False)
    assert first.action.target != second.action.target
    for sourced, n in zip((first, second), (0, 1), strict=True):
        payee = _deal(n)[0]["x-deal-v0"]["counterparty"]["ids"]["payee"]
        assert sourced.action.target == f"payee-fp:hmac-sha256-deal-key:{payee}"
    assert _constraint(second.decision, "dedupe").result == "pass"
    assert second.decision.outcome == ALLOW


def test_the_companion_is_not_an_act_and_gets_no_decision():
    (check_1, companion_1, _), (check_2, companion_2, _) = _deal(0), _deal(1)
    result = _result((check_1, DAY_1), (companion_1, DAY_1), (check_2, DAY_2), (companion_2, DAY_2))
    assert [s.record["capsule_id"] for s in result.decisions] == [f"{1:064x}", f"{3:064x}"]
    assert [r["capsule_id"] for r in result.undecided] == [f"{2:064x}", f"{4:064x}"]
    for record, companion in zip(result.undecided, (companion_1, companion_2), strict=True):
        assert action_for_record(record, companion).states_act is False


def test_a_companion_naming_another_record_keys_nothing():
    check, companion, _ = _deal(0)
    stray = json.loads(json.dumps(companion))
    stray["x-deal-v0"]["refs"][0]["digest"] = "0" * 64
    (first,) = _replay((check, DAY_1), (stray, DAY_1))
    assert first.action.target.startswith("payee-fp:hmac-sha256-deal-key:")
    assert first.action.ignored_inputs == ()


def test_a_companion_under_another_rel_keys_nothing():
    check, companion, _ = _deal(0)
    other = json.loads(json.dumps(companion))
    other["x-deal-v0"]["refs"][0]["rel"] = "checks"
    (first,) = _replay((check, DAY_1), (other, DAY_1))
    assert first.action.target.startswith("payee-fp:hmac-sha256-deal-key:")


def test_a_companion_ref_of_another_type_keys_nothing():
    check, companion, _ = _deal(0)
    other = json.loads(json.dumps(companion))
    other["x-deal-v0"]["refs"][0]["type"] = "task-authority"
    (first,) = _replay((check, DAY_1), (other, DAY_1))
    assert first.action.target.startswith("payee-fp:hmac-sha256-deal-key:")


def test_a_companion_with_more_than_one_ref_keys_nothing():
    check, companion, _ = _deal(0)
    other = json.loads(json.dumps(companion))
    other["x-deal-v0"]["refs"].append(dict(other["x-deal-v0"]["refs"][0], rel="checks"))
    (first,) = _replay((check, DAY_1), (other, DAY_1))
    assert first.action.target.startswith("payee-fp:hmac-sha256-deal-key:")


def test_two_companions_about_one_check_are_ignored():
    check, companion, _ = _deal(0)
    rival = _with_profile(companion, {"fp_alg": PROFILE_ALG, "ids": {"payee": "b" * 64}})
    (first,) = _replay((check, DAY_1), (companion, DAY_1), (rival, DAY_1))
    assert first.action.target.startswith("payee-fp:hmac-sha256-deal-key:")
    assert first.action.ignored_inputs == IGNORED


# -- live: the checker input keys the record ---------------------------------------


@pytest.mark.parametrize("n", [0, 1])
def test_live_input_and_replay_companion_give_the_same_target_bytes(n):
    check, companion, expected = _deal(n)
    live = action_for_check_input(_entry(check, 1, DAY_1, counterparty_profile=_profile_block(n)))
    (replayed,) = _replay((check, DAY_1), (companion, DAY_1))
    assert live.target == replayed.action.target == expected
    assert live.target.encode() == expected.encode()


def test_live_input_without_the_member_keys_on_the_per_deal_fingerprint():
    check, _, _ = _deal(0)
    live = action_for_check_input(_entry(check, 1, DAY_1))
    assert live == action_for_record(_capsule(check, 1, DAY_1), check)
    assert live.target.startswith("payee-fp:hmac-sha256-deal-key:")


def _seen_before_on_deal_2(store, signer, *, with_profile: bool):
    """Deal 1's purchase is accepted for real; deal 2's is then checked by an
    engine running counterparty_seen_before/2.0.0, which counts no dry run."""
    fold = load_fold(SPEND_WEEKLY)
    members = [{"counterparty_profile": _profile_block(n)} if with_profile else {} for n in (0, 1)]
    deal_1 = action_for_check_input(_entry(_deal(0)[0], 1, DAY_1, **members[0]))
    deal_2 = action_for_check_input(_entry(_deal(1)[0], 2, DAY_2, **members[1]))
    plain = GuardEngine(ledger=store, caps_fold=fold, signer_provider=lambda: signer)
    assert plain.check(deal_1).capsule["disposition"]["decision"] == "accept"
    engine = GuardEngine(
        ledger=store, caps_fold=fold, signer_provider=lambda: signer, wickets=(load_wicket(SEEN_BEFORE_V2),)
    )
    return engine.check(deal_2)


def test_live_with_the_profile_fingerprint_the_merchant_is_seen_before_and_the_repeat_asks(store, signer):
    decision = _seen_before_on_deal_2(store, signer, with_profile=True)
    seen = _constraint(decision, "counterparty_seen_before")
    assert (seen.result, seen.evidence["prior_count"]) == ("pass", 1)
    assert _constraint(decision, "dedupe").evidence["repeat"] == "other_deal"
    assert decision.outcome == ESCALATE


def test_live_without_it_the_merchant_is_new_in_every_deal(store, signer):
    decision = _seen_before_on_deal_2(store, signer, with_profile=False)
    seen = _constraint(decision, "counterparty_seen_before")
    assert (seen.result, seen.evidence["prior_count"]) == ("fail", 0)
    assert _constraint(decision, "dedupe").result == "pass"


# -- an envelope value that is not the agreed shape is ignored ----------------------

_GOOD_PAYEE = "6c55aadf729943d4ef73ad242b6a4fa8b471d8b4077947fc36d415e2fa354342"
BAD_BLOCKS = {
    "another fp_alg": {"fp_alg": "hmac-sha256-deal-key", "ids": {"payee": _GOOD_PAYEE}},
    "no fp_alg": {"ids": {"payee": _GOOD_PAYEE}},
    "uppercase hex": {"fp_alg": PROFILE_ALG, "ids": {"payee": _GOOD_PAYEE.upper()}},
    "short hex": {"fp_alg": PROFILE_ALG, "ids": {"payee": _GOOD_PAYEE[:63]}},
    "not hex": {"fp_alg": PROFILE_ALG, "ids": {"payee": "z" * 64}},
    "trailing newline": {"fp_alg": PROFILE_ALG, "ids": {"payee": _GOOD_PAYEE + "\n"}},
    "no payee": {"fp_alg": PROFILE_ALG, "ids": {}},
    "ids not an object": {"fp_alg": PROFILE_ALG, "ids": [_GOOD_PAYEE]},
    "not an object": f"{PROFILE_ALG}:{_GOOD_PAYEE}",
}


@pytest.mark.parametrize("name", sorted(BAD_BLOCKS))
def test_a_bad_live_value_is_ignored_and_noted(name):
    check, _, _ = _deal(0)
    live = action_for_check_input(_entry(check, 1, DAY_1, counterparty_profile=BAD_BLOCKS[name]))
    payee = check["x-deal-v0"]["counterparty"]["ids"]["payee"]
    assert live.target == f"payee-fp:hmac-sha256-deal-key:{payee}"
    assert live.ignored_inputs == IGNORED


@pytest.mark.parametrize("name", sorted(BAD_BLOCKS))
def test_a_bad_companion_value_is_ignored_and_noted(name):
    check, companion, _ = _deal(0)
    (first,) = _replay((check, DAY_1), (_with_profile(companion, BAD_BLOCKS[name]), DAY_1))
    assert first.action.target.startswith("payee-fp:hmac-sha256-deal-key:")
    assert first.action.ignored_inputs == IGNORED


def test_a_companion_carrying_no_block_is_ignored_and_noted():
    check, companion, _ = _deal(0)
    empty = json.loads(json.dumps(companion))
    del empty["x-deal-v0"]["counterparty_profile"]
    (first,) = _replay((check, DAY_1), (empty, DAY_1))
    assert first.action.target.startswith("payee-fp:hmac-sha256-deal-key:")
    assert first.action.ignored_inputs == IGNORED


def test_the_seen_before_evidence_names_the_ignored_field_and_never_its_value(store, signer):
    check, _, _ = _deal(0)
    action = action_for_check_input(_entry(check, 1, DAY_1, counterparty_profile=BAD_BLOCKS["uppercase hex"]))
    engine = GuardEngine(
        ledger=store, caps_fold=load_fold(SPEND_WEEKLY), signer_provider=lambda: signer,
        wickets=(load_wicket(SEEN_BEFORE_V2),),
    )
    decision = engine.check(action, dry_run=True)
    seen = _constraint(decision, "counterparty_seen_before")
    assert seen.evidence["ignored_inputs"] == list(IGNORED)
    assert _GOOD_PAYEE.upper() not in json.dumps(seen.evidence)
    # Evidence only: the action's own payload never seals it.
    assert "ignored_inputs" not in decision.capsule["asg_payload"]
    assert _GOOD_PAYEE.upper() not in json.dumps(decision.capsule)


def test_a_good_value_notes_nothing(store, signer):
    check, _, _ = _deal(0)
    action = action_for_check_input(_entry(check, 1, DAY_1, counterparty_profile=_profile_block(0)))
    assert action.ignored_inputs == ()
    engine = GuardEngine(
        ledger=store, caps_fold=load_fold(SPEND_WEEKLY), signer_provider=lambda: signer,
        wickets=(load_wicket(SEEN_BEFORE_V2),),
    )
    assert "ignored_inputs" not in _constraint(engine.check(action, dry_run=True), "counterparty_seen_before").evidence


# -- transition: no back-fill --------------------------------------------------------


def test_a_decision_made_before_the_profile_fingerprint_never_matches_one_made_after():
    """Deal 1 was decided on the per-deal target, before capsulectl sealed
    companions; deal 2 on the profile target. Sealed decisions are never
    rewritten, so cross-deal matching starts at deal 2."""
    (check_1, _, _), (check_2, companion_2, _) = _deal(0), _deal(1)
    first, second = _replay((check_1, DAY_1), (check_2, DAY_2), (companion_2, DAY_2))
    assert first.action.target.startswith("payee-fp:hmac-sha256-deal-key:")
    assert second.action.target.startswith(LOCAL_ONLY_TARGET_PREFIX)
    assert _constraint(second.decision, "dedupe").result == "pass"
    assert second.decision.outcome == ALLOW


# -- a profile-scoped target is local-only -------------------------------------------


def _profile_hexes() -> set[str]:
    return {_deal(n)[2].removeprefix(LOCAL_ONLY_TARGET_PREFIX) for n in (0, 1)}


def _assert_refused_naming_target(message: str, count: int) -> None:
    assert f"{count} record(s)" in message
    assert "target" in message
    assert "local-only" in message
    for value in _profile_hexes():
        assert value not in message


def test_the_refusal_names_the_target_field_and_the_count():
    decisions = [d.decision.capsule for d in _two_deals()]
    _assert_refused_naming_target(local_only_refusal(decisions), 2)


def test_a_per_deal_target_is_not_local_only():
    decisions = [d.decision.capsule for d in _two_deals(with_companions=False)]
    assert all(not d["asg_payload"].get("target", "").startswith(LOCAL_ONLY_TARGET_PREFIX) for d in decisions)
    found = local_only_refusal(decisions)
    assert found is None or "target" not in found


def _bundle_file(tmp_path: Path, with_companions: bool) -> Path:
    (check_1, companion_1, _), (check_2, companion_2, _) = _deal(0), _deal(1)
    steps = [(check_1, DAY_1), (companion_1, DAY_1), (check_2, DAY_2), (companion_2, DAY_2)]
    if not with_companions:
        steps = [steps[0], steps[2]]
    records, disclosed = _bundle(*steps)
    path = tmp_path / "deal.bundle.json"
    path.write_text(json.dumps({"records": records, "disclosures": {k: {"agent_input": v} for k, v in disclosed.items()}}))
    return path


def test_dry_run_share_refuses_a_decision_with_a_profile_target(tmp_path, capsys):
    out = tmp_path / "report.html"
    rc = cli_main(["guard", "dry-run", "--ledger", str(_bundle_file(tmp_path, True)), "--since", "all",
                   "--out", str(out), "--share", "--no-manifest"])
    printed = capsys.readouterr()
    assert rc == 1
    assert not out.exists()
    assert "file://" not in printed.out
    assert "target" in printed.err
    for value in _profile_hexes():
        assert value not in printed.err + printed.out


def test_bundle_refuses_a_ledger_holding_a_decision_with_a_profile_target(tmp_path, capsys):
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text("".join(json.dumps(d.decision.capsule) + "\n" for d in _two_deals()))
    out = tmp_path / "bundle.json"
    rc = cli_main(["bundle", "--ledger", str(ledger), "--out", str(out)])
    printed = capsys.readouterr()
    assert rc == 2
    assert not out.exists()
    _assert_refused_naming_target(printed.err, 2)


# -- never in a reason ---------------------------------------------------------------


def test_no_reason_carries_a_fingerprint():
    hexes = _profile_hexes() | {_deal(n)[0]["x-deal-v0"]["counterparty"]["ids"]["payee"] for n in (0, 1)}
    for sourced in _two_deals():
        texts = [sourced.decision.reason or ""] + [c.reason or "" for c in sourced.decision.constraints]
        for value in hexes:
            assert all(value not in t for t in texts)


def test_a_plan_subject_mismatch_never_echoes_a_profile_target():
    target = _deal(0)[2]
    action = Action(verb="pay", operator="household-a", developer="d", action_class="money.purchase", target=target)
    plan = parse_plan_definition(
        {"outcome_id": "deal.paid/1.0.0", "allowed_actions": ["pay"], "binding": {"subject": "merchant/ref-1"}}
    )
    out = check_plan_containment(action, plan)
    assert out.constraint.result == "fail"
    assert target.removeprefix(LOCAL_ONLY_TARGET_PREFIX) not in out.constraint.reason
