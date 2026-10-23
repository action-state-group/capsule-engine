# SPDX-License-Identifier: Apache-2.0
"""``spend.weekly/3.0.0``: a cancel or refund that cites the charge it reverses
takes that charge back out of the weekly total, and dry runs never count.

``spend.weekly/2.0.0`` summed every accepted amount whatever its class, so a
cancellation added to spend: buying and cancelling the same booking counted
twice against the limit. It also counted dry-run decisions, which are
recorded as accepted but never ran.

The reversal is linked, not inferred from the class alone: a cancel or refund
reduces the total only when its chain block cites a prior counted charge in
the same partition with the registered ``supersedes`` relation, and never by
more than that charge has left. An unlinked one neither adds nor subtracts,
and the evidence counts it as unlinked.
"""
from __future__ import annotations

from pathlib import Path
from typing import NotRequired, TypedDict

import pytest

from capsule_engine.folds.definition import parse_definition
from capsule_engine.folds.engine import evaluate_one
from capsule_engine.folds.errors import FoldDefinitionError
from capsule_engine.folds.loader import load_definition_file
from capsule_engine.guards import Action, GuardEngine
from capsule_engine.guards.wickets.catalog import Catalog as WicketCatalog

PACKAGE_DIR = Path(__file__).parent.parent / "capsule_engine"
CATALOG_DIR = PACKAGE_DIR / "folds" / "catalog_defs"
WICKET_CATALOG_DIR = PACKAGE_DIR / "guards" / "wickets" / "catalog_defs"

OPERATOR = "household-a"
OTHER_OPERATOR = "household-b"
AS_OF = "2026-10-07T23:00:00Z"


def _v3():
    return load_definition_file(CATALOG_DIR / "spend.weekly.v3.yaml")


# -- the earlier versions are untouched ------------------------------------------


@pytest.mark.parametrize(
    ("filename", "digest"),
    [
        ("spend.weekly.yaml", "3e7f6a3e8707de92b120bbc2aa0b5a78032df5d36e99f9955dd3ba948bba5e9c"),
        ("spend.weekly.v2.yaml", "a27c581b44a9735b6fad42ec619ac8e22928da6719dec59259549247726f3f10"),
        ("counterparty.seen_before.yaml", "c43b2011b4d49cf54a0306e9637ecfd3a945ff7febc13283507dd77513ae9b28"),
    ],
)
def test_earlier_fold_versions_keep_their_digests(filename, digest):
    definition = load_definition_file(CATALOG_DIR / filename)
    assert definition.reversal is None
    assert definition.definition_digest() == digest


def test_caps_v4_cites_the_v3_fold_and_does_not_cap_a_reversal_class():
    config = WicketCatalog(WICKET_CATALOG_DIR).get("caps/4.0.0").definition.config
    fold = _v3()
    assert config["fold_id"] == "spend.weekly/3.0.0" == fold.fold_id
    assert config["fold_digest"] == fold.definition_digest()
    assert set(config["per_action_minor"]) == set(config["caps_minor"])
    assert not set(config["caps_minor"]) & set(fold.reversal.classes)
    v3_classes = set(WicketCatalog(WICKET_CATALOG_DIR).get("caps/3.0.0").definition.config["caps_minor"])
    assert v3_classes - set(config["caps_minor"]) == {"booking.cancel"}


# -- the fold over plain records ---------------------------------------------------


class Chain(TypedDict):
    parent_capsule_id: str
    relation: str


class Checkpoint(TypedDict):
    tree_size: int
    dry_run: NotRequired[bool]


class Payload(TypedDict):
    amount_minor: int
    action_class: NotRequired[str]
    checkpoint: Checkpoint


class Disposition(TypedDict):
    decision: str


class Record(TypedDict):
    """The fields of a sealed decision capsule this fold reads."""

    capsule_id: str
    operator: str
    timestamp: str
    disposition: Disposition
    asg_payload: Payload
    chain: NotRequired[Chain]


def _record(
    capsule_id: str,
    action_class: str,
    amount: int,
    *,
    operator: str = OPERATOR,
    timestamp: str = "2026-10-07T10:00:00Z",
    decision: str = "accept",
    chain: Chain | None = None,
    dry_run: bool = False,
) -> Record:
    checkpoint = Checkpoint(tree_size=0)
    if dry_run:
        checkpoint["dry_run"] = True
    record = Record(
        capsule_id=capsule_id,
        operator=operator,
        timestamp=timestamp,
        disposition=Disposition(decision=decision),
        asg_payload=Payload(amount_minor=amount, action_class=action_class, checkpoint=checkpoint),
    )
    if chain is not None:
        record["chain"] = chain
    return record


def _cites(parent: str, relation: str = "supersedes") -> Chain:
    return Chain(parent_capsule_id=parent, relation=relation)


def _fold(records: list[Record], operator: str = OPERATOR):
    return evaluate_one(_v3(), records, key_value=operator, as_of=AS_OF)


def test_a_linked_cancel_takes_the_charge_back_out():
    trace = _fold([_record("c1", "booking.create", 55_880), _record("x1", "booking.cancel", 55_880, chain=_cites("c1"))])
    assert trace.result == 0
    assert trace.reversals == {"linked_count": 1, "linked_minor": 55_880, "unlinked_count": 0}


def test_a_linked_refund_takes_the_charge_back_out():
    trace = _fold([_record("c1", "money.purchase", 4_000), _record("r1", "money.refund", 4_000, chain=_cites("c1"))])
    assert trace.result == 0


def test_an_unlinked_cancel_neither_adds_nor_subtracts():
    trace = _fold([_record("c1", "booking.create", 2_000), _record("x1", "booking.cancel", 2_000)])
    assert trace.result == 2_000
    assert trace.reversals == {"linked_count": 0, "linked_minor": 0, "unlinked_count": 1}


def test_a_cancel_alone_does_not_drive_the_total_below_zero():
    trace = _fold([_record("x1", "booking.cancel", 2_000, chain=_cites("never-recorded"))])
    assert trace.result == 0
    assert trace.reversals["unlinked_count"] == 1


@pytest.mark.parametrize("relation", ["confirms", "follows", "duplicates", "reverses"])
def test_a_cancel_citing_its_charge_under_another_relation_is_unlinked(relation):
    trace = _fold([_record("c1", "booking.create", 2_000), _record("x1", "booking.cancel", 2_000, chain=_cites("c1", relation))])
    assert trace.result == 2_000
    assert trace.reversals["unlinked_count"] == 1


def test_a_cancel_never_decrements_more_than_its_charge():
    trace = _fold([_record("c1", "booking.create", 1_000), _record("x1", "booking.cancel", 3_000, chain=_cites("c1"))])
    assert trace.result == 0
    assert trace.reversals == {"linked_count": 1, "linked_minor": 1_000, "unlinked_count": 0}


def test_two_cancels_of_one_charge_reverse_it_once():
    records = [
        _record("c0", "booking.create", 5_000),
        _record("c1", "booking.create", 2_000),
        _record("x1", "booking.cancel", 2_000, chain=_cites("c1")),
        _record("x2", "booking.cancel", 2_000, chain=_cites("c1")),
    ]
    trace = _fold(records)
    assert trace.result == 5_000
    assert trace.reversals == {"linked_count": 1, "linked_minor": 2_000, "unlinked_count": 1}


def test_partial_refunds_reverse_up_to_the_charge():
    records = [
        _record("c1", "money.purchase", 3_000),
        _record("r1", "money.refund", 1_000, chain=_cites("c1")),
        _record("r2", "money.refund", 1_500, chain=_cites("c1")),
        _record("r3", "money.refund", 1_500, chain=_cites("c1")),
    ]
    trace = _fold(records)
    assert trace.result == 0
    assert trace.reversals == {"linked_count": 3, "linked_minor": 3_000, "unlinked_count": 0}


def test_a_cancel_citing_another_operators_charge_is_unlinked():
    records = [
        _record("c0", "booking.create", 3_000),
        _record("c1", "booking.create", 2_000, operator=OTHER_OPERATOR),
        _record("x1", "booking.cancel", 2_000, chain=_cites("c1")),
    ]
    trace = _fold(records)
    assert trace.result == 3_000
    assert trace.reversals == {"linked_count": 0, "linked_minor": 0, "unlinked_count": 1}
    assert _fold(records, OTHER_OPERATOR).result == 2_000


def test_a_cancel_citing_a_charge_outside_the_window_is_unlinked():
    records = [
        _record("c0", "booking.create", 3_000),
        _record("c1", "booking.create", 2_000, timestamp="2026-09-20T10:00:00Z"),
        _record("x1", "booking.cancel", 2_000, chain=_cites("c1")),
    ]
    trace = _fold(records)
    assert trace.result == 3_000
    assert trace.reversals["unlinked_count"] == 1


@pytest.mark.parametrize(("decision", "dry_run"), [("block", False), ("accept", True)])
def test_a_cancel_citing_a_charge_that_never_counted_is_unlinked(decision, dry_run):
    records = [
        _record("c0", "booking.create", 3_000),
        _record("c1", "booking.create", 2_000, decision=decision, dry_run=dry_run),
        _record("x1", "booking.cancel", 2_000, chain=_cites("c1")),
    ]
    trace = _fold(records)
    assert trace.result == 3_000
    assert trace.reversals == {"linked_count": 0, "linked_minor": 0, "unlinked_count": 1}


def test_a_cancel_citing_a_cancel_is_unlinked():
    records = [
        _record("c1", "booking.create", 2_000),
        _record("x1", "booking.cancel", 2_000),
        _record("x2", "booking.cancel", 2_000, chain=_cites("x1")),
    ]
    trace = _fold(records)
    assert trace.result == 2_000
    assert trace.reversals["unlinked_count"] == 2


def test_a_cancel_recorded_before_its_charge_is_unlinked():
    records = [
        _record("x1", "booking.cancel", 2_000, chain=_cites("c1")),
        _record("c1", "booking.create", 2_000),
    ]
    trace = _fold(records)
    assert trace.result == 2_000
    assert trace.reversals["unlinked_count"] == 1


def test_a_negative_charge_counts_as_zero():
    trace = _fold([_record("c0", "money.purchase", 3_000), _record("c1", "money.purchase", -9_000)])
    assert trace.result == 3_000


@pytest.mark.parametrize("amount", [0, -5_000])
def test_a_reversal_of_zero_or_less_is_unlinked_and_leaves_the_charge(amount):
    records = [
        _record("c1", "booking.create", 1_000),
        _record("x1", "booking.cancel", amount, chain=_cites("c1")),
        _record("x2", "booking.cancel", 6_000, chain=_cites("c1")),
    ]
    trace = _fold(records)
    assert trace.result == 0
    assert trace.reversals == {"linked_count": 1, "linked_minor": 1_000, "unlinked_count": 1}


def test_a_parent_that_is_not_a_capsule_id_is_unlinked():
    reversal = _record("x1", "booking.cancel", 2_000)
    reversal["chain"] = {"parent_capsule_id": ["c1"], "relation": "supersedes"}
    trace = _fold([_record("c1", "booking.create", 2_000), reversal])
    assert trace.result == 2_000
    assert trace.reversals["unlinked_count"] == 1


def test_a_denied_or_dry_run_cancel_does_not_reverse():
    records = [
        _record("c1", "booking.create", 2_000),
        _record("x1", "booking.cancel", 2_000, decision="block", chain=_cites("c1")),
        _record("x2", "booking.cancel", 2_000, dry_run=True, chain=_cites("c1")),
    ]
    trace = _fold(records)
    assert trace.result == 2_000
    assert trace.reversals == {"linked_count": 0, "linked_minor": 0, "unlinked_count": 0}


def test_a_dry_run_charge_does_not_count():
    trace = _fold([_record("c1", "money.purchase", 2_000), _record("c2", "money.purchase", 9_000, dry_run=True)])
    assert trace.result == 2_000


def test_v2_still_counts_what_v3_leaves_out():
    """The defect, pinned on the old version so the new one is the only fix."""
    records = [
        _record("c1", "booking.create", 2_000),
        _record("x1", "booking.cancel", 2_000, chain=_cites("c1")),
        _record("c2", "money.purchase", 9_000, dry_run=True),
    ]
    v2 = load_definition_file(CATALOG_DIR / "spend.weekly.v2.yaml")
    trace = evaluate_one(v2, records, key_value=OPERATOR, as_of=AS_OF)
    assert trace.result == 13_000
    assert trace.reversals is None


def test_an_operator_with_no_records_reads_zero_reversals():
    trace = _fold([_record("c1", "booking.create", 2_000, operator=OTHER_OPERATOR)])
    assert trace.result == 0
    assert trace.reversals == {"linked_count": 0, "linked_minor": 0, "unlinked_count": 0}


def test_a_record_with_no_class_or_chain_still_counts_as_a_charge():
    record = _record("c1", "money.transfer", 2_000)
    del record["asg_payload"]["action_class"]
    assert _fold([record]).result == 2_000


# -- the definition grammar ------------------------------------------------------


class RawRead(TypedDict):
    path: str
    erasure_class: str
    default: NotRequired[None]


class RawReduce(TypedDict):
    reducer: str
    field: str


class RawReversal(TypedDict):
    class_field: str
    classes: list[str]
    parent_field: str
    relation_field: str
    relation: str


class RawDefinition(TypedDict):
    """A fold definition as authored, before ``parse_definition``."""

    fold_id: str
    reads: list[RawRead]
    key: str
    reduce: RawReduce
    reversal: RawReversal
    emit: str


def _definition(reduce: RawReduce | None = None) -> RawDefinition:
    data: RawDefinition = {
        "fold_id": "spend.test/1.0.0",
        "reads": [
            {"path": "operator", "erasure_class": "commitment-ok"},
            {"path": "asg_payload.amount_minor", "erasure_class": "commitment-ok"},
            {"path": "asg_payload.action_class", "erasure_class": "commitment-ok", "default": None},
            {"path": "chain.parent_capsule_id", "erasure_class": "commitment-ok", "default": None},
            {"path": "chain.relation", "erasure_class": "commitment-ok", "default": None},
        ],
        "key": "operator",
        "reduce": {"reducer": "sum", "field": "asg_payload.amount_minor"},
        "reversal": {
            "class_field": "asg_payload.action_class",
            "classes": ["booking.cancel"],
            "parent_field": "chain.parent_capsule_id",
            "relation_field": "chain.relation",
            "relation": "supersedes",
        },
        "emit": "total",
    }
    if reduce is not None:
        data["reduce"] = reduce
    return data


def test_the_reversal_clause_is_part_of_the_digest():
    with_clause = parse_definition(_definition())
    without = parse_definition({k: v for k, v in _definition().items() if k != "reversal"})
    assert with_clause.canonical_dict()["reversal"]["relation"] == "supersedes"
    assert with_clause.definition_digest() != without.definition_digest()


@pytest.mark.parametrize("relation", ["reverses", "follows", ""])
def test_a_reversal_must_use_a_registered_terminal_relation(relation):
    data = _definition()
    data["reversal"] = {**data["reversal"], "relation": relation}
    with pytest.raises(FoldDefinitionError, match="relation"):
        parse_definition(data)


@pytest.mark.parametrize("field", ["class_field", "parent_field", "relation_field"])
def test_a_reversal_reads_only_declared_fields(field):
    data = _definition()
    data["reversal"] = {**data["reversal"], field: "asg_payload.undeclared"}
    with pytest.raises(FoldDefinitionError, match="undeclared"):
        parse_definition(data)


def test_a_reversal_needs_the_sum_reducer():
    with pytest.raises(FoldDefinitionError, match="sum"):
        parse_definition(_definition(reduce={"reducer": "max", "field": "asg_payload.amount_minor"}))


def test_a_reversal_needs_at_least_one_class():
    data = _definition()
    data["reversal"] = {**data["reversal"], "classes": []}
    with pytest.raises(FoldDefinitionError, match="classes"):
        parse_definition(data)


# -- through the guard engine ----------------------------------------------------

BOOKING_WINDOW_MINOR = 60_000
PURCHASE_LIMIT_MINOR = 10_000


def _engine(store, signer) -> GuardEngine:
    return GuardEngine(
        ledger=store,
        caps_fold=_v3(),
        signer_provider=lambda: signer,
        caps_minor={"booking.create": BOOKING_WINDOW_MINOR, "money.purchase": BOOKING_WINDOW_MINOR},
        per_action_minor={"booking.create": BOOKING_WINDOW_MINOR, "money.purchase": PURCHASE_LIMIT_MINOR},
    )


def _action(n: int, verb: str, action_class: str, amount: int) -> Action:
    return Action(
        verb=verb,
        operator=OPERATOR,
        developer="travel-assistant@v1",
        action_class=action_class,
        action_id=f"{verb}/{n}",
        amount_minor=amount,
        currency="USD",
        target=f"booking/{n}",
        timestamp=f"2026-10-07T1{n}:00:00Z",
    )


def _caps(decision):
    (caps,) = [c for c in decision.constraints if c.id == "caps"]
    return caps


def test_buy_then_linked_cancel_twice_leaves_room_for_a_small_purchase(store, signer):
    engine = _engine(store, signer)
    for cycle in (0, 2):
        booked = engine.check(_action(cycle, "book", "booking.create", 55_880))
        assert booked.outcome == "allow"
        assert _caps(booked).evidence["weekly_spend_minor"] == 0
        cancelled = engine.check(
            _action(cycle + 1, "cancel", "booking.cancel", 55_880),
            chain_parent=booked.capsule["capsule_id"],
            chain_relation="supersedes",
        )
        assert cancelled.outcome == "allow"
        assert cancelled.capsule["chain"]["relation"] == "supersedes"

    small = engine.check(_action(5, "buy", "money.purchase", 1_000))
    caps = _caps(small)
    assert small.outcome == "allow"
    assert caps.method == "spend.weekly/3.0.0"
    assert caps.evidence["fold"] == _v3().definition_digest()
    assert caps.evidence["weekly_spend_minor"] == 0
    assert caps.evidence["reversals"] == {"linked_count": 2, "linked_minor": 111_760, "unlinked_count": 0}


def test_an_unlinked_cancel_through_the_engine_leaves_the_total(store, signer):
    engine = _engine(store, signer)
    assert engine.check(_action(1, "book", "booking.create", 20_000)).outcome == "allow"
    assert engine.check(_action(2, "cancel", "booking.cancel", 20_000)).outcome == "allow"

    caps = _caps(engine.check(_action(3, "buy", "money.purchase", 1_000)))
    assert caps.evidence["weekly_spend_minor"] == 20_000
    assert caps.evidence["reversals"] == {"linked_count": 0, "linked_minor": 0, "unlinked_count": 1}


def test_a_dry_run_through_the_engine_does_not_count(store, signer):
    engine = _engine(store, signer)
    dry = engine.check(_action(1, "buy", "money.purchase", 9_000), dry_run=True)
    assert dry.outcome == "allow"
    assert dry.capsule["asg_payload"]["checkpoint"]["dry_run"] is True

    caps = _caps(engine.check(_action(2, "buy", "money.purchase", 1_000)))
    assert caps.evidence["weekly_spend_minor"] == 0


def test_the_first_check_on_an_empty_ledger_carries_a_zero_summary(store, signer):
    caps = _caps(_engine(store, signer).check(_action(1, "buy", "money.purchase", 1_000)))
    assert caps.evidence["reversals"] == {"linked_count": 0, "linked_minor": 0, "unlinked_count": 0}


def test_a_v2_fold_keeps_the_evidence_shape_it_had(store, signer):
    engine = GuardEngine(
        ledger=store,
        caps_fold=load_definition_file(CATALOG_DIR / "spend.weekly.v2.yaml"),
        signer_provider=lambda: signer,
        caps_minor={"money.purchase": PURCHASE_LIMIT_MINOR},
    )
    caps = _caps(engine.check(_action(1, "buy", "money.purchase", 1_000)))
    assert "reversals" not in caps.evidence
