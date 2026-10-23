# SPDX-License-Identifier: Apache-2.0
"""counterparty_seen_before/2.0.0: a dry run does not make a counterparty known.

GuardEngine records a dry-run allow as accepted, with
``asg_payload.checkpoint.dry_run`` set, so counterparty.seen_before/1.0.0
counted a dry run as a prior action and the first real payment to that
counterparty passed as a repeat. counterparty.seen_before/2.0.0 leaves dry
runs out. Version 1 of the fold and the wicket stay as they were.
"""
from __future__ import annotations

from pathlib import Path

from capsule_engine.folds.loader import load_definition_file as load_fold
from capsule_engine.guards import Action, GuardEngine
from capsule_engine.guards.checks import seen_before_fold
from capsule_engine.guards.wickets import load_definition_file as load_wicket

ROOT = Path(__file__).parent.parent / "capsule_engine"
WICKETS = ROOT / "guards" / "wickets" / "catalog_defs"
FOLDS = ROOT / "folds" / "catalog_defs"


def _action(n: int) -> Action:
    return Action(
        verb="buy",
        operator="household-a",
        developer="assistant@v1",
        action_class="money.purchase",
        amount_minor=1_500,
        currency="USD",
        target="merchant/ref-1",
        timestamp=f"2026-10-0{n}T09:00:00Z",
        action_id=f"buy/{n}",
        equivalence_key=f"buy-{n}",
    )


def _seen_after_a_dry_run(store, signer, wicket_file: str):
    """A dry run recorded while the rule was not yet configured (a first-time
    counterparty fails the rule, so a dry run under it is not accepted),
    then the first real action once it is."""
    fold = load_fold(FOLDS / "spend.weekly.v3.yaml")
    observing = GuardEngine(ledger=store, caps_fold=fold, signer_provider=lambda: signer)
    dry = observing.check(_action(1), dry_run=True)
    assert dry.capsule["disposition"]["decision"] == "accept"
    assert dry.capsule["asg_payload"]["checkpoint"]["dry_run"] is True
    engine = GuardEngine(
        ledger=store,
        caps_fold=fold,
        signer_provider=lambda: signer,
        wickets=(load_wicket(WICKETS / wicket_file),),
    )
    (out,) = [c for c in engine.check(_action(2)).constraints if c.id == "counterparty_seen_before"]
    return out


def test_v2_wicket_cites_the_v2_fold_and_v1_is_untouched():
    v1 = load_wicket(WICKETS / "counterparty_seen_before.yaml")
    v2 = load_wicket(WICKETS / "counterparty_seen_before.v2.yaml")
    assert v1.config["fold_id"] == "counterparty.seen_before/1.0.0"
    assert v1.config["fold_digest"] == "c43b2011b4d49cf54a0306e9637ecfd3a945ff7febc13283507dd77513ae9b28"
    assert v2.wicket_id == "counterparty_seen_before/2.0.0"
    assert v2.config["action_classes"] == v1.config["action_classes"]
    fold = seen_before_fold(v2.config["fold_id"], v2.config["fold_digest"])
    assert fold.fold_id == "counterparty.seen_before/2.0.0"
    assert fold.definition_digest() == load_fold(FOLDS / "counterparty.seen_before.v2.yaml").definition_digest()


def test_v1_counts_a_dry_run_as_a_prior_action(store, signer):
    """The defect, pinned on the old version."""
    out = _seen_after_a_dry_run(store, signer, "counterparty_seen_before.yaml")
    assert out.result == "pass"
    assert out.evidence["prior_count"] == 1


def test_v2_does_not_count_a_dry_run(store, signer):
    out = _seen_after_a_dry_run(store, signer, "counterparty_seen_before.v2.yaml")
    assert out.result == "fail"
    assert out.evidence["prior_count"] == 0
    assert out.method == "counterparty.seen_before/2.0.0"


def test_v2_still_counts_a_real_prior_action(store, signer):
    fold = load_fold(FOLDS / "spend.weekly.v3.yaml")
    plain = GuardEngine(ledger=store, caps_fold=fold, signer_provider=lambda: signer)
    prior = plain.check(_action(1))
    assert prior.capsule["disposition"]["decision"] == "accept"
    assert "dry_run" not in prior.capsule["asg_payload"]["checkpoint"]

    engine = GuardEngine(
        ledger=store,
        caps_fold=fold,
        signer_provider=lambda: signer,
        wickets=(load_wicket(WICKETS / "counterparty_seen_before.v2.yaml"),),
    )
    (out,) = [c for c in engine.check(_action(2)).constraints if c.id == "counterparty_seen_before"]
    assert out.result == "pass"
    assert out.evidence["prior_count"] == 1
