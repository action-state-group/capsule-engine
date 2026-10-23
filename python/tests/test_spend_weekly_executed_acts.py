# SPDX-License-Identifier: Apache-2.0
"""``spend.weekly/3.1.0``: a replay counts every act a deal executed in the
7-day total, once, whoever approved it.

capsulectl seals a decide capsule only for ``pay``. A booking commit, a
signature or another non-pay act is sealed ``fyi``, with no decision, and an
act taken without a check is sealed as an outcome. A replay re-decides every
check as a dry run, and ``spend.weekly/3.0.0`` leaves dry runs out, so under
everyday 0.3.5 no act a deal executed ever reached the total. Under 0.3.6
(``caps/5.1.0``) the replay writes a record for each executed act when it
reaches it (``report/replay.py``, ``_executed_record``):

- an action step (``x-deal-v0`` ``action``, or a typed ``action-record/v0``)
  counts at the ``spend_minor`` its body seals;
- an act taken without a check (an ``outcome`` with ``body.unchecked``, or a
  typed ``action-outcome/v0`` with ``body.attempted``) counts at its
  ``spend_minor``, else its ``amount_minor``;
- an act whose money moved in (``direction: "in"``) counts 0;
- an act whose spend cannot be read (none, a negative one, or one that is not
  an integer) leaves caps ``n/a`` in scope with the reason "an earlier act has
  no recorded decision, so the total cannot count it", and the engine asks
  where it would allow: never allowed, and not refused for that alone.

None of these records is a decision or names a target, so none makes a payee
seen and none is the earlier act a repeat duplicates.
"""
from __future__ import annotations

import tempfile
from itertools import count
from pathlib import Path
from typing import NotRequired, TypedDict

import pytest
from agent_action_capsule import json_digest

import capsule_engine
from capsule_engine.guards.capsule import ALLOW, ESCALATE
from capsule_engine.guards.checks import RUNNABLE_CHECKS
from capsule_engine.guards.checks.caps import WINDOW_UNREADABLE_REASON
from capsule_engine.guards.classes import TAXONOMY_VERSION
from capsule_engine.guards.engine import NOT_EVALUABLE
from capsule_engine.packs import install_pack, load_pack_dir
from capsule_engine.packs.schema import PackDefinition
from capsule_engine.report.replay import ReplayResult, replay

REPO = Path(capsule_engine.__file__).parent.parent
PACK = load_pack_dir(REPO / "capsule_engine" / "packs" / "catalog" / "everyday")
FROZEN_0_3_5 = load_pack_dir(REPO / "tests" / "fixtures" / "packs" / "everyday-0.3.5")

OPERATOR = "household-a"
OTHER_OPERATOR = "household-b"
PAYEE = "a" * 64
DAY_1 = "2026-10-07T12:00:00Z"
DAY_2 = "2026-10-08T12:00:00Z"
DAY_3 = "2026-10-09T12:00:00Z"
NINE_DAYS_LATER = "2026-10-16T12:00:00Z"
DEAL_ALG = "hmac-sha256-deal-key"


class Disposition(TypedDict):
    decision: str


class Bound(TypedDict):
    capsule_id: str
    action_id: str
    action_type: str
    operator: str
    developer: str
    timestamp: str
    model_attestation: dict[str, dict[str, str]]
    disposition: NotRequired[Disposition]


class ActBody(TypedDict, total=False):
    """An act's body as capsulectl seals it (deal_profile.go actionBody)."""

    action: str
    action_class: str
    taxonomy_version: str
    currency: str
    amount_minor: int
    spend_minor: int | str | dict[str, int]
    direction: str


class Ref(TypedDict):
    rel: str
    type: str
    digest_alg: str
    digest: str


class Counterparty(TypedDict):
    fp_alg: str
    ids: dict[str, str]


class DealBlock(TypedDict, total=False):
    record_type: str
    deal_id: str
    seq: int
    refs: list[Ref]
    counterparty: Counterparty


class OutcomeBody(TypedDict, total=False):
    """An outcome's body: ``unchecked`` (``x-deal-v0``) or ``attempted``
    (typed) for an act taken without a check."""

    status: str
    outcome: str
    unchecked: ActBody
    attempted: ActBody


class Disclosed(TypedDict):
    to: str
    fields: list[str]


class DisclosureBody(TypedDict):
    action: str
    disclosed: Disclosed


Body = ActBody | OutcomeBody | DisclosureBody
_DealFields = TypedDict("_DealFields", {"body": Body, "x-deal-v0": DealBlock})


class DealRecord(_DealFields):
    """An ``x-deal-v0`` record."""


class TypedRecord(TypedDict):
    """capsulectl's typed header around a body."""

    type: str
    canonicalization: str
    chain_id: str
    seq: int
    at: str
    body: Body


Sealed = tuple[Bound, DealRecord | TypedRecord]

_SEQ = count(1)


def _bound(record: DealRecord | TypedRecord, at: str, operator: str) -> Bound:
    seq = next(_SEQ)
    return {
        "capsule_id": f"{seq:064x}",
        "action_id": f"deal/{seq}",
        "action_type": "fyi",
        "operator": operator,
        "developer": "capsulectl-deal",
        "timestamp": at,
        "model_attestation": {"compute_attestation": {"agent_input_digest": json_digest(record)}},
    }


def _act_body(action: str, amount: int | None, *, spend: int | None = None, direction: str | None = None,
              action_class: str = "money.purchase") -> ActBody:
    body: ActBody = {"action": action, "action_class": action_class, "taxonomy_version": TAXONOMY_VERSION,
                  "currency": "USD"}
    if amount is not None:
        body["amount_minor"] = amount
    if spend is not None:
        body["spend_minor"] = spend
    if direction is not None:
        body["direction"] = direction
    return body


def _deal_record(record_type: str, deal: str, body: Body, *, refs: list[Ref] | None = None,
                 payee: str | None = None) -> DealRecord:
    block: DealBlock = {"record_type": record_type, "deal_id": deal, "seq": next(_SEQ)}
    if refs is not None:
        block["refs"] = refs
    if payee is not None:
        block["counterparty"] = {"fp_alg": DEAL_ALG, "ids": {"payee": payee}}
    return {"body": body, "x-deal-v0": block}


def check(deal: str, amount: int, at: str, *, operator: str = OPERATOR, action: str = "pay",
          action_class: str = "money.purchase") -> Sealed:
    record = _deal_record("check", deal, _act_body(action, amount, spend=amount, action_class=action_class), payee=PAYEE)
    return _bound(record, at, operator), record


def acted(deal: str, body: ActBody, at: str, *, operator: str = OPERATOR) -> Sealed:
    """An executed ``x-deal-v0`` action step. Its ``authorized_by`` names no
    sealed approval here, so it is never carried out (never seen): its
    spend counts whoever approved it."""
    ref: Ref = {"rel": "authorized_by", "type": "deal-record", "digest_alg": "SHA-256", "digest": "f" * 64}
    record = _deal_record("action", deal, body, refs=[ref])
    return _bound(record, at, operator), record


def unchecked(deal: str, body: ActBody, at: str) -> Sealed:
    """An act taken without a check, as capsulectl seals it: an outcome."""
    record = _deal_record("outcome", deal, {"status": "unchecked_action", "outcome": "mismatch", "unchecked": body})
    return _bound(record, at, OPERATOR), record


def typed(type_name: str, deal: str, body: Body, at: str) -> Sealed:
    record: TypedRecord = {"type": type_name, "canonicalization": "jcs", "chain_id": deal, "seq": next(_SEQ), "at": at, "body": body}
    return _bound(record, at, OPERATOR), record


def _replay(*sealed: Sealed, pack: PackDefinition = PACK) -> ReplayResult:
    with tempfile.TemporaryDirectory() as tmp:
        installed = install_pack(pack, project_dir=Path(tmp) / "replay-project", mode="observe")
        resolved = installed.resolved
        return replay(
            [capsule for capsule, _ in sealed],
            caps_fold=resolved.caps_fold(),
            caps_minor=resolved.caps_minor(),
            per_action_minor=resolved.per_action_minor(),
            per_action_reads=resolved.per_action_reads(),
            manifest_digest=resolved.manifest_digest,
            disclosed={capsule["capsule_id"]: record for capsule, record in sealed},
            wickets=resolved.configured_wickets(RUNNABLE_CHECKS),
            pack=installed.pack,
        )


def _last(result: ReplayResult, check_id: str = "caps"):
    decision = result.decisions[-1].decision
    return decision, next(c for c in decision.constraints if c.id == check_id)


# -- what counts -----------------------------------------------------------------


def test_a_booking_commit_and_a_pay_in_one_week_both_count():
    """em-priv's first case: a non-pay act sealed fyi no longer leaves the
    window out of the total, and r05 evaluates after a booking."""
    _, caps = _last(_replay(
        check("deal-1", 2_000, DAY_1, action="commit"),
        acted("deal-1", _act_body("commit", 2_000, spend=2_000), DAY_1),
        check("deal-2", 1_500, DAY_2),
        acted("deal-2", _act_body("pay", 1_500, spend=1_500, direction="out"), DAY_2),
        check("deal-3", 1_000, DAY_3),
    ))
    assert caps.result == "pass"
    assert (caps.evidence["weekly_spend_minor"], caps.evidence["projected_minor"]) == (3_500, 4_500)
    assert "window_unreadable_count" not in caps.evidence


def test_a_signature_counts_at_its_sealed_spend():
    _, caps = _last(_replay(
        acted("deal-1", _act_body("sign", 2_200, spend=2_200, action_class="agreement.accept"), DAY_1),
        check("deal-2", 1_000, DAY_2),
    ))
    assert caps.evidence["weekly_spend_minor"] == 2_200


@pytest.mark.parametrize("action_class", ["money.refund", "agreement.accept"])
def test_money_in_counts_nothing_whatever_its_spend_says(action_class):
    """Money moved to the user (#130): its spend is 0 under any class, not
    only one the fold reads as a reversal, and it is never a reason the
    window cannot be read."""
    _, caps = _last(_replay(
        acted("deal-1", _act_body("refund", 2_000, spend=2_000, direction="in", action_class=action_class), DAY_1),
        check("deal-2", 1_000, DAY_2),
    ))
    assert (caps.result, caps.evidence["weekly_spend_minor"]) == ("pass", 0)
    assert "window_unreadable_count" not in caps.evidence


def test_the_window_counts_past_the_cap_and_asks():
    decision, caps = _last(_replay(
        *(acted(f"deal-{n}", _act_body("commit", 2_400, spend=2_400), DAY_1) for n in range(4)),
        check("deal-5", 2_000, DAY_2),
    ))
    assert (caps.result, caps.evidence["weekly_spend_minor"]) == ("fail", 9_600)
    assert caps.evidence["tripped"] == [{"limit": "window", "threshold_minor": 10_000, "observed_minor": 11_600}]
    assert decision.outcome == ESCALATE


def test_an_act_taken_without_a_check_counts_its_amount():
    """capsulectl seals it before its spend is classed: ``amount_minor``
    stands in, as the plugin reads it."""
    _, caps = _last(_replay(unchecked("deal-1", _act_body("pay", 1_800), DAY_1), check("deal-2", 1_000, DAY_2)))
    assert caps.evidence["weekly_spend_minor"] == 1_800


def test_typed_records_count_the_same_way():
    _, caps = _last(_replay(
        typed("action-record/v0", "deal-1", _act_body("commit", 1_200, spend=1_200), DAY_1),
        typed("action-outcome/v0", "deal-2", {"status": "unchecked_action", "attempted": _act_body("pay", 700)}, DAY_1),
        check("deal-3", 1_000, DAY_2),
    ))
    assert caps.evidence["weekly_spend_minor"] == 1_900


def test_a_typed_disclosure_moves_no_money():
    _, caps = _last(_replay(
        typed("action-record/v0", "deal-1", {"action": "disclose", "disclosed": {"to": "merchant", "fields": []}}, DAY_1),
        check("deal-2", 1_000, DAY_2),
    ))
    assert (caps.result, caps.evidence["weekly_spend_minor"]) == ("pass", 0)


def test_a_checked_act_counts_once_and_only_when_executed():
    """The check is decided as a dry run, which no total counts, even when it
    is allowed; its action step counts. A check that was allowed and never
    acted on adds nothing."""
    transfer = "money.transfer"
    result = _replay(
        check("deal-1", 2_000, DAY_1, action_class=transfer),
        acted("deal-1", _act_body("pay", 2_000, spend=2_000, direction="out", action_class=transfer), DAY_1),
        check("deal-2", 1_500, DAY_1, action_class=transfer),
        check("deal-3", 1_000, DAY_2, action_class=transfer),
    )
    assert [s.decision.outcome for s in result.decisions] == [ALLOW, ALLOW, ALLOW]
    _, caps = _last(result)
    assert caps.evidence["weekly_spend_minor"] == 2_000


def test_an_act_older_than_the_window_or_of_another_operator_is_not_counted():
    _, caps = _last(_replay(
        acted("deal-1", _act_body("commit", 2_000, spend=2_000), DAY_1),
        acted("deal-2", _act_body("commit", 900, spend=900), DAY_2, operator=OTHER_OPERATOR),
        check("deal-3", 1_000, NINE_DAYS_LATER),
    ))
    assert caps.evidence["weekly_spend_minor"] == 0


# -- an unreadable spend ---------------------------------------------------------


UNREADABLE = {"constraint_id": "caps", "in_scope": True, "missing_field": "spend_minor"}


def test_an_executed_act_whose_spend_cannot_be_read_is_not_evaluable_and_asks():
    """No spend: the total is not known. A transfer nothing else fails would
    be allowed; it is asked about instead, verdict not evaluable."""
    transfer = "money.transfer"
    decision, caps = _last(_replay(acted("deal-1", _act_body("sign", None), DAY_1),
                                   check("deal-2", 1_000, DAY_2, action_class=transfer)))
    assert (caps.result, caps.reason, caps.evidence) == ("n/a", WINDOW_UNREADABLE_REASON, UNREADABLE)
    assert [c.id for c in decision.constraints if c.result == "fail"] == []
    assert (decision.outcome, decision.verdict) == (ESCALATE, NOT_EVALUABLE)
    assert decision.asked_unevaluated_checks == ("caps",)


@pytest.mark.parametrize("spend", ["2000", -2_000, {"minor": 2_000}])
def test_a_spend_in_the_wrong_shape_or_negative_is_unreadable(spend):
    body = _act_body("commit", 2_000)
    body["spend_minor"] = spend
    _, caps = _last(_replay(acted("deal-1", body, DAY_1), check("deal-2", 1_000, DAY_2)))
    assert (caps.result, caps.evidence) == ("n/a", UNREADABLE)


def test_an_accepted_act_counts_its_amount():
    """As the fold counts an accepted decision: its amount_minor, whatever
    spend_minor it seals."""
    capsule, record = acted("deal-1", _act_body("pay", 1_700), DAY_1)
    capsule["disposition"] = {"decision": "accept"}
    _, caps = _last(_replay((capsule, record), check("deal-2", 1_000, DAY_2)))
    assert caps.evidence["weekly_spend_minor"] == 1_700


def test_a_per_action_limit_the_action_exceeds_still_fails():
    _, caps = _last(_replay(acted("deal-1", _act_body("sign", None), DAY_1), check("deal-2", 3_000, DAY_2)))
    assert caps.result == "fail"
    assert caps.reason.startswith("per_action limit 2500 exceeded by 3000")
    assert caps.evidence["window_unreadable_count"] == 1


def test_an_unreadable_act_leaves_the_window_when_it_ages_out():
    _, caps = _last(_replay(acted("deal-1", _act_body("sign", None), DAY_1), check("deal-2", 1_000, NINE_DAYS_LATER)))
    assert caps.result == "pass"
    assert "window_unreadable_count" not in caps.evidence


# -- what does not change --------------------------------------------------------


def test_an_executed_act_is_never_seen_and_never_the_earlier_act_of_a_repeat():
    """The record names no target and is not a decision: r06 still reads a
    first-time merchant, and dedupe matches nothing."""
    decision, seen = _last(_replay(
        unchecked("deal-1", _act_body("pay", 1_000), DAY_1),
        acted("deal-1", _act_body("commit", 1_000, spend=1_000), DAY_1),
        check("deal-2", 1_000, DAY_2),
    ), "counterparty_seen_before")
    assert (seen.result, seen.evidence["prior_count"]) == ("fail", 0)
    (dedupe,) = [c for c in decision.constraints if c.id == "dedupe"]
    assert dedupe.result == "pass"


def test_under_0_3_5_nothing_is_written_and_nothing_counts():
    """spend.weekly/3.0.0 keeps its meaning: the replay writes no record for
    an executed act, so every later decision keeps its bytes."""
    sealed = (
        acted("deal-1", _act_body("commit", 2_000, spend=2_000), DAY_1),
        acted("deal-2", _act_body("sign", None), DAY_1),
        check("deal-3", 1_000, DAY_2),
    )
    decision, caps = _last(_replay(*sealed, pack=FROZEN_0_3_5))
    assert (caps.result, caps.evidence["weekly_spend_minor"]) == ("pass", 0)
    assert "window_unreadable_count" not in caps.evidence
    assert decision.checkpoint["tree_size"] == 0
    _, current = _last(_replay(*sealed))
    assert (current.result, current.evidence) == ("n/a", UNREADABLE)
