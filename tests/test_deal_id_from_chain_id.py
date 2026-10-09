# SPDX-License-Identifier: Apache-2.0
"""The deal a check is in, read from its one source on each record kind.

An ``x-deal-v0`` record names its deal in ``x-deal-v0.deal_id``. A typed
record (``proposed-action/v0``) carries no ``x-deal-v0`` block and names it in
its top-level ``chain_id``, which capsulectl sets to exactly that value. So the
deal is ``x-deal-v0.deal_id`` when the record states it, else ``chain_id``,
and ``dedupe`` places a repeat in the same deal or another the same way for
both kinds.

A record stating both, with different values, is in no deal: neither is
picked. Its decision names the two fields (``deal_id_conflict``), never their
values, and what would be allowed is refused and sealed ``reject`` with
``verdict`` ``not_evaluable``, so the act is never counted later as seen or as
spend.

``party_role`` sits at the top level of the checker input beside ``record``
and ``item_ref``. A checker may read it to pick a pack; the engine reads only the
record entry and ``item_ref``, so an input carrying it is decided exactly as
one without it.
"""
from __future__ import annotations

from pathlib import Path
from typing import TypedDict

import pytest
from agent_action_capsule import json_digest
from capsule_ledger.ledger import LedgerStore

from capsule_engine.folds.loader import load_definition_file as load_fold
from capsule_engine.guards import GuardEngine, LocalSigner
from capsule_engine.guards.capsule import ALLOW, DENY, ESCALATE
from capsule_engine.guards.classes import TAXONOMY_VERSION
from capsule_engine.guards.engine import NOT_EVALUABLE
from capsule_engine.report.replay import action_for_check_input, action_for_record, replay

SPEND_WEEKLY = Path(__file__).parent.parent / "capsule_engine" / "folds" / "catalog_defs" / "spend.weekly.yaml"

ALG = "hmac-sha256-deal-key"
PAYEE = "a" * 64
DEAL_A = "deal-aaaaaaaaaaaaaaaa"
DEAL_B = "deal-bbbbbbbbbbbbbbbb"
DAY_1 = "2026-10-07T12:00:00Z"
DAY_2 = "2026-10-08T12:00:00Z"
ITEM = "c" * 64
BOTH_FIELDS = ("x-deal-v0.deal_id", "chain_id")
SIGNER = LocalSigner(key_id="deal-id-test-key", secret=b"deal-id-test-secret")


class CheckBody(TypedDict):
    action: str
    action_class: str
    taxonomy_version: str
    currency: str
    amount_minor: int
    spend_minor: int
    direction: str


class BoundCapsule(TypedDict):
    capsule_id: str
    action_id: str
    action_type: str
    operator: str
    developer: str
    timestamp: str
    model_attestation: dict[str, dict[str, str]]


class Counterparty(TypedDict):
    fp_alg: str
    ids: dict[str, str]


class DealBlock(TypedDict, total=False):
    record_type: str
    seq: int
    counterparty: Counterparty
    # Any value: what a record states is what is under test.
    deal_id: object


_UntypedFields = TypedDict("_UntypedFields", {"body": CheckBody, "x-deal-v0": DealBlock})


class UntypedCheck(_UntypedFields, total=False):
    """An ``x-deal-v0`` check, with a typed ``chain_id`` beside its block
    when the case states one."""

    chain_id: object


class TypedCheck(TypedDict):
    """capsulectl's typed header around a check body."""

    type: str
    canonicalization: str
    chain_id: object
    seq: int
    at: str
    body: CheckBody


Check = tuple[BoundCapsule, UntypedCheck | TypedCheck]


class Entry(BoundCapsule):
    """An external-check-input/v0 ``record`` entry."""

    agent_input: UntypedCheck | TypedCheck


class _EnvelopeFields(TypedDict):
    record: Entry
    item_ref: str


class Envelope(_EnvelopeFields, total=False):
    party_role: str


_SEQ = iter(range(1, 1_000))
# ``_untyped`` without a ``chain_id``: absent, which is not ``None``.
_ABSENT = object()


def _body() -> CheckBody:
    return {
        "action": "pay",
        "action_class": "money.purchase",
        "taxonomy_version": TAXONOMY_VERSION,
        "currency": "USD",
        "amount_minor": 4_500,
        "spend_minor": 4_500,
        "direction": "out",
    }


def _bound(record: UntypedCheck | TypedCheck, seq: int, at: str) -> BoundCapsule:
    return {
        "capsule_id": f"{seq:064x}",
        # One action_id prefix for every deal: the deal is never read from it.
        "action_id": f"deal-0000000000000000/{seq}",
        "action_type": "fyi",
        "operator": "household-a",
        "developer": "capsulectl-deal",
        "timestamp": at,
        "model_attestation": {"compute_attestation": {"agent_input_digest": json_digest(record)}},
    }


def _untyped(deal: object, at: str, *, chain_id: object = _ABSENT) -> tuple[BoundCapsule, UntypedCheck]:
    """An ``x-deal-v0`` check stating ``deal`` in its block (unless it is
    ``None``), and ``chain_id`` beside the block when one is given."""
    seq = next(_SEQ)
    block: DealBlock = {"record_type": "check", "seq": seq, "counterparty": {"fp_alg": ALG, "ids": {"payee": PAYEE}}}
    if deal is not None:
        block["deal_id"] = deal
    record: UntypedCheck = {"body": _body(), "x-deal-v0": block}
    if chain_id is not _ABSENT:
        record["chain_id"] = chain_id
    return _bound(record, seq, at), record


def _typed(chain: object, at: str) -> tuple[BoundCapsule, TypedCheck]:
    """A typed ``proposed-action/v0`` check naming its deal as ``chain_id``,
    in capsulectl's typed header."""
    seq = next(_SEQ)
    record: TypedCheck = {"type": "proposed-action/v0", "canonicalization": "jcs", "chain_id": chain, "seq": seq, "at": at,
              "body": _body()}
    return _bound(record, seq, at), record


def _replay(*checks: Check, caps_minor: dict[str, int] | None = None):
    result = replay(
        [capsule for capsule, _ in checks],
        caps_fold=load_fold(SPEND_WEEKLY),
        caps_minor=caps_minor,
        disclosed={capsule["capsule_id"]: record for capsule, record in checks},
    )
    return [sourced.decision for sourced in result.decisions]


def _dedupe(decision):
    return next(c for c in decision.constraints if c.id == "dedupe")


# -- one source per record kind --------------------------------------------------


def test_a_record_stating_only_x_deal_v0_deal_id_is_keyed_on_it():
    capsule, record = _untyped(DEAL_A, DAY_1)
    action = action_for_record(capsule, record)
    assert (action.deal_id, action.deal_id_conflict) == (DEAL_A, ())
    (decision,) = _replay((capsule, record))
    assert decision.capsule["asg_payload"]["deal_id"] == DEAL_A
    assert decision.deal_id_conflict is None


def test_a_typed_record_is_keyed_on_its_chain_id():
    capsule, record = _typed(DEAL_A, DAY_1)
    action = action_for_record(capsule, record)
    assert (action.deal_id, action.deal_id_conflict) == (DEAL_A, ())
    (decision,) = _replay((capsule, record))
    assert decision.capsule["asg_payload"]["deal_id"] == DEAL_A
    assert decision.verdict == decision.outcome == ALLOW


def test_a_record_stating_both_the_same_is_keyed_on_that_deal():
    capsule, record = _untyped(DEAL_A, DAY_1, chain_id=DEAL_A)
    action = action_for_record(capsule, record)
    assert (action.deal_id, action.deal_id_conflict) == (DEAL_A, ())
    (decision,) = _replay((capsule, record))
    assert (decision.outcome, decision.verdict) == (ALLOW, ALLOW)
    assert decision.capsule["asg_payload"]["deal_id"] == DEAL_A


@pytest.mark.parametrize("chain", [None, 7, ""])
def test_a_typed_chain_id_that_is_not_a_deal_names_none(chain):
    action = action_for_record(*_typed(chain, DAY_1))
    assert (action.deal_id, action.deal_id_conflict) == (None, ())


# -- a typed repeat is placed in its deal (dedupe) --------------------------------


def test_a_typed_repeat_in_the_same_deal_is_refused():
    first, second = _replay(_typed(DEAL_A, DAY_1), _typed(DEAL_A, DAY_2))
    assert first.outcome == ALLOW
    assert (_dedupe(second).result, second.outcome) == ("fail", DENY)
    assert "repeat" not in _dedupe(second).evidence


def test_a_typed_repeat_in_another_deal_asks():
    first, second = _replay(_typed(DEAL_A, DAY_1), _typed(DEAL_B, DAY_2))
    assert (_dedupe(second).result, _dedupe(second).evidence["repeat"]) == ("fail", "other_deal")
    assert second.outcome == ESCALATE


# -- both stated, and different: in no deal, never allowed -----------------------


@pytest.mark.parametrize(
    ("deal_id", "chain_id"),
    [(DEAL_A, DEAL_B), (DEAL_B, DEAL_A), ("", DEAL_A), (DEAL_A, ""), (7, DEAL_A), (DEAL_A, None)],
    ids=["a-b", "b-a", "empty-deal_id", "empty-chain_id", "number-deal_id", "null-chain_id"],
)
def test_a_record_stating_two_deals_names_both_fields_and_neither_deal(deal_id, chain_id):
    action = action_for_record(*_untyped(deal_id, DAY_1, chain_id=chain_id))
    assert (action.deal_id, action.deal_id_conflict) == (None, BOTH_FIELDS)


def test_a_conflicted_check_that_would_be_allowed_is_refused_not_evaluable():
    (decision,) = _replay(_untyped(DEAL_A, DAY_1, chain_id=DEAL_B))
    assert not any(c.result == "fail" for c in decision.constraints)
    assert (decision.outcome, decision.verdict) == (DENY, NOT_EVALUABLE)
    assert decision.capsule["disposition"]["decision"] == "reject"
    assert decision.deal_id_conflict == {"fields": BOTH_FIELDS}
    assert "deal_id" not in decision.capsule["asg_payload"]


def test_the_conflict_reason_names_the_fields_never_the_values():
    (decision,) = _replay(_untyped(DEAL_A, DAY_1, chain_id=DEAL_B))
    assert "x-deal-v0.deal_id and chain_id" in decision.reason
    assert "refused because its deal is not known" in decision.reason
    assert DEAL_A not in decision.reason and DEAL_B not in decision.reason


def test_a_conflicted_repeat_is_a_double_commit_never_another_deal():
    """Neither deal is picked, so nothing places the repeat in another deal:
    it is refused, never asked about, and links to no earlier act."""
    first, second = _replay(_untyped(DEAL_A, DAY_1), _untyped(DEAL_A, DAY_2, chain_id=DEAL_B))
    out = _dedupe(second)
    assert (out.result, second.outcome, second.verdict) == ("fail", DENY, DENY)
    assert "repeat" not in out.evidence
    assert first.capsule["capsule_id"][:16] not in out.reason
    assert second.capsule.get("chain_parent") is None


def test_a_conflicted_check_is_never_counted_as_spend():
    """Refused, so the weekly spend a later check in the deal reads leaves it
    out: under a cap of 50.00, 45.00 refused then 46.00 fits; counted, it
    would not."""
    conflicted = _untyped(DEAL_A, DAY_1, chain_id=DEAL_B)
    later = _untyped(DEAL_A, DAY_2)
    later[1]["body"].update(amount_minor=4_600, spend_minor=4_600)
    later[0]["model_attestation"]["compute_attestation"]["agent_input_digest"] = json_digest(later[1])
    first, second = _replay(conflicted, later, caps_minor={"money.purchase": 5_000})
    assert first.outcome == DENY
    caps = next(c for c in second.constraints if c.id == "caps")
    assert (caps.result, second.outcome) == ("pass", ALLOW)


# -- live: the checker input -------------------------------------------------------


def _entry(check: Check) -> Entry:
    capsule, record = check
    return Entry(**capsule, agent_input=record)


def test_live_a_typed_entry_is_keyed_on_its_chain_id():
    assert action_for_check_input(_entry(_typed(DEAL_A, DAY_1))).deal_id == DEAL_A


def test_live_a_conflicted_entry_names_both_fields_and_no_deal():
    action = action_for_check_input(_entry(_untyped(DEAL_A, DAY_1, chain_id=DEAL_B)))
    assert (action.deal_id, action.deal_id_conflict) == (None, BOTH_FIELDS)


def _live(envelope: Envelope):
    """What the engine reads of an external-check-input/v0 envelope: the
    ``record`` entry and the top-level ``item_ref``, nothing else."""
    return action_for_check_input(envelope["record"], item_ref=envelope.get("item_ref"))


def _decided(action, ledger_dir: Path):
    with LedgerStore(ledger_dir) as store:
        engine = GuardEngine(ledger=store, caps_fold=load_fold(SPEND_WEEKLY), signer_provider=lambda: SIGNER)
        return engine.check(action, dry_run=True)


@pytest.mark.parametrize("party_role", ["buyer", "seller", "neither"])
def test_an_input_carrying_party_role_is_decided_as_one_without_it(party_role, tmp_path):
    without = Envelope(record=_entry(_untyped(DEAL_A, DAY_1)), item_ref=ITEM)
    carrying = Envelope(**without, party_role=party_role)
    assert carrying["record"] is without["record"]
    assert _live(carrying) == _live(without)
    decided = [_decided(_live(envelope), tmp_path / name) for name, envelope in (("c", carrying), ("w", without))]
    assert decided[0].capsule == decided[1].capsule
    assert decided[0].capsule["asg_payload"]["deal_id"] == DEAL_A
