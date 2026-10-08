# SPDX-License-Identifier: Apache-2.0
"""counterparty_list: a deny list or an allow list of counterparty
references the user supplies in a policy profile. Every assertion names the
field it reads; the list never lives in the wicket definition."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from agent_action_capsule.canonical import json_digest
from capsule_ledger.ledger import LedgerStore

from capsule_engine.guards import Action, GuardEngine, LocalSigner
from capsule_engine.guards.capsule import ALLOW, DENY, ESCALATE, not_applicable_evidence
from capsule_engine.guards.checks import check_counterparty_list
from capsule_engine.guards.wickets import load_definition_file
from capsule_engine.packs import build_engine, install_pack, load_pack_dir
from capsule_engine.policy import PROFILE_FORMAT, PolicyManifestError, parse_profile
from capsule_engine.policy.errors import MALFORMED_PROFILE, PROFILE_UNKNOWN_PARAMETER

CATALOG = Path(__file__).parent.parent / "capsule_engine" / "guards" / "wickets" / "catalog_defs"
LIST_WICKET = load_definition_file(CATALOG / "counterparty_list.yaml")
EVERYDAY_DIR = Path(__file__).parent.parent / "capsule_engine" / "packs" / "catalog" / "everyday"
SIGNER = LocalSigner(key_id="counterparty-list-test-key", secret=b"counterparty-list-test-fixed-key")

BLOCKED = "shop/example-blocked"
ALLOWED = "shop/example-allowed"
OTHER = "shop/example-other"
ENTRIES = [BLOCKED, "shop/example-second"]


def _action(**overrides) -> Action:
    fields = dict(
        verb="make_purchase",
        operator="household-list-fixture",
        developer="household-assistant@v1",
        action_class="money.purchase",
        amount_minor=1_000,
        currency="USD",
        target=OTHER,
        rail="card",
        recurrence="one_time",
        timestamp="2026-10-08T10:00:00Z",
    )
    fields.update(overrides)
    return Action(**fields)


def _check(action: Action, *, mode: str = "deny", entries: list[str] = ENTRIES):
    return check_counterparty_list(
        action, mode=mode, entries=entries, action_classes=LIST_WICKET.config["action_classes"]
    ).constraint


# -- the definition carries no list ---------------------------------------------


def test_the_definition_ships_an_empty_deny_list_so_the_list_is_the_users():
    assert LIST_WICKET.wicket_id == "counterparty_list/1.0.0"
    assert LIST_WICKET.config["entries"] == []
    assert LIST_WICKET.config["mode"] == "deny"
    assert LIST_WICKET.config["disposition"] == "deny"


# -- the check -------------------------------------------------------------------


def test_deny_mode_fails_a_listed_counterparty_and_names_the_matched_entry():
    out = _check(_action(target=BLOCKED))
    assert out.result == "fail"
    assert out.evidence == {
        "match_field": "target",
        "match_rule": "exact",
        "value": BLOCKED,
        "mode": "deny",
        "list_digest": json_digest(sorted(ENTRIES)),
        "list_size": 2,
        "matched_entry": BLOCKED,
    }


def test_deny_mode_passes_an_unlisted_counterparty_and_records_the_list_it_consulted():
    out = _check(_action(target=OTHER))
    assert out.result == "pass"
    assert out.evidence["matched_entry"] is None
    assert out.evidence["list_digest"] == json_digest(sorted(ENTRIES))
    assert out.evidence["list_size"] == 2
    assert out.evidence["value"] == OTHER


def test_the_list_digest_does_not_depend_on_entry_order():
    a = _check(_action(target=OTHER), entries=ENTRIES)
    b = _check(_action(target=OTHER), entries=list(reversed(ENTRIES)))
    assert a.evidence == b.evidence


def test_allow_mode_fails_an_unlisted_counterparty():
    out = _check(_action(target=OTHER), mode="allow", entries=[ALLOWED])
    assert out.result == "fail"
    assert out.evidence["mode"] == "allow"
    assert out.evidence["matched_entry"] is None


def test_allow_mode_passes_a_listed_counterparty_and_names_the_matched_entry():
    out = _check(_action(target=ALLOWED), mode="allow", entries=[ALLOWED])
    assert out.result == "pass"
    assert out.evidence["matched_entry"] == ALLOWED


def test_matching_is_exact_a_reference_is_not_a_name():
    """No case folding or trimming: ``Shop/Example-Blocked`` is a different
    reference, and treating it as the same would be a guess."""
    assert _check(_action(target=BLOCKED.upper())).result == "pass"


@pytest.mark.parametrize("target", [None, ""])
def test_no_usable_counterparty_reference_is_not_evaluable_and_names_the_field(target):
    out = _check(_action(target=target))
    assert out.result == "n/a"
    assert out.evidence == not_applicable_evidence("counterparty_list", in_scope=True, missing_field="target")


def test_allow_mode_without_a_reference_is_not_evaluable_too():
    out = _check(_action(target=None), mode="allow", entries=[ALLOWED])
    assert out.result == "n/a"
    assert out.evidence == not_applicable_evidence("counterparty_list", in_scope=True, missing_field="target")


def test_outside_its_action_classes_is_out_of_scope():
    out = _check(_action(action_class="info.query", target=BLOCKED))
    assert out.result == "n/a"
    assert out.evidence == not_applicable_evidence("counterparty_list", in_scope=False)


def test_an_unknown_mode_is_refused():
    with pytest.raises(ValueError, match="mode"):
        _check(_action(), mode="block")


# -- the configured disposition, through the engine -------------------------------


def _engine(store, caps_fold, **config) -> GuardEngine:
    return GuardEngine(
        ledger=store,
        caps_fold=caps_fold,
        signer_provider=lambda: SIGNER,
        wickets=(replace(LIST_WICKET, config={**LIST_WICKET.config, **config}),),
    )


def _transfer(target: str) -> Action:
    # money.transfer is the class the taxonomy names an approver for, so an
    # ``ask`` can pause it rather than refuse it.
    return _action(action_class="money.transfer", target=target)


def test_disposition_deny_refuses_a_listed_counterparty(store, caps_fold):
    config = {"entries": [BLOCKED], "disposition": "deny", "action_classes": ["money.transfer"]}
    assert _engine(store, caps_fold, **config).check(_transfer(BLOCKED)).outcome == DENY


def test_disposition_ask_pauses_a_listed_counterparty_for_the_approver(store, caps_fold):
    config = {"entries": [BLOCKED], "disposition": "ask", "action_classes": ["money.transfer"]}
    assert _engine(store, caps_fold, **config).check(_transfer(BLOCKED)).outcome == ESCALATE


def test_disposition_ask_on_a_class_with_no_approver_still_refuses(store, caps_fold):
    """The engine's existing rule: there is nobody to ask, so it denies."""
    engine = _engine(store, caps_fold, entries=[BLOCKED], disposition="ask")
    assert engine.check(_action(target=BLOCKED)).outcome == DENY


def test_disposition_ask_allows_an_unlisted_counterparty(store, caps_fold):
    config = {"entries": [BLOCKED], "disposition": "ask", "action_classes": ["money.transfer"]}
    assert _engine(store, caps_fold, **config).check(_transfer(OTHER)).outcome == ALLOW


def test_an_unknown_disposition_is_refused_when_the_engine_is_built(store, caps_fold):
    with pytest.raises(ValueError, match="disposition"):
        _engine(store, caps_fold, disposition="pause")


# -- the list rides in a policy profile ------------------------------------------


def _pack_with_list():
    """The everyday pack with this wicket added, built here only: no shipped
    pack cites counterparty_list yet."""
    pack = load_pack_dir(EVERYDAY_DIR)
    return replace(pack, constraints=(*pack.constraints, LIST_WICKET))


def _profile(**params):
    return parse_profile(
        {"format": PROFILE_FORMAT, "packs": [{"pack": "asg/everyday", "parameters": {"counterparty_list": params}}]}
    )


def _decide(tmp_path, profile, action):
    installed = install_pack(_pack_with_list(), project_dir=tmp_path / "project", mode="enforce", profile=profile)
    ledger = LedgerStore(tmp_path / "ledger")
    decision = build_engine(installed, ledger=ledger, signer_provider=lambda: SIGNER).check(action)
    ledger.close()
    return installed, decision


def _list_constraint(decision):
    return next(c for c in decision.constraints if c.id == "counterparty_list")


def test_a_profile_deny_list_refuses_a_listed_purchase_and_leaves_the_pack_unchanged(tmp_path):
    profile = _profile(mode="deny", entries=[BLOCKED])
    installed, decision = _decide(tmp_path, profile, _action(target=BLOCKED, amount_minor=100))
    assert decision.outcome == DENY
    assert _list_constraint(decision).evidence["matched_entry"] == BLOCKED
    without = install_pack(_pack_with_list(), project_dir=tmp_path / "bare", mode="enforce")
    assert installed.manifest.wickets == without.manifest.wickets
    assert installed.manifest.packs == without.manifest.packs
    assert installed.manifest.profile_digest == profile.profile_digest()


def test_a_profile_deny_list_passes_an_unlisted_purchase_and_says_it_consulted_the_list(tmp_path):
    _, decision = _decide(tmp_path, _profile(mode="deny", entries=[BLOCKED]), _action(target=OTHER, amount_minor=100))
    out = _list_constraint(decision)
    assert out.result == "pass"
    assert out.evidence["list_digest"] == json_digest([BLOCKED])
    assert out.evidence["list_size"] == 1


def test_a_profile_allow_list_refuses_an_unlisted_purchase(tmp_path):
    _, decision = _decide(tmp_path, _profile(mode="allow", entries=[ALLOWED]), _action(target=OTHER, amount_minor=100))
    assert decision.outcome == DENY
    assert _list_constraint(decision).evidence["mode"] == "allow"


def test_a_profile_purchase_with_no_reference_is_not_evaluable(tmp_path):
    _, decision = _decide(tmp_path, _profile(mode="deny", entries=[BLOCKED]), _action(target=None, amount_minor=100))
    assert _list_constraint(decision).evidence == not_applicable_evidence(
        "counterparty_list", in_scope=True, missing_field="target"
    )


def test_the_profile_entries_are_order_independent():
    a = _profile(mode="deny", entries=[BLOCKED, OTHER])
    b = _profile(mode="deny", entries=[OTHER, BLOCKED])
    assert a.profile_digest() == b.profile_digest()


@pytest.mark.parametrize(
    "params",
    [
        {"entries": [BLOCKED]},  # mode is never assumed
        {"mode": "deny"},  # no list
        {"mode": "block", "entries": [BLOCKED]},
        {"mode": "deny", "entries": [BLOCKED], "disposition": "pause"},
        {"mode": "deny", "entries": [""]},
        {"mode": "deny", "entries": [BLOCKED, BLOCKED]},
        {"mode": "deny", "entries": BLOCKED},
        {"mode": "deny", "entries": [7]},
        {"mode": "deny", "entries": [BLOCKED], "action_classes": ["money.purchase"]},
    ],
)
def test_a_malformed_list_profile_is_refused(params):
    with pytest.raises(PolicyManifestError) as exc:
        _profile(**params)
    assert exc.value.reason == MALFORMED_PROFILE


def test_a_list_profile_for_a_pack_without_the_wicket_fails_closed(tmp_path):
    with pytest.raises(PolicyManifestError) as exc:
        install_pack(
            load_pack_dir(EVERYDAY_DIR), project_dir=tmp_path, mode="enforce",
            profile=_profile(mode="deny", entries=[BLOCKED]),
        )
    assert exc.value.reason == PROFILE_UNKNOWN_PARAMETER
