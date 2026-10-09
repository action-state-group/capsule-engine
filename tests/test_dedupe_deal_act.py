# SPDX-License-Identifier: Apache-2.0
"""``dedupe`` keys a deal check on the act it carries, not on the check record.

A capsulectl deal check is sealed as its own record (``action_type`` ``fyi``,
``action_id`` ``deal-<id>/<seq>``) and carries the act it proposes in its
body, stated against a pinned action taxonomy. The dedupe key is the act's:
operator, developer, its action class, the payee fingerprint as target, and
the amount. The check record's own type and id prefix never enter it. So a
second check carrying the same act within the window matches the decision on
the first and is refused, naming it; a different payee or amount is a
different act; and two checks never match merely because both are checks of
one deal. A dedupe failure is an integrity failure: refused, never sent to an
approver.
"""
from __future__ import annotations

from pathlib import Path
from typing import NotRequired, TypedDict

import pytest
from agent_action_capsule import json_digest
from capsule_ledger.ledger import LedgerStore

from capsule_engine.folds.loader import load_definition_file as load_fold
from capsule_engine.guards import Action, GuardEngine, LocalSigner
from capsule_engine.guards.capsule import DENY
from capsule_engine.guards.checks.dedupe import equivalence_key_for_action, equivalence_key_for_capsule
from capsule_engine.report.replay import action_for_record, replay

PACKAGE_DIR = Path(__file__).parent.parent / "capsule_engine"
SPEND_WEEKLY = PACKAGE_DIR / "folds" / "catalog_defs" / "spend.weekly.yaml"
EVERYDAY_DIR = PACKAGE_DIR / "packs" / "catalog" / "everyday"

ALG = "hmac-sha256-deal-key"
PAYEE = "a" * 64
OTHER_PAYEE = "b" * 64
FIRST_AT = "2026-10-07T12:00:00Z"
AN_HOUR_LATER = "2026-10-07T13:00:00Z"
FORTY_DAYS_LATER = "2026-11-16T12:00:00Z"  # past the guard's 30-day dedupe window


class CheckBody(TypedDict):
    """The body of a deal check record, as these tests seal it."""

    action: str
    action_class: str
    taxonomy_version: str
    currency: str
    amount_minor: int
    spend_minor: int
    direction: NotRequired[str]


class Counterparty(TypedDict):
    fp_alg: str
    ids: dict[str, str]


class DealBlock(TypedDict):
    record_type: str
    deal_id: str
    seq: int
    counterparty: Counterparty


CheckRecord = TypedDict("CheckRecord", {"body": CheckBody, "x-deal-v0": DealBlock})


class BoundCapsule(TypedDict):
    """A capsule that seals a deal check by its ``agent_input_digest``."""

    capsule_id: str
    action_id: str
    action_type: str
    operator: str
    developer: str
    timestamp: str
    model_attestation: dict[str, dict[str, str]]


def _check(
    seq: int,
    *,
    at: str = FIRST_AT,
    payee: str = PAYEE,
    amount: int = 4_500,
    action: str = "pay",
    action_class: str = "money.purchase",
    record_type: str = "check",
    action_type: str = "fyi",
) -> tuple[BoundCapsule, CheckRecord]:
    """Deal check ``seq`` of one deal, carrying an act, and the capsule that
    seals it."""
    record: CheckRecord = {
        "body": {
            "action": action,
            "action_class": action_class,
            "taxonomy_version": "3",
            "currency": "USD",
            "amount_minor": amount,
            "spend_minor": amount,
        },
        "x-deal-v0": {
            "record_type": record_type,
            "deal_id": "deal-0000000000000000",
            "seq": seq,
            "counterparty": {"fp_alg": ALG, "ids": {"payee": payee}},
        },
    }
    capsule: BoundCapsule = {
        "capsule_id": f"{seq:064x}",
        "action_id": f"deal-0000000000000000/{seq}",
        "action_type": action_type,
        "operator": "household-a",
        "developer": "capsulectl-deal",
        "timestamp": at,
        "model_attestation": {"compute_attestation": {"agent_input_digest": json_digest(record)}},
    }
    return capsule, record


def _replay(*checks: tuple[BoundCapsule, CheckRecord]):
    """Each check's decision, replayed in order through one guard."""
    result = replay(
        [capsule for capsule, _ in checks],
        caps_fold=load_fold(SPEND_WEEKLY),
        disclosed={capsule["capsule_id"]: record for capsule, record in checks},
    )
    return [sourced.decision for sourced in result.decisions]


def _dedupe(decision):
    return next(c for c in decision.constraints if c.id == "dedupe")


def test_a_second_check_carrying_the_same_act_is_refused_naming_the_first():
    first, second = _replay(_check(2), _check(7, at=AN_HOUR_LATER))
    assert _dedupe(first).result == "pass"
    dedupe = _dedupe(second)
    assert dedupe.result == "fail"
    assert second.outcome == DENY
    earlier = first.capsule["capsule_id"]
    assert dedupe.evidence["matched_capsule_id"] == earlier
    assert earlier[:16] in dedupe.reason
    assert second.capsule["chain"]["parent_capsule_id"] == earlier


@pytest.mark.parametrize(
    "change",
    [{"payee": OTHER_PAYEE}, {"amount": 4_501}, {"action": "cancel", "action_class": "money.refund"}],
    ids=["another-payee", "another-amount", "another-verb"],
)
def test_a_check_carrying_a_different_act_passes(change):
    _, second = _replay(_check(2), _check(7, at=AN_HOUR_LATER, **change))
    assert _dedupe(second).result == "pass"


def test_the_same_act_outside_the_window_passes():
    _, second = _replay(_check(2), _check(7, at=FORTY_DAYS_LATER))
    assert _dedupe(second).result == "pass"


def test_the_check_records_own_type_and_id_never_enter_the_key():
    capsule, record = _check(2)
    keyed = equivalence_key_for_action(action_for_record(capsule, record))
    for action_type, action_id in (("decide", "deal-0000000000000000/2"), ("fyi", "deal-ffffffffffffffff/9")):
        other = {**capsule, "action_type": action_type, "action_id": action_id}
        assert equivalence_key_for_action(action_for_record(other, record)) == keyed


def test_two_checks_of_one_deal_never_match_merely_by_being_checks():
    """Same record type, same deal id prefix, same ``fyi`` type: only the
    acts differ, and the acts are what is compared."""
    first, second = _replay(_check(2), _check(7, at=AN_HOUR_LATER, payee=OTHER_PAYEE, amount=1_200))
    assert (first.capsule["action_type"], second.capsule["action_type"]) == ("fyi", "fyi")
    assert _dedupe(second).result == "pass"


def test_the_decision_on_a_check_recomputes_the_key_of_the_act_it_carries():
    """Both sides go through one formula: the sealed decision capsule
    projects to the same key as the action it decided, and it keeps the
    record's id and type, which the dry-run report maps its rows by."""
    capsule, record = _check(2)
    action = action_for_record(capsule, record)
    (decision,) = _replay((capsule, record))
    assert equivalence_key_for_capsule(decision.capsule) == equivalence_key_for_action(action)
    assert (decision.capsule["action_type"], decision.capsule["action_id"]) == ("fyi", "deal-0000000000000000/2")


def test_a_check_matches_the_same_act_decided_under_another_record_type(tmp_path):
    """The scan is not limited to the check's own ``fyi`` type: an earlier
    act decided as ``decide`` under another id, with the same act fields,
    is the same act."""
    signer = LocalSigner(key_id="dedupe-deal-act", secret=b"dedupe-deal-act")
    capsule, record = _check(7, at=AN_HOUR_LATER)
    check = action_for_record(capsule, record)
    earlier = Action(
        verb="pay",
        operator=check.operator,
        developer=check.developer,
        action_class=check.action_class,
        action_id="pay/elsewhere-1",
        timestamp=FIRST_AT,
        amount_minor=check.amount_minor,
        currency=check.currency,
        target=check.target,
        taxonomy_version=check.taxonomy_version,
    )
    with LedgerStore(tmp_path / "ledger") as store:
        engine = GuardEngine(ledger=store, caps_fold=load_fold(SPEND_WEEKLY), signer_provider=lambda: signer)
        first = engine.check(earlier, dry_run=True)
        second = engine.check(check, dry_run=True)
    assert (second.outcome, _dedupe(second).evidence["matched_capsule_id"]) == (DENY, first.capsule["capsule_id"])


def test_an_action_without_a_pinned_taxonomy_keeps_its_key():
    """The amount enters only an act stated against a pinned taxonomy, so
    every other action's key, and every key already recorded, is unchanged."""
    action = Action(verb="transfer_funds", operator="op", developer="dev", amount_minor=100, target="t")
    legacy = json_digest(
        {"operator": "op", "developer": "dev", "action_type": "decide", "verb": "transfer_funds", "target": "t"}
    )
    assert equivalence_key_for_action(action) == legacy
    assert equivalence_key_for_action(Action(verb="transfer_funds", operator="op", developer="dev", amount_minor=999, target="t")) == legacy


def test_a_repeated_check_is_refused_not_escalated_on_a_class_with_an_approver(tmp_path):
    """``money.purchase`` names an approver under the everyday pack; a
    dedupe failure is still refused, never sent to it."""
    from capsule_engine.packs import build_engine, install_pack, load_pack_dir

    installed = install_pack(load_pack_dir(EVERYDAY_DIR), project_dir=tmp_path / "project", mode="enforce")
    signer = LocalSigner(key_id="dedupe-deal-act", secret=b"dedupe-deal-act")
    with LedgerStore(tmp_path / "ledger") as store:
        engine = build_engine(installed, ledger=store, signer_provider=lambda: signer)
        first = engine.check(action_for_record(*_check(2, amount=500)), dry_run=True)
        second = engine.check(action_for_record(*_check(7, at=AN_HOUR_LATER, amount=500)), dry_run=True)
    assert _dedupe(first).result == "pass"
    assert _dedupe(second).result == "fail"
    assert second.outcome == DENY
