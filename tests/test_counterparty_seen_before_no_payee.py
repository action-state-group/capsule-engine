# SPDX-License-Identifier: Apache-2.0
"""counterparty_seen_before/4.0.0: a purchase that names no payee asks.

Under version 3 an in-scope action with no ``target`` was ``n/a`` naming
``target``. ``n/a`` never asks, so under everyday 0.3.4 a typed check sealing
no counterparty was allowed while the same check naming a new payee asked.
Version 4 configures ``missing_target: unseen``: such an action fails as a
first-time counterparty with the reason "no payee named", so it asks wherever
a new payee asks. The fold is unchanged, version 3 and everyday 0.3.4 stay as
they were, and classes outside the wicket's ``action_classes`` are still out
of scope.
"""
from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path
from typing import TypedDict

import pytest
from agent_action_capsule import json_digest
from capsule_ledger.ledger import LedgerStore

from capsule_engine.guards import Action, LocalSigner
from capsule_engine.guards.capsule import ALLOW, ESCALATE
from capsule_engine.guards.checks import (
    CONFIGURED_CHECKS,
    RUNNABLE_CHECKS,
    check_counterparty_seen_before,
    check_recipient_seen_before,
    seen_before_fold,
)
from capsule_engine.guards.classes import TAXONOMY_VERSION
from capsule_engine.guards.wickets import load_definition_file as load_wicket
from capsule_engine.guards.wickets.retired import retired_entry
from capsule_engine.packs import build_engine, install_pack, load_pack_dir
from capsule_engine.packs.obligation_results import obligation_results
from capsule_engine.packs.schema import PackDefinition

REPO = Path(__file__).parent.parent
WICKETS = REPO / "capsule_engine" / "guards" / "wickets" / "catalog_defs"
PACK = load_pack_dir(REPO / "capsule_engine" / "packs" / "catalog" / "everyday")
FROZEN_0_3_4 = REPO / "tests" / "fixtures" / "packs" / "everyday-0.3.4"
FROZEN_0_3_4_FILE_SHA256 = "410d091a6725650222fffeba1550516fdf74c097e6293d3e5655e2e95e7b3646"
V3_WICKET_DIGEST = "9cfab71d142ca7af8912218f4c4ea194f60fb02784d8536233cb7e18eddc24a8"
V4_WICKET_DIGEST = "c7c20d9e57cb0a75184b9c6c4d037c9bea813a7b1105c581defd4481acf964b0"
R02, R06 = "r02-ordinary-purchase", "r06-new-merchant"
OPERATOR = "household-no-payee"
DEAL = "deal-cccccccccccccccc"
AT = "2026-10-09T12:00:00Z"


# -- the definitions -------------------------------------------------------------


def test_v4_is_v3_with_missing_target_unseen_and_v3_is_untouched():
    v3 = load_wicket(WICKETS / "counterparty_seen_before.v3.yaml")
    v4 = load_wicket(WICKETS / "counterparty_seen_before.v4.yaml")
    assert (v3.definition_digest(), v4.definition_digest()) == (V3_WICKET_DIGEST, V4_WICKET_DIGEST)
    assert v4.config == {**v3.config, "missing_target": "unseen"}
    assert "no payee named" in (v4.semantics or "")
    assert retired_entry(v3.wicket_id, V3_WICKET_DIGEST) is None


def test_everyday_0_3_5_cites_v4_and_0_3_4_is_kept_citing_v3():
    cited = {c.wicket_id: c.definition_digest() for c in PACK.constraints}
    assert PACK.pack_id == "asg/everyday/0.3.5"
    assert cited["counterparty_seen_before/4.0.0"] == V4_WICKET_DIGEST
    assert "counterparty_seen_before/3.0.0" not in cited
    assert hashlib.sha256((FROZEN_0_3_4 / "pack.yaml").read_bytes()).hexdigest() == FROZEN_0_3_4_FILE_SHA256
    frozen = load_pack_dir(FROZEN_0_3_4)
    assert {c.wicket_id: c.definition_digest() for c in frozen.constraints}["counterparty_seen_before/3.0.0"] == V3_WICKET_DIGEST


def test_0_3_5_changes_nothing_else_0_3_4_cites():
    frozen = {c.wicket_id: c.definition_digest() for c in load_pack_dir(FROZEN_0_3_4).constraints}
    cited = {c.wicket_id: c.definition_digest() for c in PACK.constraints}
    del frozen["counterparty_seen_before/3.0.0"], cited["counterparty_seen_before/4.0.0"]
    assert cited == frozen
    assert {f.fold_id: f.definition_digest() for f in PACK.folds} == {
        f.fold_id: f.definition_digest() for f in load_pack_dir(FROZEN_0_3_4).folds
    }


# -- the check -------------------------------------------------------------------


def _purchase(target: str | None, *, action_class: str = "money.purchase", minute: int = 0) -> Action:
    return Action(verb="make_purchase", operator=OPERATOR, developer="no-payee-test", action_class=action_class,
                  amount_minor=900, currency="EUR", rail="card", target=target,
                  action_id=f"make_purchase/no-payee-{minute}", timestamp=f"2026-10-09T12:{minute:02d}:00Z")


def _wicket_check(version: str, action: Action, ledger):
    config = load_wicket(WICKETS / f"counterparty_seen_before.{version}.yaml").config
    return CONFIGURED_CHECKS["counterparty_seen_before"](action, ledger, config).constraint


@pytest.fixture
def empty_ledger(tmp_path):
    store = LedgerStore(tmp_path / "ledger")
    yield store
    store.close()


def test_v4_fails_an_in_scope_action_naming_no_target(empty_ledger):
    out = _wicket_check("v4", _purchase(None), empty_ledger)
    assert out.result == "fail"
    assert out.reason.startswith("no payee named")
    assert out.evidence == {"missing_field": "target", "operator": OPERATOR, "seen_before": False}


@pytest.mark.parametrize("blank", ["", "   "])
def test_v4_fails_a_blank_target_as_naming_no_payee(empty_ledger, blank):
    out = _wicket_check("v4", _purchase(blank), empty_ledger)
    assert (out.result, out.reason.startswith("no payee named")) == ("fail", True)
    assert out.evidence["missing_field"] == "target"


def test_v3_records_the_same_action_n_a_naming_target(empty_ledger):
    out = _wicket_check("v3", _purchase(None), empty_ledger)
    assert out.result == "n/a"
    assert (out.evidence["in_scope"], out.evidence["missing_field"]) == (True, "target")


def test_v4_leaves_a_class_it_is_not_configured_for_out_of_scope(empty_ledger):
    out = _wicket_check("v4", _purchase(None, action_class="comms.external"), empty_ledger)
    assert out.result == "n/a"
    assert (out.evidence["in_scope"], out.evidence["missing_field"]) == (False, None)


def test_v4_reads_an_action_naming_a_target_as_v3_does(empty_ledger):
    v3, v4 = (_wicket_check(v, _purchase("shop/new"), empty_ledger) for v in ("v3", "v4"))
    assert (v3.result, v3.reason, v3.evidence) == (v4.result, v4.reason, v4.evidence)
    assert v4.result == "fail"


def test_recipient_seen_before_still_records_no_target_n_a(empty_ledger):
    config = load_wicket(WICKETS / "recipient_seen_before.yaml").config
    out = check_recipient_seen_before(
        _purchase(None, action_class="comms.external"), empty_ledger,
        definition=seen_before_fold(config["fold_id"], config["fold_digest"]), action_classes=["comms.external"],
    ).constraint
    assert (out.result, out.evidence["missing_field"]) == ("n/a", "target")


def test_an_unknown_missing_target_mode_is_refused(empty_ledger):
    config = load_wicket(WICKETS / "counterparty_seen_before.v4.yaml").config
    with pytest.raises(ValueError, match="missing_target"):
        check_counterparty_seen_before(
            _purchase(None), empty_ledger, definition=seen_before_fold(config["fold_id"], config["fold_digest"]),
            action_classes=config["action_classes"], missing_target="pass",
        )


# -- live, under everyday 0.3.5 ------------------------------------------------


def _rules(pack: PackDefinition, decision) -> dict[str, tuple[str, str | None]]:
    return {r.obligation_id: (r.result, r.reason) for r in obligation_results(pack, decision.constraints)}


@pytest.fixture
def live(tmp_path):
    """An engine on everyday 0.3.5 whose ledger holds one accepted payment
    to ``shop/known``, which makes that merchant seen."""
    store = LedgerStore(tmp_path / "ledger")
    signer = LocalSigner(key_id="no-payee-test-key", secret=b"no-payee-test-fixed-key")
    installed = install_pack(PACK, project_dir=tmp_path / "project", mode="observe")
    engine = build_engine(installed, ledger=store, signer_provider=lambda: signer)
    known = Action(verb="make_payment", operator=OPERATOR, developer="no-payee-test", action_class="money.transfer",
                   amount_minor=700, currency="EUR", rail="card", target="shop/known", recurrence="one_time",
                   counterparty_account_ref="acct-ref-known-1", action_id="make_payment/no-payee-known",
                   timestamp="2026-10-09T11:59:00Z")
    assert engine.check(known).outcome == ALLOW
    yield engine
    store.close()


def test_live_a_purchase_naming_no_payee_asks_citing_r06(live):
    decision = live.check(_purchase(None, minute=1), dry_run=True)
    assert decision.outcome == ESCALATE
    assert [c.id for c in decision.constraints if c.result == "fail"] == ["counterparty_seen_before"]
    rules = _rules(PACK, decision)
    assert rules[R06][0] == rules[R02][0] == "fail"
    assert rules[R06][1].startswith("no payee named")


def test_live_an_earlier_blank_target_act_never_makes_a_blank_target_purchase_a_repeat(live):
    blank = Action(verb="make_payment", operator=OPERATOR, developer="no-payee-test", action_class="money.transfer",
                   amount_minor=700, currency="EUR", rail="card", target="", recurrence="one_time",
                   counterparty_account_ref="acct-ref-blank-1", action_id="make_payment/no-payee-blank",
                   timestamp="2026-10-09T12:04:00Z")
    assert live.check(blank).outcome == ALLOW
    decision = live.check(_purchase("", minute=5), dry_run=True)
    assert decision.outcome == ESCALATE
    assert _rules(PACK, decision)[R06][1].startswith("no payee named")


def test_live_a_purchase_from_a_payee_seen_before_passes(live):
    decision = live.check(_purchase("shop/known", minute=2), dry_run=True)
    assert decision.outcome == ALLOW
    assert _rules(PACK, decision)[R06][0] == "pass"


def test_live_a_purchase_from_a_new_payee_asks_as_before(live):
    decision = live.check(_purchase("shop/new", minute=3), dry_run=True)
    assert decision.outcome == ESCALATE
    result, reason = _rules(PACK, decision)[R06]
    assert (result, reason.startswith("first-time counterparty")) == ("fail", True)


# -- replay of a typed check sealing no counterparty -----------------------------


class Counterparty(TypedDict):
    fp_alg: str
    ids: dict[str, str]


class CheckBody(TypedDict, total=False):
    action: str
    action_class: str
    taxonomy_version: str
    currency: str
    amount_minor: int
    spend_minor: int
    counterparty: Counterparty


class TypedCheck(TypedDict):
    """capsulectl's typed header around a check body."""

    type: str
    canonicalization: str
    chain_id: str
    seq: int
    at: str
    body: CheckBody


class BoundCapsule(TypedDict):
    capsule_id: str
    action_id: str
    action_type: str
    operator: str
    developer: str
    timestamp: str
    model_attestation: dict[str, dict[str, str]]


def _typed(payee: str | None) -> tuple[BoundCapsule, TypedCheck]:
    """A typed ``money.purchase`` check, sealing ``payee`` in its body or no
    counterparty when ``payee`` is ``None``."""
    body: CheckBody = {"action": "pay", "action_class": "money.purchase", "taxonomy_version": TAXONOMY_VERSION,
                       "currency": "USD", "amount_minor": 4_500, "spend_minor": 4_500}
    if payee is not None:
        body["counterparty"] = {"fp_alg": "hmac-sha256-chain-key", "ids": {"payee": payee}}
    record: TypedCheck = {"type": "proposed-action/v0", "canonicalization": "jcs", "chain_id": DEAL, "seq": 1,
                          "at": AT, "body": body}
    capsule: BoundCapsule = {
        "capsule_id": f"{1:064x}", "action_id": f"{DEAL}/1", "action_type": "fyi", "operator": OPERATOR,
        "developer": "capsulectl-deal", "timestamp": AT,
        "model_attestation": {"compute_attestation": {"agent_input_digest": json_digest(record)}},
    }
    return capsule, record


def _replay(pack: PackDefinition, check: tuple[BoundCapsule, TypedCheck]):
    from capsule_engine.report.replay import replay

    capsule, record = check
    with tempfile.TemporaryDirectory() as tmp:
        installed = install_pack(pack, project_dir=Path(tmp) / "replay-project", mode="observe")
        resolved = installed.resolved
        result = replay([capsule], caps_fold=resolved.caps_fold(), caps_minor=resolved.caps_minor(),
                        manifest_digest=resolved.manifest_digest, disclosed={capsule["capsule_id"]: record},
                        wickets=resolved.configured_wickets(RUNNABLE_CHECKS), pack=installed.pack)
    (sourced,) = result.decisions
    return sourced.decision


def test_replay_a_typed_purchase_sealing_no_counterparty_asks_citing_r06():
    decision = _replay(PACK, _typed(None))
    assert decision.outcome == ESCALATE
    assert [c.id for c in decision.constraints if c.result == "fail"] == ["counterparty_seen_before"]
    result, reason = _rules(PACK, decision)[R06]
    assert (result, reason.startswith("no payee named")) == ("fail", True)


def test_replay_a_typed_purchase_naming_a_new_payee_asks_as_before():
    decision = _replay(PACK, _typed("a" * 64))
    assert decision.outcome == ESCALATE
    result, reason = _rules(PACK, decision)[R06]
    assert (result, reason.startswith("first-time counterparty")) == ("fail", True)


def test_under_0_3_4_the_same_typed_purchase_was_allowed():
    """The fail-open this version closes, kept on the frozen pack."""
    frozen = load_pack_dir(FROZEN_0_3_4)
    decision = _replay(frozen, _typed(None))
    assert decision.outcome == ALLOW
    assert _rules(frozen, decision)[R06][0] == "n/a"
