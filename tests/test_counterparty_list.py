# SPDX-License-Identifier: Apache-2.0
"""counterparty_list: a deny list or an allow list of counterparty
references the user supplies in a policy profile. Every assertion names the
field it reads; the list never lives in the wicket definition.

An entry names the kind of reference it matches: ``target`` (a clear
reference a producer declares) or a sealed fingerprint (``domain``,
``payee``) with the algorithm that made it. The two forms never compare."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import NotRequired, TypedDict

import pytest
from agent_action_capsule.canonical import json_digest
from capsule_ledger.ledger import LedgerStore

from capsule_engine.folds.loader import load_definition_file as load_fold
from capsule_engine.guards import Action, GuardEngine, LocalSigner
from capsule_engine.guards.capsule import ALLOW, DENY, ESCALATE, not_applicable_evidence
from capsule_engine.guards.checks import check_counterparty_list
from capsule_engine.guards.checks.counterparty_list import ListEntry
from capsule_engine.guards.wickets import load_definition_file
from capsule_engine.packs import build_engine, install_pack, load_pack_dir, record_pack_activation
from capsule_engine.policy import PROFILE_FORMAT, PolicyManifestError, parse_profile
from capsule_engine.policy.errors import MALFORMED_PROFILE, PROFILE_UNKNOWN_PARAMETER
from capsule_engine.report.replay import action_for_record, load_disclosed, load_records, replay

PACKAGE_DIR = Path(__file__).parent.parent / "capsule_engine"
CATALOG = PACKAGE_DIR / "guards" / "wickets" / "catalog_defs"
FOLDS = PACKAGE_DIR / "folds" / "catalog_defs"
LIST_WICKET = load_definition_file(CATALOG / "counterparty_list.yaml")
EVERYDAY_DIR = PACKAGE_DIR / "packs" / "catalog" / "everyday"
REFUND = Path(__file__).parent / "fixtures" / "deal-bundles" / "deal-purchase-then-refund.bundle.json"
SIGNER = LocalSigner(key_id="counterparty-list-test-key", secret=b"counterparty-list-test-fixed-key")

BLOCKED = "shop/example-blocked"
ALLOWED = "shop/example-allowed"
OTHER = "shop/example-other"
ALG = "hmac-sha256-deal-key"
PAYEE = "a" * 64
DOMAIN = "b" * 64


def _t(value: str) -> ListEntry:
    return {"kind": "target", "value": value}


def _fp(kind: str, value: str, fp_alg: str = ALG) -> ListEntry:
    return {"kind": kind, "fp_alg": fp_alg, "value": value}


ENTRIES = [_t(BLOCKED), _t("shop/example-second")]


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


def _sealed(ids: dict[str, str], fp_alg: str = ALG) -> Action:
    """An action as a deal check states it: fingerprints, no clear target."""
    return _action(target=None, counterparty_ids=ids, counterparty_fp_alg=fp_alg)


def _check(action: Action, *, mode: str = "deny", entries: list[ListEntry] = ENTRIES):
    return check_counterparty_list(
        action, mode=mode, entries=entries, action_classes=LIST_WICKET.config["action_classes"]
    ).constraint


def _missing(field: str):
    return not_applicable_evidence("counterparty_list", in_scope=True, missing_field=field)


# -- the definition carries no list ---------------------------------------------


def test_the_definition_ships_an_empty_deny_list_so_the_list_is_the_users():
    assert LIST_WICKET.wicket_id == "counterparty_list/1.0.0"
    assert LIST_WICKET.config["entries"] == []
    assert LIST_WICKET.config["mode"] == "deny"
    assert LIST_WICKET.config["disposition"] == "deny"


# -- a clear target ----------------------------------------------------------------


def test_deny_mode_fails_a_listed_target_and_names_the_matched_entry():
    out = _check(_action(target=BLOCKED))
    assert out.result == "fail"
    assert out.evidence == {
        "match_rule": "exact",
        "mode": "deny",
        "list_digest": json_digest(sorted(ENTRIES, key=json_digest)),
        "list_size": 2,
        "read": [{"kind": "target", "value": BLOCKED}],
        "matched_entry": _t(BLOCKED),
    }


def test_deny_mode_passes_an_unlisted_target_and_records_the_list_it_consulted():
    out = _check(_action(target=OTHER))
    assert out.result == "pass"
    assert out.evidence["matched_entry"] is None
    assert out.evidence["list_digest"] == json_digest(sorted(ENTRIES, key=json_digest))
    assert out.evidence["list_size"] == 2
    assert out.evidence["read"] == [{"kind": "target", "value": OTHER}]


def test_the_list_digest_does_not_depend_on_entry_order():
    a = _check(_action(target=OTHER), entries=ENTRIES)
    b = _check(_action(target=OTHER), entries=list(reversed(ENTRIES)))
    assert a.evidence == b.evidence


def test_allow_mode_fails_an_unlisted_target():
    out = _check(_action(target=OTHER), mode="allow", entries=[_t(ALLOWED)])
    assert out.result == "fail"
    assert out.evidence["mode"] == "allow"
    assert out.evidence["matched_entry"] is None


def test_allow_mode_passes_a_listed_target_and_names_the_matched_entry():
    out = _check(_action(target=ALLOWED), mode="allow", entries=[_t(ALLOWED)])
    assert out.result == "pass"
    assert out.evidence["matched_entry"] == _t(ALLOWED)


def test_matching_is_exact_a_reference_is_not_a_name():
    """No case folding or trimming: ``SHOP/EXAMPLE-BLOCKED`` is a different
    reference, and treating it as the same would be a guess."""
    assert _check(_action(target=BLOCKED.upper())).result == "pass"


@pytest.mark.parametrize("target", [None, ""])
def test_no_usable_target_is_not_evaluable_and_names_the_field(target):
    out = _check(_action(target=target))
    assert out.result == "n/a"
    assert out.evidence == _missing("target")


def test_allow_mode_without_a_target_is_not_evaluable_too():
    out = _check(_action(target=None), mode="allow", entries=[_t(ALLOWED)])
    assert out.result == "n/a"
    assert out.evidence == _missing("target")


def test_outside_its_action_classes_is_out_of_scope():
    out = _check(_action(action_class="info.query", target=BLOCKED))
    assert out.result == "n/a"
    assert out.evidence == not_applicable_evidence("counterparty_list", in_scope=False)


def test_an_unknown_mode_is_refused():
    with pytest.raises(ValueError, match="mode"):
        _check(_action(), mode="block")


# -- a sealed fingerprint -----------------------------------------------------------


def test_deny_mode_fails_a_listed_payee_fingerprint_and_names_kind_and_algorithm():
    out = _check(_sealed({"payee": PAYEE}), entries=[_fp("payee", PAYEE)])
    assert out.result == "fail"
    assert out.evidence["matched_entry"] == {"kind": "payee", "fp_alg": ALG, "value": PAYEE}
    assert out.evidence["read"] == [{"kind": "payee", "fp_alg": ALG, "value": PAYEE}]


def test_deny_mode_passes_an_unlisted_payee_fingerprint():
    out = _check(_sealed({"payee": "c" * 64}), entries=[_fp("payee", PAYEE)])
    assert out.result == "pass"
    assert out.evidence["matched_entry"] is None
    assert out.evidence["list_size"] == 1


def test_a_fingerprint_matches_only_its_own_kind():
    """The same bytes under ``domain`` are not the payee."""
    out = _check(_sealed({"payee": PAYEE, "domain": DOMAIN}), entries=[_fp("domain", PAYEE)])
    assert out.result == "pass"


def test_a_clear_target_and_a_fingerprint_never_compare():
    action = _action(target=PAYEE, counterparty_ids={"payee": PAYEE}, counterparty_fp_alg=ALG)
    assert _check(action, entries=[_t("unrelated")]).result == "pass"
    assert _check(action, entries=[_fp("payee", "unrelated")]).result == "pass"


def test_an_algorithm_mismatch_is_not_evaluable_and_names_fp_alg():
    out = _check(_sealed({"payee": PAYEE}, fp_alg="sha256-unkeyed"), entries=[_fp("payee", PAYEE)])
    assert out.result == "n/a"
    assert out.evidence == _missing("counterparty.fp_alg")


def test_a_kind_the_action_does_not_seal_is_not_evaluable_and_names_it():
    out = _check(_sealed({"payee": PAYEE}), entries=[_fp("domain", DOMAIN)])
    assert out.result == "n/a"
    assert out.evidence == _missing("counterparty.ids.domain")


def test_no_counterparty_block_is_not_evaluable_and_names_it():
    out = _check(_action(target=None), entries=[_fp("payee", PAYEE)])
    assert out.result == "n/a"
    assert out.evidence == _missing("counterparty")


def test_an_empty_list_with_no_reference_at_all_is_not_evaluable():
    out = _check(_action(target=None), entries=[])
    assert out.result == "n/a"
    assert out.evidence == _missing("counterparty")


def test_a_list_mixing_kinds_needs_every_kind_it_names():
    """A target entry beside a payee entry: a deal check carries no target,
    so the list cannot be fully consulted and nothing passes silently."""
    out = _check(_sealed({"payee": "c" * 64}), entries=[_fp("payee", PAYEE), _t(BLOCKED)])
    assert out.result == "n/a"
    assert out.evidence == _missing("target")


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
    config = {"entries": [_t(BLOCKED)], "disposition": "deny", "action_classes": ["money.transfer"]}
    assert _engine(store, caps_fold, **config).check(_transfer(BLOCKED)).outcome == DENY


def test_disposition_ask_pauses_a_listed_counterparty_for_the_approver(store, caps_fold):
    config = {"entries": [_t(BLOCKED)], "disposition": "ask", "action_classes": ["money.transfer"]}
    assert _engine(store, caps_fold, **config).check(_transfer(BLOCKED)).outcome == ESCALATE


def test_disposition_ask_on_a_class_with_no_approver_still_refuses(store, caps_fold):
    """The engine's existing rule: there is nobody to ask, so it denies."""
    engine = _engine(store, caps_fold, entries=[_t(BLOCKED)], disposition="ask")
    assert engine.check(_action(target=BLOCKED)).outcome == DENY


def test_disposition_ask_allows_an_unlisted_counterparty(store, caps_fold):
    config = {"entries": [_t(BLOCKED)], "disposition": "ask", "action_classes": ["money.transfer"]}
    assert _engine(store, caps_fold, **config).check(_transfer(OTHER)).outcome == ALLOW


def test_an_unknown_disposition_is_refused_when_the_engine_is_built(store, caps_fold):
    with pytest.raises(ValueError, match="disposition"):
        _engine(store, caps_fold, disposition="pause")


# -- a real deal bundle, through the replay path -----------------------------------


class SealedCounterparty(TypedDict):
    """``x-deal-v0.counterparty`` as a deal check seals it."""

    fp_alg: str
    ids: dict[str, str]


class CheckBody(TypedDict):
    action: str


class DealBlock(TypedDict):
    record_type: str
    counterparty: NotRequired[SealedCounterparty]
SealedRecord = TypedDict("SealedRecord", {"body": CheckBody, "x-deal-v0": DealBlock})


class BundleCapsule(TypedDict):
    capsule_id: str


def _bundle_checks() -> dict[str, tuple[BundleCapsule, SealedRecord]]:
    """Each deal check in the refund bundle by its checked action: (capsule,
    the record it sealed)."""
    value = json.loads(REFUND.read_text())
    out = {}
    for capsule in value["records"]:
        record = value["disclosures"][capsule["capsule_id"]]["agent_input"]
        if record["x-deal-v0"]["record_type"] == "check":
            out[record["body"]["action"]] = (capsule, record)
    return out


def _pay_counterparty() -> SealedCounterparty:
    _, record = _bundle_checks()["pay"]
    return record["x-deal-v0"]["counterparty"]


def _replay_list(mode: str, entries: list[ListEntry]) -> dict[str, tuple[str, object]]:
    """Each deal check's (decision outcome, counterparty_list constraint),
    replayed with the list wicket configured as given."""
    wicket = replace(LIST_WICKET, config={**LIST_WICKET.config, "mode": mode, "entries": entries,
                                          "action_classes": ["money.purchase", "money.refund"]})
    result = replay(
        load_records([REFUND]),
        caps_fold=load_fold(FOLDS / "spend.weekly.yaml"),
        disclosed=load_disclosed([REFUND]),
        wickets=(wicket,),
    )
    checks = {capsule["capsule_id"]: action for action, (capsule, _) in _bundle_checks().items()}
    out = {}
    for sourced in result.decisions:
        action = checks.get(sourced.record["capsule_id"])
        if action is not None:
            listed = next(c for c in sourced.decision.constraints if c.id == "counterparty_list")
            out[action] = (sourced.decision.outcome, listed)
    return out


def test_the_deal_bridge_carries_the_checks_sealed_counterparty_ids():
    capsule, record = _bundle_checks()["pay"]
    action = action_for_record(capsule, record)
    sealed = record["x-deal-v0"]["counterparty"]
    assert action.counterparty_ids == sealed["ids"]
    assert action.counterparty_fp_alg == sealed["fp_alg"]
    assert action.target is None


def test_a_real_pay_check_whose_payee_is_on_a_deny_list_is_denied_on_replay():
    sealed = _pay_counterparty()
    entry = _fp("payee", sealed["ids"]["payee"], sealed["fp_alg"])
    outcome, listed = _replay_list("deny", [entry])["pay"]
    assert outcome == DENY
    assert listed.result == "fail"
    assert listed.evidence["matched_entry"] == entry


def test_the_same_pay_check_unlisted_passes_and_says_the_list_was_consulted():
    sealed = _pay_counterparty()
    entry = _fp("payee", "c" * 64, sealed["fp_alg"])
    outcome, listed = _replay_list("deny", [entry])["pay"]
    assert outcome == ALLOW
    assert listed.result == "pass"
    assert listed.evidence["list_digest"] == json_digest([entry])
    assert listed.evidence["read"] == [{"kind": "payee", "fp_alg": sealed["fp_alg"], "value": sealed["ids"]["payee"]}]


def test_a_real_check_with_no_counterparty_block_is_not_evaluable_naming_it():
    _, record = _bundle_checks()["cancel"]
    assert "counterparty" not in record["x-deal-v0"]
    _, listed = _replay_list("deny", [_fp("payee", "c" * 64)])["cancel"]
    assert listed.result == "n/a"
    assert listed.evidence == _missing("counterparty")


def test_a_domain_list_on_a_real_pay_check_is_not_evaluable_because_the_check_seals_no_domain():
    """Today's pay check seals only the payee fingerprint (the domain is on
    the deal's baseline record, which is not an action). A domain list
    must say it could not be consulted, never pass."""
    _, listed = _replay_list("deny", [_fp("domain", "c" * 64)])["pay"]
    assert listed.result == "n/a"
    assert listed.evidence == _missing("counterparty.ids.domain")


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


def _decide(tmp_path, profile, action, *, activate=True):
    """Install with ``profile`` and decide ``action``. A profile applies only
    once a signed activation binds it (``policy/limits.py``), so the install
    is activated an hour before the action unless ``activate`` is false."""
    installed = install_pack(_pack_with_list(), project_dir=tmp_path / "project", mode="enforce", profile=profile)
    ledger = LedgerStore(tmp_path / "ledger")
    if activate:
        record_pack_activation(installed, ledger=ledger, operator="household-list-fixture", developer="ops",
                               signer=SIGNER, timestamp="2026-10-08T09:00:00Z")
    engine = build_engine(installed, ledger=ledger, signer_provider=lambda: SIGNER, clock=lambda: action.timestamp)
    decision = engine.check(action)
    ledger.close()
    return installed, decision


def _list_constraint(decision):
    return next(c for c in decision.constraints if c.id == "counterparty_list")


def _failed(decision) -> list[str]:
    return [c.id for c in decision.constraints if c.result == "fail"]


def test_a_profile_deny_list_refuses_a_listed_purchase_and_leaves_the_pack_unchanged(tmp_path):
    profile = _profile(mode="deny", entries=[_fp("payee", PAYEE)])
    installed, decision = _decide(tmp_path, profile, _sealed({"payee": PAYEE}))
    assert decision.outcome == DENY
    assert _failed(decision) == ["counterparty_list"]
    assert _list_constraint(decision).evidence["matched_entry"] == _fp("payee", PAYEE)
    without = install_pack(_pack_with_list(), project_dir=tmp_path / "bare", mode="enforce")
    assert installed.manifest.wickets == without.manifest.wickets
    assert installed.manifest.packs == without.manifest.packs
    assert installed.manifest.profile_digest == profile.profile_digest()


def test_a_profile_deny_list_passes_an_unlisted_purchase_and_says_it_consulted_the_list(tmp_path):
    _, decision = _decide(tmp_path, _profile(mode="deny", entries=[_t(BLOCKED)]), _action(target=OTHER, amount_minor=100))
    assert decision.outcome == ALLOW
    out = _list_constraint(decision)
    assert out.result == "pass"
    assert out.evidence["list_digest"] == json_digest([_t(BLOCKED)])
    assert out.evidence["list_size"] == 1


def test_a_profile_allow_list_refuses_an_unlisted_purchase(tmp_path):
    _, decision = _decide(tmp_path, _profile(mode="allow", entries=[_t(ALLOWED)]), _action(target=OTHER, amount_minor=100))
    assert decision.outcome == DENY
    assert _failed(decision) == ["counterparty_list"]
    assert _list_constraint(decision).evidence["mode"] == "allow"


def test_a_profile_purchase_with_no_reference_is_not_evaluable(tmp_path):
    _, decision = _decide(tmp_path, _profile(mode="deny", entries=[_t(BLOCKED)]), _action(target=None, amount_minor=100))
    assert _list_constraint(decision).evidence == _missing("target")
    assert "policy_binding" not in [c.id for c in decision.constraints]


def test_a_list_profile_no_activation_binds_is_denied_by_policy_binding_not_the_list(tmp_path):
    # The same deny-list profile and an unlisted purchase that it would pass:
    # unbound, the decision is denied before any rule runs.
    _, decision = _decide(tmp_path, _profile(mode="deny", entries=[_t(BLOCKED)]), _action(target=OTHER, amount_minor=100),
                          activate=False)
    assert decision.outcome == DENY
    assert [(c.id, c.result) for c in decision.constraints] == [("policy_binding", "fail")]
    assert "(profile_unbound)" in decision.constraints[0].reason


def test_the_profile_entries_are_order_independent():
    a = _profile(mode="deny", entries=[_t(BLOCKED), _fp("payee", PAYEE)])
    b = _profile(mode="deny", entries=[_fp("payee", PAYEE), _t(BLOCKED)])
    assert a.profile_digest() == b.profile_digest()


@pytest.mark.parametrize(
    "params",
    [
        {"entries": [_t(BLOCKED)]},  # mode is never assumed
        {"mode": "deny"},  # no list
        {"mode": "block", "entries": [_t(BLOCKED)]},
        {"mode": "deny", "entries": [_t(BLOCKED)], "disposition": "pause"},
        {"mode": "deny", "entries": [_t("")]},
        {"mode": "deny", "entries": [_t(BLOCKED), _t(BLOCKED)]},
        {"mode": "deny", "entries": _t(BLOCKED)},
        {"mode": "deny", "entries": [BLOCKED]},  # a bare string names no kind
        {"mode": "deny", "entries": [{"kind": "name", "fp_alg": ALG, "value": PAYEE}]},  # a name is not an id
        {"mode": "deny", "entries": [{"kind": "payee", "value": PAYEE}]},  # a fingerprint needs its algorithm
        {"mode": "deny", "entries": [{"kind": "target", "fp_alg": ALG, "value": BLOCKED}]},  # a clear value has none
        {"mode": "deny", "entries": [{"kind": "payee", "fp_alg": "", "value": PAYEE}]},
        {"mode": "deny", "entries": [{"kind": "payee", "fp_alg": ALG, "value": 7}]},
        {"mode": "deny", "entries": [_t(BLOCKED)], "action_classes": ["money.purchase"]},
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
            profile=_profile(mode="deny", entries=[_t(BLOCKED)]),
        )
    assert exc.value.reason == PROFILE_UNKNOWN_PARAMETER
