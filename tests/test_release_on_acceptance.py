# SPDX-License-Identifier: Apache-2.0
"""seller.release_on_acceptance/1.0.0: some personal data (the address)
reaches a buyer only when the disclosure cites the sale's sealed acceptance
and that acceptance was sealed for the same buyer. Anything else fails, and
the engine refuses it: an approval does not clear it. The outcome names no
value: no reference, no counterparty, no capsule id.

Each failure case below differs from a passing disclosure in one input, so
each test names the one condition it removes."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from capsule_ledger.ledger import LedgerStore
from capsule_ledger.ledger.api import VerificationResult

from capsule_engine.folds.loader import load_definition_file as load_fold
from capsule_engine.guards import Action, GuardEngine
from capsule_engine.guards.capsule import ALLOW, DENY
from capsule_engine.guards.checks import CONFIGURED_CHECKS, check_release_on_acceptance
from capsule_engine.guards.classes import resolve
from capsule_engine.guards.wickets import load_definition_file

ROOT = Path(__file__).parent.parent / "capsule_engine"
RELEASE = load_definition_file(ROOT / "guards" / "wickets" / "catalog_defs" / "release_on_acceptance.seller.yaml")
SPEND = load_fold(ROOT / "folds" / "catalog_defs" / "spend.weekly.v3.yaml")

SALE = "a" * 64
OTHER_SALE = "b" * 64
ITEM = "item/ref-1"
OTHER_ITEM = "item/ref-2"
BUYER_A = "buyer/ref-a"
BUYER_B = "buyer/ref-b"
CONFIG = RELEASE.config

# Every value a release record must never carry.
VALUES = (SALE, OTHER_SALE, ITEM, OTHER_ITEM, BUYER_A, BUYER_B)


def _engine(store, signer) -> GuardEngine:
    return GuardEngine(ledger=store, caps_fold=SPEND, signer_provider=lambda: signer, wickets=(RELEASE,))


def _accept(engine, n: int, *, target: str = BUYER_A, sale: str = SALE, item: str = ITEM,
            action_class: str = "agreement.accept", dry_run: bool = False) -> str:
    """Seal one record in the ledger; returns its capsule_id. Each has its
    own equivalence_key, so dedupe never reads two of them as one act."""
    decision = engine.check(
        Action(verb="accept", operator="household", developer="assistant@v1", action_class=action_class,
               target=target, amount_minor=175_000, currency="USD", task_authority_ref=sale, item_ref=item,
               action_id=f"accept/{n}", equivalence_key=f"accept/{n}", timestamp=f"2026-10-09T08:0{n}:00Z"),
        dry_run=dry_run,
    )
    assert decision.outcome == ALLOW
    return decision.capsule["capsule_id"]


def _share(**overrides) -> Action:
    fields = dict(verb="share_address", operator="household", developer="assistant@v1",
                  action_class="disclosure.personal", representation_class="address", recipient_role="buyer",
                  target=BUYER_A, task_authority_ref=SALE, item_ref=ITEM, action_id="share/1",
                  timestamp="2026-10-09T09:00:00Z")
    fields.update(overrides)
    return Action(**fields)


def _check(action: Action, store):
    return check_release_on_acceptance(action, store, **CONFIG).constraint


def _assert_value_free(outcome) -> None:
    serialised = json.dumps({"reason": outcome.reason, "evidence": outcome.evidence})
    for value in VALUES:
        assert value not in serialised, value


# -- the definition --------------------------------------------------------------


def test_the_definition_configures_the_check_and_states_its_rule():
    assert RELEASE.wicket_id == "seller.release_on_acceptance/1.0.0"
    assert RELEASE.check == "release_on_acceptance"
    assert CONFIG == {"release_classes": ["address"], "acceptance_classes": ["agreement.accept"],
                      "action_classes": ["disclosure.personal"]}
    assert "release_on_acceptance" in CONFIGURED_CHECKS
    # The rule is in the digested body: rewording it moves the digest.
    reworded = type(RELEASE)(RELEASE.wicket_id, RELEASE.check, RELEASE.config, RELEASE.semantics + " ")
    assert reworded.definition_digest() != RELEASE.definition_digest()


# -- pass ------------------------------------------------------------------------


def test_citing_the_sales_acceptance_by_the_recipient_passes(store, signer):
    accepted = _accept(_engine(store, signer), 1)
    outcome = _check(_share(cited_mandate_capsule_id=accepted), store)
    assert outcome.result == "pass"
    assert outcome.evidence == {"constraint_id": "release_on_acceptance", "representation_class": "address",
                                "bound_to_acceptance": True, "missing_field": None}
    _assert_value_free(outcome)


# -- fail: each removes one condition --------------------------------------------


def test_no_acceptance_sealed_and_none_cited_fails(store):
    outcome = _check(_share(), store)
    assert (outcome.result, outcome.reason) == ("fail", "the disclosure cites no acceptance")


def test_an_acceptance_sealed_but_not_cited_fails(store, signer):
    _accept(_engine(store, signer), 1)
    assert _check(_share(), store).result == "fail"


def test_the_acceptance_cited_to_another_recipient_fails(store, signer):
    """The counterparty match: A accepted, the address goes to B citing A's acceptance."""
    accepted = _accept(_engine(store, signer), 1)
    outcome = _check(_share(target=BUYER_B, cited_mandate_capsule_id=accepted), store)
    assert outcome.result == "fail"
    assert outcome.evidence["bound_to_acceptance"] is False
    _assert_value_free(outcome)


def test_another_buyers_acceptance_of_another_item_fails(store, signer):
    engine = _engine(store, signer)
    _accept(engine, 1)
    other = _accept(engine, 2, target=BUYER_B, item=OTHER_ITEM)
    outcome = _check(_share(cited_mandate_capsule_id=other), store)
    assert outcome.result == "fail"
    _assert_value_free(outcome)


@pytest.mark.parametrize(("field", "value"), [("task_authority_ref", OTHER_SALE), ("item_ref", OTHER_ITEM)])
def test_the_recipients_acceptance_of_another_sale_fails(store, signer, field, value):
    """The sale match: A's acceptance is real and to A, but of another sale."""
    other = _accept(_engine(store, signer), 1, **{"sale" if field == "task_authority_ref" else "item": value})
    assert _check(_share(cited_mandate_capsule_id=other), store).result == "fail"


def test_a_cited_record_that_is_not_an_acceptance_fails(store, signer):
    """The sale has a genuine acceptance by A; the disclosure to A cites an
    offer to A in the same sale instead."""
    engine = _engine(store, signer)
    offer = _accept(engine, 1, action_class="marketplace.offer")
    _accept(engine, 2)
    assert _check(_share(cited_mandate_capsule_id=offer), store).result == "fail"


def test_a_dry_run_acceptance_fails(store, signer):
    """A dry-run acceptance by A is sealed before the genuine one; citing the
    dry run does not release the address."""
    engine = _engine(store, signer)
    dry = _accept(engine, 1, dry_run=True)
    _accept(engine, 2)
    assert _check(_share(cited_mandate_capsule_id=dry), store).result == "fail"


def test_a_later_acceptance_of_the_same_sale_fails(store, signer):
    """The sale's acceptance is its first; with no single_commitment in force
    a second can be sealed, and citing it does not release the address."""
    engine = _engine(store, signer)
    _accept(engine, 1)
    later = _accept(engine, 2, target=BUYER_B)
    assert _check(_share(target=BUYER_B, cited_mandate_capsule_id=later), store).result == "fail"


class _NothingVerifies(LedgerStore):
    """A ledger that finds no record to verify (``verify`` returns None)."""

    def verify(self, capsule_id: str) -> VerificationResult | None:
        return None


class _NothingReverifies(LedgerStore):
    """A ledger whose verifier finds every record altered (``ok`` False)."""

    def verify(self, capsule_id: str) -> VerificationResult | None:
        return VerificationResult(ok=False, capsule_id=capsule_id)


@pytest.mark.parametrize("ledger_type", [_NothingVerifies, _NothingReverifies])
def test_an_acceptance_that_does_not_reverify_fails(tmp_path, signer, ledger_type):
    store = ledger_type(tmp_path / "ledger")
    try:
        accepted = _accept(_engine(store, signer), 1)
        assert _check(_share(cited_mandate_capsule_id=accepted), store).result == "fail"
    finally:
        store.close()


@pytest.mark.parametrize("field", ["task_authority_ref", "item_ref", "target"])
def test_a_missing_reference_fails_naming_the_field(store, signer, field):
    accepted = _accept(_engine(store, signer), 1)
    outcome = _check(_share(cited_mandate_capsule_id=accepted, **{field: None}), store)
    assert outcome.result == "fail"
    assert outcome.evidence["missing_field"] == field
    _assert_value_free(outcome)


@pytest.mark.parametrize("action_class", [None, "", "disclosure.Personal", "disclosure.private"])
def test_an_action_class_with_no_taxonomy_row_fails_closed(store, signer, action_class):
    """Without a known class the action cannot be shown to be out of scope:
    an address to a buyer who never accepted, with its class left out or
    misspelt, is refused, not passed as n/a."""
    accepted = _accept(_engine(store, signer), 1)
    outcome = _check(_share(action_class=action_class, target=BUYER_B, cited_mandate_capsule_id=accepted), store)
    assert outcome.result == "fail"
    assert outcome.evidence == {"constraint_id": "release_on_acceptance", "representation_class": None,
                                "bound_to_acceptance": False, "missing_field": "action_class"}
    _assert_value_free(outcome)


def test_a_disclosure_declaring_no_class_fails_closed(store):
    outcome = _check(_share(representation_class=None), store)
    assert outcome.result == "fail"
    assert outcome.evidence["missing_field"] == "representation_class"


# -- not applicable --------------------------------------------------------------


def test_another_personal_class_is_out_of_scope(store):
    outcome = _check(_share(representation_class="phone"), store)
    assert outcome.result == "n/a"
    assert outcome.evidence == {"constraint_id": "release_on_acceptance", "in_scope": False, "missing_field": None}


def test_another_action_class_is_out_of_scope(store):
    assert _check(_share(action_class="communication.send"), store).result == "n/a"


# -- the engine refuses; an approval does not clear it ---------------------------


def test_the_engine_refuses_a_failure_on_a_class_that_names_an_approver(store, signer):
    assert resolve("disclosure.personal").approver_role is not None
    decision = _engine(store, signer).check(_share())
    assert decision.outcome == DENY
    assert "release_on_acceptance=fail" in decision.reason


def test_the_engine_refuses_an_unknown_action_class(store, signer):
    decision = _engine(store, signer).check(_share(action_class=None, target=BUYER_B))
    assert decision.outcome == DENY
    assert "release_on_acceptance=fail" in decision.reason


def test_the_engine_allows_the_bound_release(store, signer):
    engine = _engine(store, signer)
    accepted = _accept(engine, 1)
    assert engine.check(_share(cited_mandate_capsule_id=accepted)).outcome == ALLOW
