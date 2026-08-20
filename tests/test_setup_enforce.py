# SPDX-License-Identifier: Apache-2.0
import io

import pytest

from capsule_engine.guards.action import Action
from capsule_engine.setup.confirm import confirm_accept
from capsule_engine.setup.declarations import DeclarationStore
from capsule_engine.setup.enforce import (
    EnforceError,
    EnforceStateStore,
    dispatch,
    historical_actions_for,
    promote,
    reproduce_refusal,
    reproduction_command,
    run_shadow_report,
)
from capsule_engine.setup.observe import ObserveRecorder
from capsule_engine.setup.propose import persist_proposals, propose_from_ledger


def _observe(store, signer, events):
    recorder = ObserveRecorder(
        ledger=store, signer=signer, operator="op", developer="dev", heartbeat_every=0, heartbeat_stream=io.StringIO()
    )
    return recorder.run(events)


def _accepted_store(store, signer, tmp_path, events):
    _observe(store, signer, events)
    proposal_set = propose_from_ledger(store)
    decl_store = DeclarationStore(tmp_path)
    persist_proposals(proposal_set, decl_store)
    confirm_accept("outcome.remediation_confirmed", store=decl_store, ledger=store, signer=signer, operator="op", developer="dev")
    return decl_store


DISPATCH_EVENTS = [
    {"kind": "dispatch", "dispatch_id": "d1", "action_class": "remediation", "tool": "remediate"},
    {"kind": "confirmation", "commitment_ref": "d1", "status": "confirmed"},
]


def test_historical_actions_for_replays_observed_dispatches(store, signer):
    _observe(store, signer, DISPATCH_EVENTS)
    actions = historical_actions_for(store, "remediation")
    assert len(actions) == 1
    assert actions[0].verb == "remediation"


def test_run_shadow_report_is_pure_and_appends_nothing(store, signer, tmp_path):
    decl_store = _accepted_store(store, signer, tmp_path, DISPATCH_EVENTS)
    before = sum(1 for _ in store.scan())
    actions = historical_actions_for(store, "remediation")
    report = run_shadow_report("outcome.remediation_confirmed", actions, store=decl_store)
    after = sum(1 for _ in store.scan())
    assert after == before
    assert report.total == 1
    assert report.would_fail_count == 0


def test_shadow_report_flags_a_verb_outside_allowed_actions(store, signer, tmp_path):
    decl_store = _accepted_store(store, signer, tmp_path, DISPATCH_EVENTS)
    rogue = Action(verb="not_remediation", operator="op", developer="dev")
    report = run_shadow_report("outcome.remediation_confirmed", [rogue], store=decl_store)
    assert report.would_fail_count == 1
    assert report.results[0].would_pass is False


def test_promote_requires_accepted_state(store, signer, tmp_path):
    _observe(store, signer, DISPATCH_EVENTS)
    proposal_set = propose_from_ledger(store)
    decl_store = DeclarationStore(tmp_path)
    persist_proposals(proposal_set, decl_store)
    enforce_state = EnforceStateStore(tmp_path)
    actions = historical_actions_for(store, "remediation")
    report = run_shadow_report("outcome.remediation_confirmed", actions, store=decl_store)
    with pytest.raises(EnforceError, match="not accepted"):
        promote("outcome.remediation_confirmed", shadow_report=report, store=decl_store, enforce_state=enforce_state, ledger=store, signer=signer, operator="op", developer="dev")


def test_promote_flips_enforce_state_and_appends_a_capsule(store, signer, tmp_path):
    decl_store = _accepted_store(store, signer, tmp_path, DISPATCH_EVENTS)
    enforce_state = EnforceStateStore(tmp_path)
    assert enforce_state.mode("outcome.remediation_confirmed") == "shadow"
    actions = historical_actions_for(store, "remediation")
    report = run_shadow_report("outcome.remediation_confirmed", actions, store=decl_store)
    capsule = promote("outcome.remediation_confirmed", shadow_report=report, store=decl_store, enforce_state=enforce_state, ledger=store, signer=signer, operator="op", developer="dev")
    assert store.fetch(capsule["capsule_id"]) is not None
    assert enforce_state.mode("outcome.remediation_confirmed") == "enforced"


def test_dispatch_refuses_while_still_in_shadow_mode(store, signer, tmp_path):
    decl_store = _accepted_store(store, signer, tmp_path, DISPATCH_EVENTS)
    enforce_state = EnforceStateStore(tmp_path)
    action = Action(verb="remediation", operator="op", developer="dev")
    with pytest.raises(EnforceError, match="shadow mode"):
        dispatch("outcome.remediation_confirmed", action, store=decl_store, enforce_state=enforce_state, ledger=store, signer=signer, setup_dir=tmp_path)


def _promoted(store, signer, tmp_path):
    decl_store = _accepted_store(store, signer, tmp_path, DISPATCH_EVENTS)
    enforce_state = EnforceStateStore(tmp_path)
    actions = historical_actions_for(store, "remediation")
    report = run_shadow_report("outcome.remediation_confirmed", actions, store=decl_store)
    promote("outcome.remediation_confirmed", shadow_report=report, store=decl_store, enforce_state=enforce_state, ledger=store, signer=signer, operator="op", developer="dev")
    return decl_store, enforce_state


def test_dispatch_allows_a_verb_within_the_plan(store, signer, tmp_path):
    decl_store, enforce_state = _promoted(store, signer, tmp_path)
    action = Action(verb="remediation", operator="op", developer="dev")
    result = dispatch("outcome.remediation_confirmed", action, store=decl_store, enforce_state=enforce_state, ledger=store, signer=signer, setup_dir=tmp_path)
    assert result.passed is True
    assert result.reproduction_command is None


def test_dispatch_denies_a_verb_outside_the_plan_and_ships_a_reproduction_command(store, signer, tmp_path):
    decl_store, enforce_state = _promoted(store, signer, tmp_path)
    action = Action(verb="not_remediation", operator="op", developer="dev")
    result = dispatch("outcome.remediation_confirmed", action, store=decl_store, enforce_state=enforce_state, ledger=store, signer=signer, setup_dir=tmp_path)
    assert result.passed is False
    assert result.reproduction_command == reproduction_command(result.capsule["capsule_id"], setup_dir=tmp_path)
    assert result.reproduction_command.endswith(f"--declarations {tmp_path}")
    assert str(tmp_path / "declarations") not in result.reproduction_command


def test_reproduction_command_does_not_double_nest_the_declarations_path(tmp_path):
    """The regression this proves against: ``setup_dir`` must be the
    ``capsule setup init`` root, never ``DeclarationStore.directory``
    (already one level deeper) -- passing the latter here would double the
    ``declarations`` suffix when ``DeclarationStore`` appends its own."""
    cmd = reproduction_command("cap-123", setup_dir=tmp_path)
    assert cmd == f"capsule verify cap-123 --refusal --declarations {tmp_path}"
    assert str(tmp_path / "declarations") not in cmd


def test_reproduce_refusal_matches_a_denied_dispatch(store, signer, tmp_path):
    decl_store, enforce_state = _promoted(store, signer, tmp_path)
    action = Action(verb="not_remediation", operator="op", developer="dev")
    result = dispatch("outcome.remediation_confirmed", action, store=decl_store, enforce_state=enforce_state, ledger=store, signer=signer, setup_dir=tmp_path)
    reproduction = reproduce_refusal(result.capsule["capsule_id"], ledger=store, store=decl_store)
    assert reproduction.matches is True
    assert reproduction.original_decision != "accept"
    assert reproduction.outcome_id == "outcome.remediation_confirmed"


def test_reproduce_refusal_matches_an_allowed_dispatch_too(store, signer, tmp_path):
    decl_store, enforce_state = _promoted(store, signer, tmp_path)
    action = Action(verb="remediation", operator="op", developer="dev")
    result = dispatch("outcome.remediation_confirmed", action, store=decl_store, enforce_state=enforce_state, ledger=store, signer=signer, setup_dir=tmp_path)
    reproduction = reproduce_refusal(result.capsule["capsule_id"], ledger=store, store=decl_store)
    assert reproduction.matches is True
    assert reproduction.original_decision == "accept"


def test_reproduce_refusal_rejects_a_capsule_from_outside_this_mechanism(store, signer, tmp_path):
    decl_store, _ = _promoted(store, signer, tmp_path)
    from capsule_engine.guards.capsule import build_event_capsule

    foreign = build_event_capsule(operator="op", developer="dev", signer=signer, event="unrelated.event", detail={})
    store.append(foreign, consequential=False)
    with pytest.raises(EnforceError, match="does not disclose"):
        reproduce_refusal(foreign["capsule_id"], ledger=store, store=decl_store)


def test_enforce_state_store_outcome_id_with_slash_does_not_collide(tmp_path):
    enforce_state = EnforceStateStore(tmp_path)
    enforce_state.set_mode("a/b", "enforced")
    enforce_state.set_mode("a__b", "shadow")
    assert enforce_state.mode("a/b") == "enforced"
    assert enforce_state.mode("a__b") == "shadow"
