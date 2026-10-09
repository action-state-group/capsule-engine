# SPDX-License-Identifier: Apache-2.0
"""``caps/2.0.0``: one weekly spend limit over every class that pays money
out, folded per operator so neither a second producer nor a developer
version bump starts a fresh total.

``caps/1.0.0`` configured ``money.transfer`` only, so a booking or a purchase
was never checked against the limit, and ``spend.weekly/1.0.0`` keyed the
total by ``developer``: a record from another producer, or from the same
agent after its version string changed, landed in a different partition and
the running total read as zero.
"""
from __future__ import annotations

from pathlib import Path
from typing import TypedDict

import pytest

from capsule_engine.folds.loader import load_definition_file
from capsule_engine.guards import Action, GuardEngine
from capsule_engine.guards.checks.caps import resolve_caps_minor
from capsule_engine.guards.classes import TAXONOMY
from capsule_engine.guards.wickets.catalog import Catalog as WicketCatalog

PACKAGE_DIR = Path(__file__).parent.parent / "capsule_engine"
CATALOG_DIR = PACKAGE_DIR / "folds" / "catalog_defs"
WICKET_CATALOG_DIR = PACKAGE_DIR / "guards" / "wickets" / "catalog_defs"
LIMIT_MINOR = 20_000  # 200.00
BOOKING_MINOR = 55_880  # 558.80
OPERATOR = "household-a"

# COMMIT rows the cap leaves out on purpose: money arrives at the operator,
# so counting it as spend would be wrong.
INBOUND_COMMIT_CLASSES = frozenset({"money.refund", "marketplace.sale"})


class CapsConfig(TypedDict):
    """The ``config`` block of a ``caps`` wicket, as authored."""

    fold_id: str
    fold_digest: str
    caps_minor: dict[str, int]


def _caps_v2_config() -> CapsConfig:
    entry = WicketCatalog(WICKET_CATALOG_DIR).get("caps/2.0.0")
    assert entry is not None
    return entry.definition.config


def _fold_v2():
    return load_definition_file(CATALOG_DIR / "spend.weekly.v2.yaml")


def _engine(store, signer, caps_minor: dict[str, int], fold=None) -> GuardEngine:
    return GuardEngine(
        ledger=store, caps_fold=fold or _fold_v2(), signer_provider=lambda: signer, caps_minor=caps_minor
    )


def _action(n: int, action_class: str, amount_minor: int, *, developer: str = "trip-assistant@v1", **extra) -> Action:
    return Action(
        verb="book",
        operator=OPERATOR,
        developer=developer,
        action_class=action_class,
        action_id=f"book/{n}",
        amount_minor=amount_minor,
        currency="USD",
        target=f"vendor/{n}",
        timestamp=f"2026-10-0{n}T10:00:00Z",
        **extra,
    )


def _caps(decision):
    (caps,) = [c for c in decision.constraints if c.id == "caps"]
    return caps


def test_cap_set_is_every_outgoing_commit_class_in_the_taxonomy():
    config = _caps_v2_config()
    commit = {name for name, ac in TAXONOMY.items() if ac.trigger_class == "COMMIT"}
    assert set(config["caps_minor"]) == commit - INBOUND_COMMIT_CLASSES
    assert {"money.transfer", "money.purchase", "booking.create"} <= set(config["caps_minor"])
    # One pooled limit: the fold totals every class, so the per-class values agree.
    assert len(set(config["caps_minor"].values())) == 1


def test_caps_v2_cites_the_operator_keyed_fold_by_digest():
    config = _caps_v2_config()
    fold = _fold_v2()
    assert config["fold_id"] == fold.fold_id == "spend.weekly/2.0.0"
    assert config["fold_digest"] == fold.definition_digest()
    assert fold.key == "operator"


def test_over_cap_booking_is_held_and_evidence_names_the_fold_key(store, signer):
    caps_minor = dict.fromkeys(_caps_v2_config()["caps_minor"], LIMIT_MINOR)
    decision = _engine(store, signer, caps_minor).check(_action(1, "booking.create", BOOKING_MINOR))

    assert decision.outcome == "escalate"
    caps = _caps(decision)
    assert caps.result == "fail"
    assert caps.evidence["fold_key"] == {"path": "operator", "value": OPERATOR}
    assert caps.evidence["projected_minor"] == BOOKING_MINOR
    assert caps.evidence["cap_minor"] == LIMIT_MINOR


@pytest.mark.parametrize(("first", "second"), [("money.purchase", "booking.create"), ("booking.create", "money.purchase")])
def test_booking_and_purchase_draw_on_one_pooled_total(store, signer, first, second):
    engine = _engine(store, signer, dict.fromkeys(_caps_v2_config()["caps_minor"], LIMIT_MINOR))
    assert engine.check(_action(1, first, 15_000)).outcome == "allow"

    decision = engine.check(_action(2, second, 10_000))
    assert decision.outcome == "escalate"
    assert _caps(decision).evidence["weekly_spend_minor"] == 15_000


@pytest.mark.parametrize("second_developer", ["trip-assistant@v2", "deal-producer"])
def test_a_new_developer_string_does_not_reset_the_operator_total(store, signer, second_developer):
    engine = _engine(store, signer, {"money.purchase": LIMIT_MINOR})
    assert engine.check(_action(1, "money.purchase", 15_000, developer="trip-assistant@v1")).outcome == "allow"

    decision = engine.check(_action(2, "money.purchase", 10_000, developer=second_developer))
    assert decision.outcome == "escalate"
    assert _caps(decision).evidence["weekly_spend_minor"] == 15_000


def test_another_operator_has_its_own_total(store, signer):
    engine = _engine(store, signer, {"money.purchase": LIMIT_MINOR})
    assert engine.check(_action(1, "money.purchase", 15_000)).outcome == "allow"

    other = Action(
        verb="book", operator="household-b", developer="trip-assistant@v1", action_class="money.purchase",
        action_id="book/2", amount_minor=10_000, currency="USD", target="vendor/2", timestamp="2026-10-02T10:00:00Z",
    )
    decision = engine.check(other)
    assert decision.outcome == "allow"
    assert _caps(decision).evidence["weekly_spend_minor"] == 0


def test_developer_keyed_fold_still_evaluates_and_names_its_key(store, signer, caps_fold):
    decision = _engine(store, signer, {"money.transfer": LIMIT_MINOR}, fold=caps_fold).check(
        _action(1, "money.transfer", BOOKING_MINOR)
    )
    assert _caps(decision).result == "fail"
    assert _caps(decision).evidence["fold_key"] == {"path": "developer", "value": "trip-assistant@v1"}


@pytest.mark.parametrize(
    ("config_key", "action_class"),
    [("comms.external", "communication.send"), ("communication.send", "comms.external")],
)
def test_a_legacy_keyed_cap_and_a_canonical_keyed_cap_are_the_same_cap(store, signer, config_key, action_class):
    decision = _engine(store, signer, {config_key: 100}).check(_action(1, action_class, 500))
    assert _caps(decision).result == "fail"


def test_resolve_caps_minor_refuses_two_limits_for_one_class():
    with pytest.raises(ValueError, match="communication.send"):
        resolve_caps_minor({"comms.external": 100, "communication.send": 200})
    assert resolve_caps_minor({"comms.external": 100, "communication.send": 100}) == {"communication.send": 100}


def test_resolve_caps_minor_keeps_an_unknown_name_as_written():
    assert resolve_caps_minor({"custom.class": 5}) == {"custom.class": 5}
