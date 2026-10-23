# SPDX-License-Identifier: Apache-2.0
"""A complete history the caps window cannot be summed over is held, and the
engine asks rather than allows (capsule-engine#214 group 3; Sherlock2, ops
u504): a history that reaches back fewer days than the window, one with an
act in the window that carries no recorded decision and a spend that cannot
be read (under a fold that counts accepted acts only), or one with acts
classed under a taxonomy whose classes are not the engine's. The Go guard
holds the same window with the same reasons, in the same order
(go/guard/engine.go windowCoverage)."""
from __future__ import annotations

import copy
import dataclasses
import json
from pathlib import Path

import pytest
from agent_action_capsule import json_digest
from cll.ledger import LedgerStore

from capsule_engine.guards.checks.caps import WINDOW_UNREADABLE_REASON
from capsule_engine.guards.classes import ENGINE_TAXONOMY, same_classes
from capsule_engine.guards.history_state import WINDOW_HELD, history_state
from capsule_engine.packs import install_pack, load_pack_dir
from capsule_engine.report.live_check import decide_check_input
from capsule_engine.report.live_history import _act_taxonomy_version, history_ledger, window_held_reason

ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = ROOT.parent
EMIT = REPO_ROOT / "go" / "guard" / "testdata" / "emit"
PACK = load_pack_dir(ROOT / "capsule_engine" / "packs" / "catalog" / "everyday")
# The frozen everyday 0.3.5, whose caps fold (spend.weekly/3.0.0, keyed by
# operator) counts accepted acts only: the fold an undecided act holds.
ACCEPTED_ONLY = load_pack_dir(ROOT / "tests" / "fixtures" / "packs" / "everyday-0.3.5")
G3 = REPO_ROOT / "go" / "guard" / "testdata" / "g3-executed-acts-vectors" / "inputs"
STATEMENTS = REPO_ROOT / "go" / "guard" / "testdata" / "statement-window-vectors" / "inputs"


def _envelope(name: str) -> dict:
    return json.loads((EMIT / f"{name}.json").read_text())


def _caps(envelope: dict, pack=PACK):
    decision = decide_check_input(pack, envelope).decision
    (caps,) = [c for c in decision.constraints if c.id == "caps"]
    return decision, caps


def test_a_history_shorter_than_the_window_is_held():
    decision, caps = _caps(_envelope("pay-history-5-days"))
    assert caps.result == "n/a"
    assert caps.reason == "the history supplied reaches back 5 day(s); the 7d total needs 7d"
    assert caps.evidence == {"constraint_id": "caps", "in_scope": True, "missing_field": None}
    assert decision.outcome == "escalate"
    assert decision.verdict == "escalate"


def test_a_held_window_reads_as_an_escalation_only_when_it_asks():
    """capsule-engine#217: held with an approver, the decision asks, and its
    verdict is the escalation. Refused for want of an approver (#216), the
    same held window is labelled not_evaluable."""
    decision, _ = _caps(_envelope("pay-history-incomplete"))
    assert decision.asked_unevaluated_checks == ("caps",)
    assert (decision.outcome, decision.verdict) == ("escalate", "escalate")
    refused = dataclasses.replace(decision, outcome="deny")
    assert refused.verdict == "not_evaluable"


def test_acts_under_another_taxonomy_hold_the_window():
    decision, caps = _caps(_envelope("pay-history-taxonomy-1-acts"))
    assert caps.result == "n/a"
    assert caps.reason == (
        f"3 earlier act(s) were classed under another taxonomy version than the engine's "
        f"({ENGINE_TAXONOMY.version}), so the 7d total cannot be computed"
    )
    assert decision.outcome == "escalate"


@pytest.mark.parametrize("days", [7, 31])
def test_a_history_that_reaches_over_the_window_is_summed(days):
    envelope = _envelope("pay-history-5-days")
    envelope["history_scope"]["days"] = days
    decision, caps = _caps(envelope)
    assert caps.result == "pass"
    assert decision.outcome == "allow"


def test_a_history_with_no_days_is_held_as_reaching_back_none():
    envelope = _envelope("pay-history-5-days")
    del envelope["history_scope"]["days"]
    _, caps = _caps(envelope)
    assert caps.reason == "the history supplied reaches back 0 day(s); the 7d total needs 7d"


def test_an_incomplete_history_is_held_as_incomplete_first():
    envelope = copy.deepcopy(_envelope("pay-history-5-days"))
    envelope["history_scope"]["complete"] = False
    _, caps = _caps(envelope)
    assert caps.reason.startswith("the history supplied is incomplete")


def test_a_carried_taxonomy_with_the_engines_classes_is_the_same_classes():
    assert same_classes(ENGINE_TAXONOMY.version)
    assert all(same_classes(v) for v in ("2", "3", "4", "5"))
    assert not same_classes("1")
    assert not same_classes(6)


def test_a_failing_per_purchase_limit_still_stands_under_a_held_window():
    envelope = json.loads((REPO_ROOT / "conformance-vectors" / "guard" / "v0" / "inputs" / "caps-held"
                           / "short-history-over-limit.json").read_text())
    decision, caps = _caps(envelope)
    assert caps.result == "fail"
    assert decision.outcome == "escalate"


def test_with_no_caps_fold_nothing_is_held(tmp_path):
    with LedgerStore(tmp_path / "ledger") as store:
        history_ledger(_envelope("pay-history-5-days"), store, caps_fold=None)
        assert not [r for r in store.scan() if history_state(r.capsule) == WINDOW_HELD]


def test_the_marker_changes_no_check_but_caps():
    short = decide_check_input(PACK, _envelope("pay-history-5-days")).decision
    whole = _envelope("pay-history-5-days")
    whole["history_scope"]["days"] = 31
    full = decide_check_input(PACK, whole).decision
    others = lambda d: [(c.id, c.result, c.evidence) for c in d.constraints if c.id != "caps"]  # noqa: E731
    assert others(short) == others(full)


@pytest.mark.parametrize(
    ("shown", "version"),
    [
        ({"body": {"taxonomy_version": "6", "unchecked": {"taxonomy_version": "1"}}}, "1"),
        ({"body": {"taxonomy_version": "1", "unchecked": {"amount_minor": 1}}}, "1"),
        ({"type": "action-outcome/v0", "body": {"taxonomy_version": "6", "attempted": {"taxonomy_version": "1"}}}, "1"),
        # attempted counts only on a typed outcome, as the Go guard reads it
        ({"body": {"taxonomy_version": "6", "attempted": {"taxonomy_version": "1"}}}, "6"),
        ({"body": {"taxonomy_version": "6"}}, "6"),
        (None, None),
    ],
)
def test_an_act_names_its_taxonomy_in_the_member_that_records_it(shown, version):
    assert _act_taxonomy_version(shown) == version


# -- an act with no recorded decision -----------------------------------------

UNDECIDED_REASON = ("{} earlier act(s) carry no recorded decision; the 7d total as the engine computes it counts "
                    "accepted acts only, so it cannot be computed")


def _rebound(entry: dict) -> dict:
    """``entry`` with its capsule binding its edited ``agent_input`` again."""
    entry["model_attestation"]["compute_attestation"]["agent_input_digest"] = json_digest(entry["agent_input"])
    return entry


def _undecided(entry: dict, **body) -> dict:
    """``entry`` with no recorded decision and no ``spend_minor``, its body
    then given ``body`` (a ``None`` removes the member)."""
    entry.pop("disposition", None)
    shown = entry["agent_input"]["body"]
    shown.pop("spend_minor", None)
    for name, value in body.items():
        if value is None:
            shown.pop(name, None)
        else:
            shown[name] = value
    return _rebound(entry)


def _with_undecided(*indexes: int, **body) -> dict:
    """``pay-history-under`` (accepted acts on 10-06, 10-03 and, outside the
    window, 09-20; checked on 10-07) with the acts at ``indexes`` undecided."""
    envelope = _envelope("pay-history-under")
    for index in indexes:
        _undecided(envelope["history"][index], **body)
    return envelope


def test_an_undecided_act_whose_spend_cannot_be_read_holds_the_window():
    decision, caps = _caps(_with_undecided(0), ACCEPTED_ONLY)
    assert caps.result == "n/a"
    assert caps.reason == UNDECIDED_REASON.format(1)
    assert caps.method == "spend.weekly/3.0.0"
    assert caps.evidence == {"constraint_id": "caps", "in_scope": True, "missing_field": None}
    assert decision.outcome == "escalate"
    assert decision.verdict == "escalate"


def test_the_same_history_with_its_decisions_is_allowed():
    decision, caps = _caps(_envelope("pay-history-under"), ACCEPTED_ONLY)
    assert caps.result == "pass"
    assert decision.outcome == "allow"


def test_every_undecided_act_in_the_window_is_counted():
    _, caps = _caps(_with_undecided(0, 1), ACCEPTED_ONLY)
    assert caps.reason == UNDECIDED_REASON.format(2)


@pytest.mark.parametrize("spend", [-1, "3000", {"minor": 3000}, True])
def test_a_spend_in_another_shape_cannot_be_read(spend):
    _, caps = _caps(_with_undecided(0, spend_minor=spend), ACCEPTED_ONLY)
    assert caps.reason == UNDECIDED_REASON.format(1)


def test_an_undecided_act_outside_the_window_does_not_hold_it():
    decision, caps = _caps(_with_undecided(2), ACCEPTED_ONLY)
    assert caps.result == "pass"
    assert decision.outcome == "allow"


def test_an_undecided_act_under_another_operator_does_not_hold_it():
    envelope = _with_undecided(0)
    envelope["history"][0]["operator"] = "another-operator"
    _, caps = _caps(envelope, ACCEPTED_ONLY)
    assert caps.result == "pass"


@pytest.mark.parametrize(
    "edit",
    [
        pytest.param(lambda e: _undecided(e, spend_minor=0), id="spend-0"),
        pytest.param(lambda e: _undecided(e, spend_minor=3000), id="readable-spend"),
        pytest.param(lambda e: _undecided(e, direction="in"), id="money-in"),
        pytest.param(lambda e: {**_undecided(e), "disposition": {"decision": "reject"}}, id="decided"),
        pytest.param(lambda e: _rebound({**_undecided(e), "agent_input": {**e["agent_input"], "chain_id": "another"}}),
                     id="names-two-deals"),
        pytest.param(lambda e: _rebound({**_undecided(e), "agent_input": {**e["agent_input"], "body": {
            "action_class": "money.purchase", "taxonomy_version": "6", "unchecked": e["agent_input"]["body"]}}}),
            id="unchecked"),
    ],
)
def test_an_act_the_window_counts_or_leaves_out_does_not_hold_it(edit):
    envelope = _envelope("pay-history-under")
    envelope["history"][0] = edit(envelope["history"][0])
    _, caps = _caps(envelope, ACCEPTED_ONLY)
    assert caps.result == "pass"


def test_a_statement_is_not_an_undecided_act():
    claim = json.loads((STATEMENTS / "statement-in-history" / "step-2.input.json").read_text())["history"][0]
    envelope = _envelope("pay-history-under")
    envelope["history"].insert(0, {**claim, "timestamp": "2026-10-07T00:00:00Z", "operator": "deal"})
    written = decide_check_input(ACCEPTED_ONLY, envelope).written
    assert written.statement == 1
    _, caps = _caps(envelope, ACCEPTED_ONLY)
    assert caps.result == "pass"


def test_an_act_whose_capsule_does_not_bind_its_input_has_no_spend_to_read():
    # The Go guard refuses such a history (capsule-engine#214 group 5); the
    # engine reads it, and its spend, as unread.
    envelope = _with_undecided(0)
    envelope["history"][0]["model_attestation"]["compute_attestation"]["agent_input_digest"] = "0" * 64
    _, caps = _caps(envelope, ACCEPTED_ONLY)
    assert caps.reason == UNDECIDED_REASON.format(1)


def test_an_outcome_stating_its_attempted_act_is_unchecked_and_does_not_hold():
    envelope = _envelope("pay-history-under")
    entry = _undecided(envelope["history"][0])
    entry["agent_input"] = {"type": "action-outcome/v0", "body": {
        "action_class": "money.purchase", "taxonomy_version": "6", "attempted": entry["agent_input"]["body"]}}
    _rebound(entry)
    _, caps = _caps(envelope, ACCEPTED_ONLY)
    assert caps.result == "pass"


def test_a_checked_record_with_no_time_holds_every_undecided_act():
    envelope = _with_undecided(0, 2)
    del envelope["record"]["timestamp"]
    _, caps = _caps(envelope, ACCEPTED_ONLY)
    assert caps.reason == UNDECIDED_REASON.format(2)


def test_a_checked_record_whose_time_cannot_be_read_holds_every_undecided_act(tmp_path):
    # caps refuses such a check itself; the hold counts every undecided act,
    # in the window or not, as the Go guard's windowCoverage does.
    envelope = _with_undecided(0, 2)
    caps_fold = install_pack(ACCEPTED_ONLY, project_dir=tmp_path / "project", mode="observe").resolved.caps_fold()
    reason = window_held_reason(envelope["history_scope"], envelope["history"], caps_fold,
                                {**envelope["record"], "timestamp": "not a time"})
    assert reason == UNDECIDED_REASON.format(2)


def _transfer(envelope: dict) -> dict:
    """``envelope`` with its checked record classed ``money.transfer``, the
    one class seller 0.1.0 and 0.1.1's caps/1.0.0 limits."""
    record = envelope["record"]
    record["agent_input"]["body"]["action_class"] = "money.transfer"
    record["model_attestation"]["compute_attestation"]["agent_input_digest"] = json_digest(record["agent_input"])
    return envelope


@pytest.mark.parametrize("version", ["seller-0.1.0", "seller-0.1.1"])
def test_under_a_fold_keyed_by_developer_only_the_checks_developer_holds(version):
    # spend.weekly/1.0.0 sums accepted acts by developer.
    pack = load_pack_dir(ROOT / "tests" / "fixtures" / "packs" / version)
    _, caps = _caps(_transfer(_with_undecided(0)), pack)
    assert (caps.method, caps.reason) == ("spend.weekly/1.0.0", UNDECIDED_REASON.format(1))
    envelope = _transfer(_with_undecided(0))
    envelope["history"][0]["developer"] = "another-developer"
    _, caps = _caps(envelope, pack)
    assert caps.result == "pass"


def test_under_a_fold_that_counts_executed_acts_the_window_names_the_unreadable_spend():
    _, caps = _caps(_with_undecided(0))
    assert caps.reason == WINDOW_UNREADABLE_REASON
    assert caps.evidence["missing_field"] == "spend_minor"


def test_a_short_history_is_held_before_an_undecided_act():
    envelope = _with_undecided(0)
    envelope["history_scope"]["days"] = 5
    _, caps = _caps(envelope, ACCEPTED_ONLY)
    assert caps.reason == "the history supplied reaches back 5 day(s); the 7d total needs 7d"


def test_an_undecided_act_is_held_before_a_foreign_taxonomy():
    envelope = _with_undecided(0)
    other = envelope["history"][1]
    other["agent_input"]["body"]["taxonomy_version"] = "1"
    _rebound(other)
    _, caps = _caps(envelope, ACCEPTED_ONLY)
    assert caps.reason == UNDECIDED_REASON.format(1)
    _undecided(envelope["history"][0], spend_minor=3000)
    _, caps = _caps(envelope, ACCEPTED_ONLY)
    assert caps.reason.startswith("1 earlier act(s) were classed under another taxonomy version")


# The G3 vectors' unreadable steps, which the Go guard holds with this reason
# under everyday 0.3.5 (go/guard/g3_executed_acts_test.go); the one outside
# the window it does not hold.
@pytest.mark.parametrize("sequence", ["unreadable-no-amount-at-all", "unreadable-no-spend-minor",
                                      "unreadable-spend-a-string", "unreadable-spend-an-object",
                                      "unreadable-spend-negative", "unreadable-window-edge",
                                      "unreadable-outside-the-window"])
def test_the_g3_unreadable_steps_are_held_as_the_go_guard_holds_them(sequence):
    _, caps = _caps(json.loads((G3 / sequence / "step-2.input.json").read_text()), ACCEPTED_ONLY)
    if sequence == "unreadable-outside-the-window":
        assert caps.result == "pass"
    else:
        assert caps.reason == UNDECIDED_REASON.format(1)
