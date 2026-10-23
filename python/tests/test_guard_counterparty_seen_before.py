# SPDX-License-Identifier: Apache-2.0
"""counterparty_seen_before/1.0.0: a first-time counterparty fails the check
and asks (escalates) where the class has an approver; a repeat passes. The
count is the counterparty.seen_before/1.0.0 fold over one operator's
accepted actions, keyed by asg_payload.target."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import TypedDict

import pytest
from agent_action_capsule import json_digest

from capsule_engine.folds.loader import load_definition_file as load_fold
from capsule_engine.guards import Action, GuardEngine
from capsule_engine.guards.capsule import not_applicable_evidence
from capsule_engine.guards.checks import seen_before_fold
from capsule_engine.guards.wickets import load_definition_file as load_wicket

ROOT = Path(__file__).parent.parent / "capsule_engine"
SEEN = load_wicket(ROOT / "guards" / "wickets" / "catalog_defs" / "counterparty_seen_before.yaml")
FOLD = load_fold(ROOT / "folds" / "catalog_defs" / "counterparty.seen_before.yaml")
RAIL = load_wicket(ROOT / "guards" / "wickets" / "catalog_defs" / "destination_rail.yaml")
# external_commitment.other names no approver role.
CLASSES = ["money.purchase", "money.transfer", "external_commitment.other"]
SEEN_BOTH = replace(SEEN, config={**SEEN.config, "action_classes": CLASSES})


def _action(n: int, **overrides) -> Action:
    fields = dict(
        verb="buy",
        operator="household",
        developer="assistant@v1",
        action_class="money.purchase",
        amount_minor=1_500,
        currency="EUR",
        target="merchant/ref-1",
        timestamp=f"2026-10-0{n}T09:00:00Z",
        action_id=f"buy/{n}",
        equivalence_key=f"buy-{n}",
    )
    fields.update(overrides)
    return Action(**fields)


def _engine(store, caps_fold, signer, *, wickets=(SEEN_BOTH,), caps_minor=None) -> GuardEngine:
    return GuardEngine(
        ledger=store,
        caps_fold=caps_fold,
        signer_provider=lambda: signer,
        caps_minor=caps_minor or {c: 10_000 for c in CLASSES},
        wickets=wickets,
    )


def _seen(decision):
    (out,) = [c for c in decision.constraints if c.id == "counterparty_seen_before"]
    return out


def _sealed_digest(decision) -> str:
    (record,) = [c for c in decision.capsule["constraints"] if c["id"] == "counterparty_seen_before"]
    return record["evidence_digest"]


class FoldKey(TypedDict):
    path: str
    value: str


class SeenEvidence(TypedDict):
    fold: str
    fold_key: FoldKey
    operator: str
    prior_count: int
    seen_before: bool


def _evidence(*, prior_count: int, operator: str = "household", target: str = "merchant/ref-1") -> SeenEvidence:
    return {
        "fold": FOLD.definition_digest(),
        "fold_key": {"path": "asg_payload.target", "value": target},
        "operator": operator,
        "prior_count": prior_count,
        "seen_before": prior_count > 0,
    }


def test_the_wicket_pins_the_catalog_fold_digest():
    assert SEEN.config["fold_digest"] == FOLD.definition_digest()
    assert seen_before_fold(SEEN.config["fold_id"], SEEN.config["fold_digest"]) == FOLD


def test_a_wrong_fold_digest_is_refused():
    with pytest.raises(ValueError, match="the wicket pins"):
        seen_before_fold(SEEN.config["fold_id"], "0" * 64)


@pytest.mark.parametrize(
    ("action_class", "outcome"),
    [("money.transfer", "escalate"), ("money.purchase", "escalate"), ("external_commitment.other", "deny")],
)
def test_a_first_time_counterparty_fails_and_asks_where_the_class_has_an_approver(store, caps_fold, signer,
                                                                                 action_class, outcome):
    decision = _engine(store, caps_fold, signer).check(_action(1, action_class=action_class))
    out = _seen(decision)
    assert out.result == "fail"
    assert out.evidence == _evidence(prior_count=0)
    assert _sealed_digest(decision) == json_digest(_evidence(prior_count=0))
    assert decision.outcome == outcome


def test_a_repeat_counterparty_within_limits_passes_and_allows(store, caps_fold, signer):
    # The first purchase was accepted before the rule was configured.
    _engine(store, caps_fold, signer, wickets=()).check(_action(1))
    decision = _engine(store, caps_fold, signer).check(_action(2))
    out = _seen(decision)
    assert out.result == "pass"
    assert out.evidence == _evidence(prior_count=1)
    assert _sealed_digest(decision) == json_digest(_evidence(prior_count=1))
    assert decision.outcome == "allow"
    third = _engine(store, caps_fold, signer).check(_action(3))
    assert _seen(third).evidence["prior_count"] == 2


def test_another_operator_keeps_its_own_history(store, caps_fold, signer):
    _engine(store, caps_fold, signer, wickets=()).check(_action(1))
    decision = _engine(store, caps_fold, signer).check(_action(2, operator="neighbour"))
    assert _seen(decision).result == "fail"
    assert _seen(decision).evidence == _evidence(prior_count=0, operator="neighbour")


def test_another_target_is_a_first_time_counterparty(store, caps_fold, signer):
    _engine(store, caps_fold, signer, wickets=()).check(_action(1))
    decision = _engine(store, caps_fold, signer).check(_action(2, target="merchant/ref-2"))
    assert _seen(decision).evidence == _evidence(prior_count=0, target="merchant/ref-2")


def test_a_refused_prior_attempt_does_not_make_the_counterparty_known(store, caps_fold, signer):
    # A cited mandate that is not on the ledger fails verify_before_dispatch.
    refused = _engine(store, caps_fold, signer, wickets=()).check(_action(1, cited_mandate_capsule_id="0" * 64))
    assert refused.outcome == "deny"
    assert refused.capsule["disposition"]["decision"] != "accept"
    decision = _engine(store, caps_fold, signer).check(_action(2))
    assert _seen(decision).evidence == _evidence(prior_count=0)


def test_a_first_time_counterparty_over_the_cap_still_asks(store, caps_fold, signer):
    decision = _engine(store, caps_fold, signer, caps_minor={"money.transfer": 1_000}).check(
        _action(1, action_class="money.transfer")
    )
    assert {c.id for c in decision.constraints if c.result == "fail"} == {"caps", "counterparty_seen_before"}
    assert decision.outcome == "escalate"


def test_a_first_time_counterparty_on_a_watched_rail_denies(store, caps_fold, signer):
    rail = replace(RAIL, config={**RAIL.config, "action_classes": CLASSES})
    decision = _engine(store, caps_fold, signer, wickets=(SEEN_BOTH, rail)).check(
        _action(1, action_class="money.transfer", rail="p2p")
    )
    assert decision.outcome == "deny"


def test_an_action_without_a_target_is_in_scope_and_names_the_missing_field(store, caps_fold, signer):
    out = _seen(_engine(store, caps_fold, signer).check(_action(1, target=None), dry_run=True))
    assert out.result == "n/a"
    assert out.evidence == not_applicable_evidence("counterparty_seen_before", in_scope=True, missing_field="target")


def test_a_class_outside_the_wicket_is_out_of_scope(store, caps_fold, signer):
    out = _seen(_engine(store, caps_fold, signer).check(_action(1, action_class="money.subscription"), dry_run=True))
    assert out.result == "n/a"
    assert out.evidence == not_applicable_evidence("counterparty_seen_before", in_scope=False)


def test_the_shipped_wicket_covers_purchases_only():
    assert SEEN.config["action_classes"] == ["money.purchase"]
