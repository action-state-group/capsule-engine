# SPDX-License-Identifier: Apache-2.0
"""``ASK_UNVERIFIED`` is a declared disposition (PRD v2.8 §8.1): the rule
applied, we asked, and we are not claiming to have evaluated it -- its
declared input was absent or arrived in the wrong shape.

It is pack vocabulary, not a verdict and not a sealed outcome word: a pack
may declare it, a check or gate selector whose rules all declare ASK or
ASK_UNVERIFIED asks an approver exactly as an all-ASK one does, and the
sealed decision carries the same ``escalate`` / ``needs_input`` it always
did. A NEVER, DO or undeclared rule beside it still refuses, and an
integrity check never asks whatever it declares.

offer_expiry/1.0.2 names which failure is which: expired is ASK,
``expiry_unverified`` is ASK_UNVERIFIED. 1.0.1 is superseded at its digest,
not retired, so the seller pack that cites it loads and digests as before.
"""
from __future__ import annotations

import json
import shutil
from dataclasses import replace
from pathlib import Path

import pytest
import yaml
from capsule_ledger.ledger import LedgerStore

from capsule_engine.guards import Action, GuardEngine, LocalSigner
from capsule_engine.guards.capsule import DENY, ESCALATE
from capsule_engine.guards.engine import ASK_RULE_EXCLUDED_CHECKS
from capsule_engine.guards.wickets import Catalog, load_definition_file
from capsule_engine.guards.wickets.retired import RETIRED_IDS
from capsule_engine.packs import build_engine, install_pack, load_pack_dir
from capsule_engine.packs.install import ASKING_DISPOSITIONS, ask_gate_selectors, ask_wickets
from capsule_engine.packs.schema import DEFAULT_DISPOSITION_VALUES, Obligation, PackDefinition

ROOT = Path(__file__).parent.parent / "capsule_engine"
CATALOG = ROOT / "packs" / "catalog"
WICKETS = ROOT / "guards" / "wickets" / "catalog_defs"
PACK = load_pack_dir(Path(__file__).parent / "fixtures" / "packs" / "everyday-0.3.2")
SIGNER = LocalSigner(key_id="ask-unverified-key", secret=b"ask-unverified-fixed-key")

EXPIRY_1_0_1 = load_definition_file(WICKETS / "offer_expiry.yaml")
EXPIRY_1_0_2 = load_definition_file(WICKETS / "offer_expiry.v1.0.2.yaml")


def _with_disposition(pack: PackDefinition, rule: str, disposition: str | None) -> PackDefinition:
    obligations = tuple(replace(o, default_disposition=disposition) if o.id == rule else o for o in pack.obligations)
    return replace(pack, obligations=obligations)


@pytest.fixture
def engine_for(tmp_path):
    stores = []

    def build(pack: PackDefinition) -> GuardEngine:
        n = len(stores)
        store = LedgerStore(tmp_path / f"ledger-{n}")
        stores.append(store)
        installed = install_pack(pack, project_dir=tmp_path / f"project-{n}", mode="observe")
        return build_engine(installed, ledger=store, signer_provider=lambda: SIGNER)

    yield build
    for store in stores:
        store.close()


def _non_refundable_payment(n: int) -> Action:
    return Action(
        verb="make_payment", operator=f"household-ask-unverified-{n}", developer="household-assistant@v1",
        action_class="money.transfer", amount_minor=2_000, currency="EUR", target=f"seller/bike-{n}", rail="card",
        counterparty_account_ref=f"acct-ref-{n}", recurrence="one_time", refundable=False,
        action_id=f"make_payment/ask-unverified-{n}", timestamp=f"2026-08-10T14:{n:02d}:00Z",
    )


def _booking(n: int) -> Action:
    return Action(
        verb="act", operator=f"household-ask-unverified-gate-{n}", developer="household-assistant@v1",
        action_class="booking.create", amount_minor=2_000, currency="EUR", target="venue/restaurant",
        action_id=f"act/ask-unverified-gate-{n}", timestamp=f"2026-08-10T13:{n:02d}:00Z",
    )


R08 = "r08-non-refundable-or-hard-to-undo"
R16 = "r16-booking-with-a-commitment"


# -- the closed set ---------------------------------------------------------------


def test_the_disposition_set_has_ask_unverified_beside_do_ask_never():
    assert DEFAULT_DISPOSITION_VALUES == frozenset({"DO", "ASK", "NEVER", "ASK_UNVERIFIED"})


def test_both_asking_dispositions_are_in_the_closed_set():
    assert ASKING_DISPOSITIONS == frozenset({"ASK", "ASK_UNVERIFIED"})
    assert ASKING_DISPOSITIONS <= DEFAULT_DISPOSITION_VALUES


def test_a_pack_declaring_ask_unverified_loads_and_digests_it(tmp_path):
    pack_dir = tmp_path / "pack"
    shutil.copytree(CATALOG / "payments-safety", pack_dir)
    doc = yaml.safe_load((pack_dir / "pack.yaml").read_text())
    doc["obligations"][0]["default_disposition"] = "ASK_UNVERIFIED"
    (pack_dir / "pack.yaml").write_text(yaml.safe_dump(doc, sort_keys=False))
    pack = load_pack_dir(pack_dir)
    assert pack.obligations[0].default_disposition == "ASK_UNVERIFIED"
    assert pack.canonical_dict()["obligations"][0]["default_disposition"] == "ASK_UNVERIFIED"
    asked = load_pack_dir(CATALOG / "payments-safety")
    assert pack.definition_digest() != asked.definition_digest()


def test_no_catalog_pack_declares_ask_unverified_yet():
    # Switching a catalog rule (everyday r14 r24 r25) is a pack version and a
    # product decision, not part of adding the value.
    for pack_dir in CATALOG.iterdir():
        if (pack_dir / "pack.yaml").exists():
            pack = load_pack_dir(pack_dir)
            assert all(o.default_disposition != "ASK_UNVERIFIED" for o in pack.obligations), pack.pack_id


# -- configured checks ------------------------------------------------------------


def test_a_check_whose_rule_declares_ask_unverified_joins_the_ask_set():
    assert "refundability" in ask_wickets(_with_disposition(PACK, R08, "ASK_UNVERIFIED"))


def test_ask_unverified_asks_with_the_same_sealed_words_as_ask(engine_for):
    asked = engine_for(PACK).check(_non_refundable_payment(1), dry_run=True)
    unverified = engine_for(_with_disposition(PACK, R08, "ASK_UNVERIFIED")).check(
        _non_refundable_payment(1), dry_run=True)
    assert {c.id for c in unverified.constraints if c.result == "fail"} == {"refundability"}
    assert unverified.outcome == asked.outcome == ESCALATE
    assert unverified.capsule["disposition"] == asked.capsule["disposition"]
    assert unverified.capsule["disposition"]["decision"] == "needs_input"
    # No new outcome word reaches the sealed record.
    assert "ASK_UNVERIFIED" not in json.dumps(unverified.capsule)


def test_ask_and_ask_unverified_rules_on_one_check_still_ask(engine_for):
    extra = Obligation(id="rx-unverified", statement="declared", check="refundability",
                       default_disposition="ASK_UNVERIFIED")
    pack = replace(PACK, obligations=(*PACK.obligations, extra))
    assert "refundability" in ask_wickets(pack)
    assert engine_for(pack).check(_non_refundable_payment(2), dry_run=True).outcome == ESCALATE


@pytest.mark.parametrize("other", ["NEVER", "DO", None])
def test_an_ask_unverified_rule_beside_a_never_do_or_undeclared_one_refuses(engine_for, other):
    unverified = _with_disposition(PACK, R08, "ASK_UNVERIFIED")
    extra = Obligation(id="rx-other", statement="declared", check="refundability", default_disposition=other)
    pack = replace(unverified, obligations=(*unverified.obligations, extra))
    assert "refundability" not in ask_wickets(pack)
    assert engine_for(pack).check(_non_refundable_payment(3), dry_run=True).outcome == DENY


@pytest.mark.parametrize("check", sorted(ASK_RULE_EXCLUDED_CHECKS))
def test_an_integrity_or_own_rule_check_never_asks_on_ask_unverified(check):
    extra = Obligation(id=f"rx-{check}", statement="declared", check=check, default_disposition="ASK_UNVERIFIED")
    assert check not in ask_wickets(replace(PACK, obligations=(*PACK.obligations, extra)))


# -- gate selectors ---------------------------------------------------------------


def test_a_gate_selector_whose_rule_declares_ask_unverified_asks(engine_for):
    pack = _with_disposition(PACK, R16, "ASK_UNVERIFIED")
    assert "booking_create" in ask_gate_selectors(pack)
    decision = engine_for(pack).check(_booking(4), dry_run=True)
    assert decision.outcome == ESCALATE
    assert decision.capsule["disposition"]["decision"] == "needs_input"


def test_a_gate_selector_mixing_ask_unverified_and_never_refuses():
    pack = _with_disposition(_with_disposition(PACK, "r11-home-address-to-an-individual", "ASK_UNVERIFIED"),
                             "r12-personal-contact-to-a-new-party", "NEVER")
    assert "personal_disclosure" not in ask_gate_selectors(pack)


# -- offer_expiry/1.0.2 -----------------------------------------------------------


def test_offer_expiry_1_0_2_is_1_0_1_with_each_failures_disposition_stated():
    assert (EXPIRY_1_0_2.check, EXPIRY_1_0_2.config) == (EXPIRY_1_0_1.check, EXPIRY_1_0_1.config)
    assert "ASK_UNVERIFIED" in EXPIRY_1_0_2.semantics
    assert "expiry_unverified" in EXPIRY_1_0_2.semantics
    assert "ASK_UNVERIFIED" not in EXPIRY_1_0_1.semantics
    assert EXPIRY_1_0_2.definition_digest() != EXPIRY_1_0_1.definition_digest()


def test_offer_expiry_1_0_1_is_superseded_not_retired():
    catalog = Catalog(WICKETS)
    assert catalog.get("offer_expiry/1.0.1").digest == "7bcd363b28475e3fac123fe2b8925f60f36bdaa3d22ebd5004ffe5290c4f2597"
    assert catalog.get("offer_expiry/1.0.2") is not None
    assert "offer_expiry/1.0.1" not in RETIRED_IDS
    seller = load_pack_dir(CATALOG / "seller")
    assert "offer_expiry/1.0.1" in {c.wicket_id for c in seller.constraints}


def _offer(n: int, **fields) -> Action:
    return Action(verb="offer", operator="household", developer="assistant@v1", action_class="marketplace.offer",
                  amount_minor=175_000, currency="USD", target="buyer/ref-7", action_id=f"offer/ask-unverified-{n}",
                  timestamp="2026-10-08T09:00:00Z", **fields)


@pytest.mark.parametrize(("fields", "unverified"), [
    ({"proposal_at": "2026-10-01T09:00:00Z"}, False),  # expired: the ASK case
    ({}, True),  # no proposal_at: the ASK_UNVERIFIED case
])
def test_offer_expiry_1_0_2_asks_on_both_failures_and_the_marker_tells_them_apart(tmp_path, fields, unverified):
    store = LedgerStore(tmp_path / "ledger")
    spend = next(f for f in PACK.folds if f.fold_id.startswith("spend.weekly/"))
    try:
        engine = GuardEngine(ledger=store, caps_fold=spend, signer_provider=lambda: SIGNER,
                             wickets=(EXPIRY_1_0_2,), ask_wickets=frozenset({"offer_expiry"}))
        decision = engine.check(_offer(1, **fields), dry_run=True)
        (outcome,) = [c for c in decision.constraints if c.id == "offer_expiry"]
        assert outcome.result == "fail"
        assert decision.outcome == ESCALATE
        assert bool(outcome.evidence.get("expiry_unverified")) is unverified
    finally:
        store.close()
