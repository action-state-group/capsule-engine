# SPDX-License-Identifier: Apache-2.0
"""counterparty_seen_before/5.0.0: a merchant is not read as new on a history
that may be missing acts (capsule-engine#327).

Version 4 failed a purchase with no earlier act counted to its payee as a
first-time counterparty, whether or not the history was complete. On a live
history the caller does not know to be complete (the ``incomplete`` record
``report/live_history.py`` writes), version 5 records the rule ``n/a`` in
scope, with ``HISTORY_INCOMPLETE_REASON``, and the engine asks rather than
allows, as caps does on the same history. A counted earlier act still
passes, a complete history with none still fails, and a purchase naming no
payee still fails as no payee named. everyday 0.3.10 cites version 5;
0.3.9 is frozen citing version 4.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from agent_action_capsule import json_digest

from capsule_engine.guards import Action, GuardEngine, LocalSigner
from capsule_engine.guards.capsule import DENY, ESCALATE
from capsule_engine.guards.checks import CONFIGURED_CHECKS
from capsule_engine.guards.checks.counterparty_seen_before import HISTORY_INCOMPLETE_REASON
from capsule_engine.guards.history import MemoryHistory
from capsule_engine.guards.history_state import INCOMPLETE, LIVE_HISTORY
from capsule_engine.guards.wickets import load_definition_file as load_wicket
from capsule_engine.guards.wickets.definition import WicketDefinition
from capsule_engine.packs import load_pack_dir
from capsule_engine.packs.obligation_results import obligation_results
from capsule_engine.report.live_check import decide_check_input

REPO = Path(__file__).parent.parent
WICKETS = REPO / "capsule_engine" / "guards" / "wickets" / "catalog_defs"
PACK = load_pack_dir(REPO / "capsule_engine" / "packs" / "catalog" / "everyday")
FROZEN_0_3_9 = load_pack_dir(REPO / "tests" / "fixtures" / "packs" / "everyday-0.3.9")
EMIT = REPO.parent / "go" / "guard" / "testdata" / "emit"
V4_WICKET_DIGEST = "c7c20d9e57cb0a75184b9c6c4d037c9bea813a7b1105c581defd4481acf964b0"
V5_WICKET_DIGEST = "a8dbea32ff103fb07fe7d9b0791b149ecd654478d044ab8a12b6d3ca9cb78df5"
R02, R06 = "r02-ordinary-purchase", "r06-new-merchant"
OPERATOR = "household-incomplete-history"
AT = "2026-11-25T12:00:00Z"


# -- the definitions -------------------------------------------------------------


def test_v5_is_v4_with_incomplete_history_not_evaluable():
    v4 = load_wicket(WICKETS / "counterparty_seen_before.v4.yaml")
    v5 = load_wicket(WICKETS / "counterparty_seen_before.v5.yaml")
    assert (v4.definition_digest(), v5.definition_digest()) == (V4_WICKET_DIGEST, V5_WICKET_DIGEST)
    assert v5.config == {**v4.config, "incomplete_history": "not_evaluable"}


def test_everyday_0_3_10_cites_v5_and_changes_only_r02_and_r06_from_0_3_9():
    cited = {c.wicket_id: c.definition_digest() for c in PACK.constraints}
    frozen = {c.wicket_id: c.definition_digest() for c in FROZEN_0_3_9.constraints}
    assert PACK.pack_id == "asg/everyday/0.3.10"
    assert FROZEN_0_3_9.pack_id == "asg/everyday/0.3.9"
    assert cited.pop("counterparty_seen_before/5.0.0") == V5_WICKET_DIGEST
    assert frozen.pop("counterparty_seen_before/4.0.0") == V4_WICKET_DIGEST
    assert cited == frozen
    statements = {o.id: o.statement for o in PACK.obligations}
    frozen_statements = {o.id: o.statement for o in FROZEN_0_3_9.obligations}
    assert {k for k in statements if statements[k] != frozen_statements[k]} == {R02, R06}


# -- the check -------------------------------------------------------------------


def _purchase(target: str | None, *, operator: str = OPERATOR) -> Action:
    return Action(verb="make_purchase", operator=operator, developer="incomplete-history-test",
                  action_class="money.purchase", amount_minor=900, currency="EUR", rail="card", target=target,
                  action_id="make_purchase/incomplete-history", timestamp=AT)


def _incomplete_marker(operator: str = OPERATOR) -> dict:
    body = {"operator": operator, "timestamp": AT, "asg_payload": {LIVE_HISTORY: INCOMPLETE}}
    return {**body, "capsule_id": json_digest(body)}


def _check(version: str, action: Action, store: MemoryHistory):
    config = load_wicket(WICKETS / f"counterparty_seen_before.{version}.yaml").config
    return CONFIGURED_CHECKS["counterparty_seen_before"](action, store, config)


@pytest.fixture
def incomplete():
    with MemoryHistory() as store:
        store.append(_incomplete_marker())
        yield store


def test_v5_on_an_incomplete_history_with_none_counted_is_not_evaluated_and_asks(incomplete):
    out = _check("v5", _purchase("shop/new"), incomplete)
    assert (out.constraint.result, out.constraint.reason) == ("n/a", HISTORY_INCOMPLETE_REASON)
    assert out.constraint.evidence == {"constraint_id": "counterparty_seen_before", "in_scope": True,
                                       "missing_field": None}
    assert out.asks_when_unevaluated


def test_v4_on_the_same_history_fails_as_a_first_time_counterparty(incomplete):
    out = _check("v4", _purchase("shop/new"), incomplete)
    assert (out.constraint.result, out.asks_when_unevaluated) == ("fail", False)


def test_v5_on_a_complete_history_with_none_counted_fails_as_v4_does():
    with MemoryHistory() as store:
        v4, v5 = (_check(v, _purchase("shop/new"), store) for v in ("v4", "v5"))
    assert v5.constraint.result == "fail"
    assert (v4.constraint.reason, v4.constraint.evidence) == (v5.constraint.reason, v5.constraint.evidence)
    assert not v5.asks_when_unevaluated


def test_v5_reads_only_its_own_operators_marker():
    with MemoryHistory() as store:
        store.append(_incomplete_marker(operator="another-household"))
        out = _check("v5", _purchase("shop/new"), store)
    assert out.constraint.result == "fail"


@pytest.mark.parametrize("target", [None, "", "  "])
def test_v5_on_an_incomplete_history_still_fails_a_purchase_naming_no_payee(incomplete, target):
    out = _check("v5", _purchase(target), incomplete)
    assert (out.constraint.result, out.constraint.reason.startswith("no payee named")) == ("fail", True)
    assert not out.asks_when_unevaluated


def test_an_unknown_incomplete_history_mode_is_refused(incomplete):
    config = {**load_wicket(WICKETS / "counterparty_seen_before.v5.yaml").config, "incomplete_history": "skip"}
    with pytest.raises(ValueError, match="incomplete_history"):
        CONFIGURED_CHECKS["counterparty_seen_before"](_purchase("shop/new"), incomplete, config)


# -- the engine, with no caps to ask for it ---------------------------------------


def _engine(store: MemoryHistory, *, action_classes: list[str] | None = None) -> GuardEngine:
    v5 = load_wicket(WICKETS / "counterparty_seen_before.v5.yaml")
    if action_classes is not None:
        v5 = WicketDefinition(wicket_id=v5.wicket_id, check=v5.check,
                              config={**v5.config, "action_classes": action_classes}, semantics=v5.semantics)
    signer = LocalSigner(key_id="incomplete-history-test", secret=b"incomplete-history-test-fixed-key")
    return GuardEngine(history=store, caps_fold=None, signer_provider=lambda: signer, wickets=(v5,),
                       ask_wickets=frozenset({"counterparty_seen_before"}))


def test_the_engine_asks_when_only_counterparty_seen_before_could_not_be_evaluated(incomplete):
    decision = _engine(incomplete).check(_purchase("shop/new"))
    (caps,) = [c for c in decision.constraints if c.id == "caps"]
    assert caps.result == "n/a"  # no caps definition: it asks nothing here
    assert decision.outcome == ESCALATE
    assert decision.asked_unevaluated_checks == ("counterparty_seen_before",)
    assert decision.verdict == ESCALATE


def test_with_no_approver_the_engine_refuses_and_the_verdict_is_not_evaluable():
    # money.refund names no approver in the action taxonomy.
    with MemoryHistory() as store:
        store.append(_incomplete_marker())
        action = Action(verb="refund", operator=OPERATOR, developer="incomplete-history-test",
                        action_class="money.refund", amount_minor=900, currency="EUR", rail="card",
                        target="shop/new", action_id="refund/incomplete-history", timestamp=AT)
        decision = _engine(store, action_classes=["money.refund"]).check(action)
    assert decision.outcome == DENY
    assert decision.asked_unevaluated_checks == ("counterparty_seen_before",)
    assert decision.verdict == "not_evaluable"


def test_a_record_at_another_taxonomy_is_held_and_not_asked_for(incomplete):
    action = Action(verb="make_purchase", operator=OPERATOR, developer="incomplete-history-test",
                    action_class="money.purchase", amount_minor=900, currency="EUR", rail="card",
                    target="shop/new", action_id="make_purchase/other-taxonomy", timestamp=AT,
                    taxonomy_version="1")
    decision = _engine(incomplete).check(action)
    (seen,) = [c for c in decision.constraints if c.id == "counterparty_seen_before"]
    assert seen.result == "n/a" and "taxonomy_version" in seen.reason
    assert decision.asked_unevaluated_checks == ()
    assert decision.outcome == DENY


# -- live checks, with everyday 0.3.10 -----------------------------------------------


def _envelope(name: str) -> dict:
    return json.loads((EMIT / f"{name}.json").read_text())


def _rules(envelope: dict, pack=PACK) -> tuple[str, dict[str, str]]:
    decision = decide_check_input(pack, envelope).decision
    return decision.outcome, {r.obligation_id: r.result for r in obligation_results(pack, decision.constraints)}


def test_live_an_incomplete_history_with_no_earlier_payment_is_not_checked_and_asks():
    outcome, rules = _rules(_envelope("pay-history-empty-incomplete"))
    assert outcome == ESCALATE
    assert (rules[R02], rules[R06]) == ("n/a", "n/a")


def test_live_under_0_3_9_the_same_purchase_failed_r02_and_r06():
    outcome, rules = _rules(_envelope("pay-history-empty-incomplete"), FROZEN_0_3_9)
    assert outcome == ESCALATE
    assert (rules[R02], rules[R06]) == ("fail", "fail")


def test_live_an_incomplete_history_with_an_earlier_payment_passes():
    _, rules = _rules(_envelope("pay-history-incomplete"))
    assert (rules[R02], rules[R06]) == ("pass", "pass")


def test_live_a_complete_history_with_no_earlier_payment_fails_as_before():
    outcome, rules = _rules(_envelope("pay-history-empty-complete"))
    assert outcome == ESCALATE
    assert (rules[R02], rules[R06]) == ("fail", "fail")


def test_live_a_history_act_whose_item_ref_cannot_be_read_makes_the_history_incomplete():
    envelope = _envelope("pay-history-other-merchant")
    assert envelope["history_scope"]["complete"] is True
    envelope["history"][0]["item_ref"] = None
    _, rules = _rules(envelope)
    assert (rules[R02], rules[R06]) == ("n/a", "n/a")


def test_the_live_history_writes_the_marker_this_check_reads():
    ledger = decide_check_input(PACK, _envelope("pay-history-empty-incomplete")).ledger
    assert [r["asg_payload"][LIVE_HISTORY] for r in ledger if LIVE_HISTORY in r.get("asg_payload", {})][-1] == INCOMPLETE
