# SPDX-License-Identifier: Apache-2.0
"""``caps/5.0.0``: the per-action limit reads the authorised maximum.

A card payment or a pre-authorisation may take more than its expected
charge. A deal check seals the expected capture as ``spend_minor`` and, when
one was declared, the most the payment may take as ``spend_authorized_minor``.
Under ``caps/4.0.0`` the per-action limit read the capture, so a payment that
authorised a buffer above the limit passed while its capture stayed under it.
Under ``caps/5.0.0`` the per-action limit reads the authorised maximum and
falls back to the capture when none was declared, or one below the capture
was; the window limit and the
rolling total read only captures. The evidence's ``per_action_basis`` names
the field read and whether it fell back.
"""
from __future__ import annotations

from pathlib import Path
from typing import NotRequired, TypedDict

import pytest
from agent_action_capsule import json_digest

from capsule_engine.folds.loader import load_definition_file
from capsule_engine.guards import Action, GuardEngine
from capsule_engine.guards.checks import check_caps
from capsule_engine.guards.wickets.catalog import Catalog as WicketCatalog
from capsule_engine.policy import load_manifest_file, resolve_manifest
from capsule_engine.report.replay import action_for_record, replay

PACKAGE_DIR = Path(__file__).parent.parent / "capsule_engine"
FOLDS = PACKAGE_DIR / "folds" / "catalog_defs"
WICKETS = PACKAGE_DIR / "guards" / "wickets" / "catalog_defs"
MANIFEST = Path(__file__).parent / "fixtures" / "deal-bundles" / "caps-v5-manifest.yaml"

CAPTURE = 454  # the expected charge, 4.54
AUTHORIZED = 954  # the most the payment may take, 4.54 plus a 5.00 buffer
PER_ACTION = 500
WINDOW = 10_000


class CapsConfig(TypedDict):
    """The ``config`` block of a two-limit ``caps`` wicket, as authored."""

    fold_id: str
    fold_digest: str
    caps_minor: dict[str, int]
    per_action_minor: dict[str, int]
    per_action_reads: NotRequired[str]


def _config(wicket_id: str) -> CapsConfig:
    return WicketCatalog(WICKETS).get(wicket_id).definition.config


def _engine(store, signer, *, per_action_reads: str | None = "spend_authorized_minor", per_action=PER_ACTION, window=WINDOW):
    return GuardEngine(
        ledger=store,
        caps_fold=load_definition_file(FOLDS / "spend.weekly.v3.yaml"),
        signer_provider=lambda: signer,
        caps_minor={"money.purchase": window},
        per_action_minor={"money.purchase": per_action},
        per_action_reads=per_action_reads,
    )


def _action(n: int, capture: int, authorized: int | None) -> Action:
    return Action(
        verb="buy",
        operator="household-a",
        developer="shopping-assistant@v1",
        action_class="money.purchase",
        action_id=f"buy/{n}",
        amount_minor=capture,
        spend_authorized_minor=authorized,
        currency="USD",
        target=f"shop/{n}",
        timestamp=f"2026-10-0{n}T10:00:00Z",
    )


def _caps(decision):
    (caps,) = [c for c in decision.constraints if c.id == "caps"]
    return caps


def test_caps_v5_is_caps_v4_plus_per_action_reads_and_v4_is_unchanged():
    v4, v5 = _config("caps/4.0.0"), _config("caps/5.0.0")
    assert {k: v for k, v in v5.items() if k != "per_action_reads"} == v4
    assert v5["per_action_reads"] == "spend_authorized_minor"
    assert "per_action_reads" not in v4
    entry = WicketCatalog(WICKETS).get("caps/4.0.0")
    assert entry.digest == "2b07340e8fc858af76d8accf6afc3d6c9d05bb92d50abcfddd1fee8d9b51de35"


def test_a_pre_authorisation_buffer_over_the_per_action_limit_is_denied_though_the_capture_is_under_it(store, signer):
    decision = _engine(store, signer).check(_action(1, CAPTURE, AUTHORIZED))
    caps = _caps(decision)
    assert caps.result == "fail"
    assert decision.outcome == "deny"
    assert caps.evidence["tripped"] == [{"limit": "per_action", "threshold_minor": PER_ACTION, "observed_minor": AUTHORIZED}]
    assert caps.evidence["per_action_basis"] == {
        "field": "spend_authorized_minor",
        "fell_back": False,
        "observed_minor": AUTHORIZED,
    }
    assert caps.evidence["amount_minor"] == CAPTURE


def test_under_caps_v4_the_same_buffer_passes_and_the_evidence_keeps_its_shape(store, signer):
    """The evasion caps/5.0.0 closes, kept as caps/4.0.0's pinned behaviour,
    under caps/4.0.0's own config."""
    v4 = _config("caps/4.0.0")
    engine = GuardEngine(
        ledger=store,
        caps_fold=load_definition_file(FOLDS / "spend.weekly.v3.yaml"),
        signer_provider=lambda: signer,
        caps_minor=v4["caps_minor"],
        per_action_minor={**v4["per_action_minor"], "money.purchase": PER_ACTION},
        per_action_reads=v4.get("per_action_reads"),
    )
    caps = _caps(engine.check(_action(1, CAPTURE, AUTHORIZED)))
    assert caps.result == "pass"
    assert set(caps.evidence) == {
        "fold", "fold_key", "weekly_spend_minor", "amount_minor", "cap_minor", "projected_minor",
        "reversals", "per_action_cap_minor", "tripped",
    }
    assert caps.reason.startswith(f"amount {CAPTURE} <= per-action limit")


def test_with_no_authorised_maximum_the_per_action_limit_reads_the_capture_and_says_it_fell_back(store, signer):
    caps = _caps(_engine(store, signer).check(_action(1, CAPTURE, None)))
    assert caps.result == "pass"
    assert caps.evidence["per_action_basis"] == {"field": "spend_minor", "fell_back": True, "observed_minor": CAPTURE}


def test_a_capture_over_the_limit_with_no_authorised_maximum_is_denied_on_the_capture(store, signer):
    caps = _caps(_engine(store, signer).check(_action(1, 600, None)))
    assert caps.result == "fail"
    assert caps.evidence["tripped"][0]["observed_minor"] == 600
    assert caps.evidence["per_action_basis"]["field"] == "spend_minor"


def test_an_authorised_maximum_at_the_limit_passes(store, signer):
    caps = _caps(_engine(store, signer).check(_action(1, CAPTURE, PER_ACTION)))
    assert caps.result == "pass"
    assert caps.evidence["per_action_basis"]["field"] == "spend_authorized_minor"


def test_an_authorised_maximum_below_the_capture_is_not_read(store, signer):
    """Not a maximum: the capture over the limit still denies."""
    caps = _caps(_engine(store, signer).check(_action(1, 600, 100)))
    assert caps.result == "fail"
    assert caps.evidence["per_action_basis"] == {"field": "spend_minor", "fell_back": True, "observed_minor": 600}


def test_the_rolling_total_sums_captures_and_never_an_authorisation(store, signer):
    engine = _engine(store, signer, per_action=2_500)
    assert engine.check(_action(1, CAPTURE, AUTHORIZED)).outcome == "allow"

    caps = _caps(engine.check(_action(2, 100, 2_000)))
    assert caps.evidence["weekly_spend_minor"] == CAPTURE
    assert caps.evidence["projected_minor"] == CAPTURE + 100


def test_the_window_limit_reads_the_capture(store, signer):
    caps = _caps(_engine(store, signer, per_action=2_500, window=500).check(_action(1, CAPTURE, AUTHORIZED)))
    assert caps.result == "pass"
    assert caps.evidence["tripped"] == []


def test_an_unknown_per_action_reads_value_is_refused_when_the_engine_is_built(store, signer):
    with pytest.raises(ValueError, match="per_action_reads"):
        _engine(store, signer, per_action_reads="authorized_max_minor")


def test_an_unknown_per_action_reads_value_is_refused_by_the_check(store):
    with pytest.raises(ValueError, match="per_action_reads"):
        check_caps(
            _action(1, CAPTURE, AUTHORIZED),
            store,
            definition=load_definition_file(FOLDS / "spend.weekly.v3.yaml"),
            cap_minor=WINDOW,
            per_action_cap_minor=PER_ACTION,
            per_action_reads="authorized_max_minor",
        )


def test_the_reason_names_the_field_the_per_action_limit_read(store, signer):
    caps = _caps(_engine(store, signer).check(_action(1, CAPTURE, PER_ACTION)))
    assert caps.reason.startswith(f"spend_authorized_minor {PER_ACTION} <= per-action limit {PER_ACTION}")


class DealCheckBody(TypedDict, total=False):
    """The members of a deal check's ``body`` these tests set."""

    action: str
    action_class: str
    taxonomy_version: str
    currency: str
    direction: str
    amount_minor: int
    authorized_max_minor: int
    spend_minor: int
    spend_authorized_minor: int


DealCheck = TypedDict("DealCheck", {"body": DealCheckBody, "x-deal-v0": dict[str, str | int]})


class BoundCapsule(TypedDict):
    """A capsule that seals a deal check by its ``agent_input_digest``."""

    capsule_id: str
    action_id: str
    action_type: str
    operator: str
    developer: str
    timestamp: str
    model_attestation: dict[str, dict[str, str]]


def _bound_check(body: DealCheckBody) -> tuple[BoundCapsule, DealCheck]:
    """A deal check record with ``body``, and a capsule that seals it."""
    record: DealCheck = {
        "body": {"action": "pay", "action_class": "money.purchase", "taxonomy_version": "2", "currency": "USD", **body},
        "x-deal-v0": {"record_type": "check", "deal_id": "deal-0000000000000000", "seq": 2},
    }
    capsule: BoundCapsule = {
        "capsule_id": "0" * 64,
        "action_id": "deal-0000000000000000/2",
        "action_type": "fyi",
        "operator": "household-a",
        "developer": "capsulectl-deal",
        "timestamp": "2026-10-07T12:00:00Z",
        "model_attestation": {"compute_attestation": {"agent_input_digest": json_digest(record)}},
    }
    return capsule, record


def test_a_deal_check_carries_its_authorised_maximum():
    capsule, record = _bound_check(
        {"amount_minor": CAPTURE, "authorized_max_minor": AUTHORIZED, "spend_minor": CAPTURE, "spend_authorized_minor": AUTHORIZED}
    )
    action = action_for_record(capsule, record)
    assert (action.amount_minor, action.spend_authorized_minor) == (CAPTURE, AUTHORIZED)


@pytest.mark.parametrize("value", [True, "954"])
def test_an_authorised_maximum_that_is_not_an_integer_is_not_carried(value):
    capsule, record = _bound_check({"spend_minor": CAPTURE})
    record["body"]["spend_authorized_minor"] = value
    capsule["model_attestation"]["compute_attestation"]["agent_input_digest"] = json_digest(record)
    assert action_for_record(capsule, record).spend_authorized_minor is None


def test_money_moving_in_carries_no_authorised_maximum():
    capsule, record = _bound_check(
        {"action": "cancel", "action_class": "money.refund", "spend_minor": 500, "spend_authorized_minor": 900, "direction": "in"}
    )
    action = action_for_record(capsule, record)
    assert (action.amount_minor, action.spend_authorized_minor) == (0, None)


def test_a_manifest_pinning_caps_v5_denies_the_buffered_deal_check_on_replay():
    resolved = resolve_manifest(load_manifest_file(MANIFEST), fold_catalog_dir=FOLDS, wicket_catalog_dir=WICKETS)
    assert resolved.per_action_reads() == "spend_authorized_minor"
    capsule, record = _bound_check({"amount_minor": CAPTURE, "spend_minor": CAPTURE, "spend_authorized_minor": 2_600})
    result = replay(
        [capsule],
        caps_fold=resolved.caps_fold(),
        caps_minor=resolved.caps_minor(),
        per_action_minor=resolved.per_action_minor(),
        per_action_reads=resolved.per_action_reads(),
        disclosed={capsule["capsule_id"]: record},
    )
    (sourced,) = result.decisions
    caps = _caps(sourced.decision)
    assert caps.result == "fail"
    assert caps.evidence["per_action_basis"]["field"] == "spend_authorized_minor"
    assert caps.evidence["tripped"][0]["observed_minor"] == 2_600
