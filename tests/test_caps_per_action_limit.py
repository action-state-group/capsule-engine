# SPDX-License-Identifier: Apache-2.0
"""``caps/3.0.0``: two limits per class -- a per-action limit on the proposed
action alone, and the pooled rolling-window limit -- and evidence that names
which one tripped.

``caps/2.0.0`` had only the window limit, so a single purchase above the
per-purchase limit passed whenever the week's total stayed under the window
limit. With two limits, "over the limit" is ambiguous unless the record says
which: the evidence carries ``tripped``, one entry per limit exceeded, each
with its threshold and the observed value it was compared against.
"""
from __future__ import annotations

from pathlib import Path
from typing import TypedDict

import pytest

from capsule_engine.folds.loader import load_definition_file
from capsule_engine.guards import Action, GuardEngine
from capsule_engine.guards.wickets.catalog import Catalog as WicketCatalog
from capsule_engine.packs import accept_thresholds, load_pack_dir

PACKAGE_DIR = Path(__file__).parent.parent / "capsule_engine"
CATALOG_DIR = PACKAGE_DIR / "folds" / "catalog_defs"
WICKET_CATALOG_DIR = PACKAGE_DIR / "guards" / "wickets" / "catalog_defs"
EVERYDAY_DIR = PACKAGE_DIR / "packs" / "catalog" / "everyday"

PER_ACTION_MINOR = 2_500  # 25.00
WINDOW_MINOR = 10_000  # 100.00
OPERATOR = "household-a"


class CapsConfig(TypedDict):
    """The ``config`` block of a two-limit ``caps`` wicket, as authored."""

    fold_id: str
    fold_digest: str
    caps_minor: dict[str, int]
    per_action_minor: dict[str, int]


def _caps_v3_config() -> CapsConfig:
    entry = WicketCatalog(WICKET_CATALOG_DIR).get("caps/3.0.0")
    assert entry is not None
    return entry.definition.config


def _engine(store, signer, action_class: str) -> GuardEngine:
    return GuardEngine(
        ledger=store,
        caps_fold=load_definition_file(CATALOG_DIR / "spend.weekly.v2.yaml"),
        signer_provider=lambda: signer,
        caps_minor={action_class: WINDOW_MINOR},
        per_action_minor={action_class: PER_ACTION_MINOR},
    )


def _action(n: int, action_class: str, amount_minor: int) -> Action:
    return Action(
        verb="buy",
        operator=OPERATOR,
        developer="shopping-assistant@v1",
        action_class=action_class,
        action_id=f"buy/{n}",
        amount_minor=amount_minor,
        currency="USD",
        target=f"shop/{n}",
        timestamp=f"2026-10-0{n}T10:00:00Z",
    )


def _caps(decision):
    (caps,) = [c for c in decision.constraints if c.id == "caps"]
    return caps


def test_caps_v3_carries_both_limits_for_every_class_and_cites_the_v2_fold():
    config = _caps_v3_config()
    assert set(config["per_action_minor"]) == set(config["caps_minor"])
    fold = load_definition_file(CATALOG_DIR / "spend.weekly.v2.yaml")
    assert config["fold_id"] == fold.fold_id
    assert config["fold_digest"] == fold.definition_digest()


def test_caps_v2_is_untouched_and_carries_no_per_action_limit():
    entry = WicketCatalog(WICKET_CATALOG_DIR).get("caps/2.0.0")
    assert entry.definition.definition_digest() == "b7ea63ec3d9fdb872d3b5db952e774f04ff8f4b945ca803e49181b91b2a25f80"
    assert "per_action_minor" not in entry.definition.config


@pytest.mark.parametrize(
    ("action_class", "outcome"),
    [("money.transfer", "escalate"), ("money.purchase", "escalate"), ("agreement.accept", "deny")],
)
def test_a_single_purchase_over_the_per_action_limit_fails_and_names_per_action(store, signer, action_class, outcome):
    """60.00 against 25.00 per action / 100.00 per week, nothing spent yet.
    The outcome is the engine's existing caps mapping: a class with an
    approver role escalates (the ASK path), a class without one denies."""
    decision = _engine(store, signer, action_class).check(_action(1, action_class, 6_000))

    caps = _caps(decision)
    assert caps.result == "fail"
    assert caps.evidence["tripped"] == [{"limit": "per_action", "threshold_minor": 2_500, "observed_minor": 6_000}]
    assert decision.outcome == outcome


def test_a_purchase_under_the_per_action_limit_that_crosses_the_window_names_window(store, signer):
    """20.00 with 95.00 already spent this week."""
    engine = _engine(store, signer, "money.transfer")
    for n, amount in enumerate((2_500, 2_500, 2_500, 2_000), start=1):
        assert engine.check(_action(n, "money.transfer", amount)).outcome == "allow"

    decision = engine.check(_action(5, "money.transfer", 2_000))
    caps = _caps(decision)
    assert caps.result == "fail"
    assert caps.evidence["weekly_spend_minor"] == 9_500
    assert caps.evidence["tripped"] == [{"limit": "window", "threshold_minor": 10_000, "observed_minor": 11_500}]
    assert decision.outcome == "escalate"


def test_both_limits_tripping_are_both_named(store, signer):
    engine = _engine(store, signer, "money.transfer")
    for n in range(1, 5):
        assert engine.check(_action(n, "money.transfer", 2_400)).outcome == "allow"

    caps = _caps(engine.check(_action(5, "money.transfer", 3_000)))
    assert [t["limit"] for t in caps.evidence["tripped"]] == ["per_action", "window"]


def test_a_purchase_under_both_limits_passes_with_nothing_tripped(store, signer):
    caps = _caps(_engine(store, signer, "money.purchase").check(_action(1, "money.purchase", 2_500)))
    assert caps.result == "pass"
    assert caps.evidence["tripped"] == []
    assert caps.evidence["per_action_cap_minor"] == PER_ACTION_MINOR


def test_without_a_per_action_limit_the_evidence_keeps_its_single_limit_shape(store, signer):
    engine = GuardEngine(
        ledger=store,
        caps_fold=load_definition_file(CATALOG_DIR / "spend.weekly.v2.yaml"),
        signer_provider=lambda: signer,
        caps_minor={"money.purchase": WINDOW_MINOR},
    )
    caps = _caps(engine.check(_action(1, "money.purchase", 6_000)))
    assert caps.result == "pass"
    assert set(caps.evidence) == {
        "fold", "fold_key", "weekly_spend_minor", "amount_minor", "cap_minor", "projected_minor",
    }


def test_a_legacy_keyed_per_action_limit_applies_to_the_canonical_class(store, signer):
    engine = GuardEngine(
        ledger=store,
        caps_fold=load_definition_file(CATALOG_DIR / "spend.weekly.v2.yaml"),
        signer_provider=lambda: signer,
        caps_minor={"communication.send": WINDOW_MINOR},
        per_action_minor={"comms.external": 100},
    )
    caps = _caps(engine.check(_action(1, "communication.send", 500)))
    assert caps.evidence["tripped"][0]["limit"] == "per_action"


def _everyday_caps_config() -> CapsConfig:
    """The config of the caps wicket the everyday pack cites."""
    (caps,) = [w for w in load_pack_dir(EVERYDAY_DIR).constraints if w.check == "caps"]
    return WicketCatalog(WICKET_CATALOG_DIR).get(caps.wicket_id).definition.config


def test_the_installed_everyday_pack_wires_the_per_action_limit(store, signer, tmp_path):
    from capsule_engine.packs import build_engine, install_pack

    installed = install_pack(load_pack_dir(EVERYDAY_DIR), project_dir=tmp_path / "project", mode="enforce")
    assert installed.resolved.per_action_minor() == _everyday_caps_config()["per_action_minor"]
    engine = build_engine(installed, ledger=store, signer_provider=lambda: signer)
    caps = _caps(engine.check(_action(1, "money.purchase", 6_000)))
    assert caps.evidence["tripped"][0]["limit"] == "per_action"


def test_a_user_accepted_per_action_limit_replaces_the_default():
    pack = accept_thresholds(load_pack_dir(EVERYDAY_DIR), {}, accepted_per_action={"money.purchase": 5_000})
    (caps,) = [w for w in pack.constraints if w.check == "caps"]
    assert caps.config["per_action_minor"]["money.purchase"] == 5_000
    assert caps.config["per_action_minor"]["booking.create"] == _everyday_caps_config()["per_action_minor"]["booking.create"]
