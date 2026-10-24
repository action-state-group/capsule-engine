# SPDX-License-Identifier: Apache-2.0
"""The ledger a live check decides on, built from its check input's history.

capsulectl hands its rules checker one external-check-input/v0 envelope per
check: the ``record`` being checked and, as ``history``, the profile's
earlier acts from every deal, newest first, each one the sealed capsule of an
act (an ``x-deal-v0`` action step, or a typed ``action-record/v0``) with its
disclosed ``agent_input`` and, beside it, the deal's ``item_ref`` when the
deal is a sale's thread.
``history_scope.complete`` says whether every act in the window is there.

``history_ledger`` writes those acts into the ledger the engine reads, once,
before the check, so every check that reads earlier acts (``dedupe``,
``counterparty_seen_before``, ``single_commitment``) reads one ledger built
one way. Each act is read by ``action_for_history_entry`` and written as the
record a decision on it would carry (``act_payload``), in ledger order:
oldest first by the capsule's ``timestamp``, with acts of the same second in
the order the envelope gives them (capsulectl's sort is stable, so that is
the order they were sealed in). An act given twice (the same ``capsule_id``)
is written once. A history entry that is a claim the user's
agent made to the counterparty is written as the replay writes it
(``statement_for_history_entry``), in state ``statement``, so ``required_disclosure``
reads a statement made first in the deal the same way live and in a replay.
It is not an act, so it is never read as an unread one and never adds to a
spend window. Only a claim sealed at or before the checked record's
``timestamp`` is written, so a claim made after the check never counts for
it; when the checked record's time cannot be read, no claim is written. A
claim given twice is written once. The claims capsulectl passes beside the
checked record as ``deal_claims`` (AMENDMENT 9) are written the same way,
after the history's records, as ``deal_claim_statements`` reads them.

Each record states what is known about it (``guards/history_state.py``),
and an act read carries, beside ``act_payload``, the counterparty its check
sealed (``thread_counterparty``), which no decision seals. Its
``disposition`` is the one the act's capsule seals, and nothing else: an act
sealed with none is written as ``no_disposition``, never as accepted and
never as declined, and no decision is inferred from its ``authority_basis``.
An entry whose act cannot be read is written as ``unread``, with its
``item_ref`` when that is in its agreed shape (64 lowercase hex). When the
history is missing, is not a list, is not declared complete, or holds an
entry with no readable time or an ``item_ref`` in another shape, one
``incomplete`` record is written after the acts.

An act that seals no ``spend_minor`` has no amount in its record, as its
deal bridge takes none, so the record also carries the amount the act states
(``STATED_AMOUNT``), which a caps window counts in its place (#214 hold A).

Under a caps fold that counts executed acts (``spend.weekly/3.1.0``), each act
is also written as the spend window's record of it (``ExecutedActs``), right
after it: every history entry is an executed act, counted once. An entry
whose ``agent_input`` its capsule does not bind, or whose spend cannot be
read, is written with ``spend_unreadable``, so the window is not evaluated
rather than counted without it. Under any other fold nothing more is
written.

The capsules are read as given: their signatures are not verified here.
capsulectl hands the envelope to the user's own checker, and every record
this writes stays in that check's ledger.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import TypedDict

from agent_action_capsule import json_digest
from cll.ledger.api import LedgerAPI

from ..folds.definition import FoldDefinition
from ..folds.duration import parse_duration_seconds
from ..guards.action import Action
from ..guards.capsule import act_payload
from ..guards.checks.caps import counts_executed_acts, in_window_count
from ..guards.classes import ENGINE_TAXONOMY, same_classes
from ..guards.history_state import (
    DISPOSITION,
    INCOMPLETE,
    LIVE_HISTORY,
    NO_DISPOSITION,
    STATED_AMOUNT,
    STATEMENT,
    THREAD_COUNTERPARTY,
    UNREAD,
    WINDOW_HELD,
    ThreadCounterparty,
)
from ..guards.statements import StatementRecord, claim_of
from .replay import (
    MONEY_IN,
    ExecutedActs,
    action_for_history_entry,
    bound_agent_input,
    deal_claim_statements,
    names_two_deals,
    statement_for_history_entry,
)

__all__ = ["HistoryLedger", "history_act_payload", "history_ledger"]


_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class _Disposition(TypedDict):
    decision: str


class _Sealed(TypedDict, total=False):
    disposition: _Disposition


class _HistoryRecord(_Sealed):
    """A record ``history_ledger`` writes for one history entry, with a
    ``disposition`` only when its capsule seals one. Its ``asg_payload`` is
    what a decision on the act seals (``act_payload``) plus
    ``live_history``."""

    capsule_id: str
    operator: object
    developer: object
    action_type: object
    action_id: object
    timestamp: object
    asg_payload: dict[str, object]


@dataclass(frozen=True)
class HistoryLedger:
    """What ``history_ledger`` wrote: how many records in each state (acts,
    and statements, which are not acts), and whether the history was known to
    be complete."""

    complete: bool
    disposition: int
    no_disposition: int
    unread: int
    statement: int = 0


def history_ledger(envelope: dict, ledger: LedgerAPI, *, caps_fold: FoldDefinition | None = None) -> HistoryLedger:
    """Write the acts of ``envelope``'s ``history`` into ``ledger``, oldest
    first, and an ``incomplete`` record after them when the history is not
    known to be complete (module docstring). ``caps_fold`` is the engine's;
    when it counts executed acts, each act's spend record follows it."""
    history = envelope.get("history")
    scope = envelope.get("history_scope")
    complete = isinstance(history, list) and isinstance(scope, dict) and scope.get("complete") is True
    dated: list[tuple[datetime, int, dict]] = []
    for index, entry in enumerate(history if isinstance(history, list) else []):
        at = _time(entry.get("timestamp")) if isinstance(entry, dict) else None
        if at is None:
            complete = False
            continue
        if "item_ref" in entry and not _is_item_ref(entry["item_ref"]):
            # Which sale the act belongs to cannot be read.
            complete = False
        dated.append((at, index, entry))
    dated.sort(key=lambda item: (item[0], item[1]))
    checked = envelope.get("record")
    checked_at = _time(checked.get("timestamp")) if isinstance(checked, dict) else None
    counts = {DISPOSITION: 0, NO_DISPOSITION: 0, UNREAD: 0, STATEMENT: 0}
    executed = (
        ExecutedActs([entry for _, _, entry in dated], reversal=caps_fold.reversal, checked_at=checked_at)
        if counts_executed_acts(caps_fold) else None
    )
    written: set[str] = set()
    claims: set[str] = set()
    for at, _, entry in dated:
        stated = _statement_record(entry)
        if stated is not None and (checked_at is None or at > checked_at):
            # A claim sealed after the check, or with no check time to hold it to.
            continue
        record = stated or _act_record(entry)
        if record["capsule_id"] in written or (stated is not None and claim_of(stated) in claims):
            # The same act, or claim, given twice: written, and counted, once.
            continue
        written.add(record["capsule_id"])
        if stated is not None:
            claims.add(claim_of(stated))
        counts[str(record["asg_payload"][LIVE_HISTORY])] += 1
        ledger.append(dict(record), consequential=False)
        if executed is not None and stated is None:
            shown = bound_agent_input(entry)
            spend = executed.record(entry, shown, json_digest(shown) if shown is not None else record["capsule_id"],
                                    every_record_is_an_act=True)
            if spend is not None:
                ledger.append(dict(spend), consequential=False)
    if isinstance(checked, dict):
        for stated in deal_claim_statements(checked).statements:
            if claim_of(stated) in claims:
                # Given in the history too: one claim, one statement.
                continue
            claims.add(claim_of(stated))
            counts[STATEMENT] += 1
            ledger.append(dict(_marked(stated)), consequential=False)
    if not complete:
        ledger.append(_incomplete_record(envelope.get("record")), consequential=False)
    else:
        held = window_held_reason(scope, [entry for _, _, entry in dated], caps_fold, checked)
        if held is not None:
            ledger.append(_window_held_record(envelope.get("record"), held), consequential=False)
    return HistoryLedger(
        complete=complete,
        disposition=counts[DISPOSITION],
        no_disposition=counts[NO_DISPOSITION],
        unread=counts[UNREAD],
        statement=counts[STATEMENT],
    )


def _statement_record(entry: dict) -> StatementRecord | None:
    """The record the replay writes for a history entry that is a claim the
    user's agent made (``statement_for_history_entry``), marked
    ``statement``; ``None`` for any other entry."""
    stated = statement_for_history_entry(entry)
    return _marked(stated) if stated is not None else None


def _marked(stated: StatementRecord) -> StatementRecord:
    """A statement record with its ``live_history`` marker."""
    return StatementRecord(**{**stated, "asg_payload": {**stated["asg_payload"], LIVE_HISTORY: STATEMENT}})


def _time(value: object) -> datetime | None:
    """``value`` as an aware time, when it is an RFC 3339 string with a zone."""
    if not isinstance(value, str):
        return None
    try:
        at = datetime.fromisoformat(value)
    except ValueError:
        # Not a time: the caller reads the history as incomplete.
        return None
    return at if at.tzinfo is not None else None


def _sealed_decision(entry: dict) -> str | None:
    """The ``disposition.decision`` an entry's capsule seals, or ``None``."""
    disposition = entry.get("disposition")
    decision = disposition.get("decision") if isinstance(disposition, dict) else None
    return decision if isinstance(decision, str) and decision else None


def _act_record(entry: dict) -> _HistoryRecord:
    """The record ``history_ledger`` writes for one history entry: its
    capsule's own identity fields, the act as ``act_payload`` carries it, and
    the disposition its capsule seals, when it seals one."""
    action = action_for_history_entry(entry)
    decision = _sealed_decision(entry)
    if action is None:
        payload: dict[str, object] = {LIVE_HISTORY: UNREAD}
        item_ref = entry.get("item_ref")
        if _is_item_ref(item_ref):
            payload["item_ref"] = item_ref
    else:
        payload = history_act_payload(action, sealed_disposition=decision is not None)
        stated = _stated_amount(entry) if "amount_minor" not in payload else None
        if stated is not None:
            payload[STATED_AMOUNT] = stated
    capsule_id = entry.get("capsule_id")
    record = _HistoryRecord(
        capsule_id=capsule_id if isinstance(capsule_id, str) and capsule_id else json_digest(entry),
        operator=entry.get("operator", ""),
        developer=entry.get("developer", ""),
        action_type=entry.get("action_type", ""),
        action_id=entry.get("action_id"),
        timestamp=entry.get("timestamp"),
        asg_payload=payload,
    )
    if decision is not None:
        record["disposition"] = _Disposition(decision=decision)
    return record


def _stated_amount(entry: dict) -> int | None:
    """The ``amount_minor`` a history act's body states, an integer, or
    ``None``: read where the act seals no ``spend_minor``, so its history
    copy carries no amount, for a caps window to count in its place
    (``STATED_AMOUNT``)."""
    acted, unchecked = _act_member(bound_agent_input(entry))
    amount = acted.get("amount_minor") if acted is not None and not unchecked else None
    return amount if isinstance(amount, int) and not isinstance(amount, bool) else None


def history_act_payload(action: Action, *, sealed_disposition: bool) -> dict[str, object]:
    """The ``asg_payload`` ``history_ledger`` writes for a history act it
    read: what a decision on ``action`` seals about the act (``act_payload``),
    its state (``disposition`` when its capsule seals one), and, beside them,
    the counterparty its check sealed as ``thread_counterparty`` when the
    action carries one, whole (``Action.counterparty_malformed``). That member is this ledger's own state: it is never in
    ``act_payload``, so no decision seals it (``guards/history_state.py``)."""
    payload: dict[str, object] = {**act_payload(action),
                                  LIVE_HISTORY: DISPOSITION if sealed_disposition else NO_DISPOSITION}
    if action.counterparty_ids and action.counterparty_fp_alg and not action.counterparty_malformed:
        payload[THREAD_COUNTERPARTY] = ThreadCounterparty(fp_alg=action.counterparty_fp_alg,
                                                          ids=dict(action.counterparty_ids))
    return payload


def _incomplete_record(checked: object) -> dict:
    """The one record saying the history may be missing acts, under the
    checked record's operator, at its time."""
    checked = checked if isinstance(checked, dict) else {}
    body = {
        "operator": checked.get("operator", ""),
        "timestamp": checked.get("timestamp"),
        "asg_payload": {LIVE_HISTORY: INCOMPLETE},
    }
    return {**body, "capsule_id": json_digest(body)}


def window_held_reason(scope: dict, entries: list[dict], caps_fold: FoldDefinition | None,
                       checked: object) -> str | None:
    """Why a complete history cannot decide the caps window, or None: it
    reaches back fewer days (``history_scope.days``, 0 when absent) than the
    window, some act the window would count carries no recorded decision and
    a spend that cannot be read, or some act names a taxonomy version whose
    classes are not the engine's. A history that holds an act it reaches back
    over is no proof that the acts before its first are absent, so a short one
    is held; an undecided act's spend is not in a total that counts accepted
    acts only, and when it cannot be read the total is not known (Sherlock2,
    ops u504); an act classed under other names cannot be summed against this
    engine's limits. ``checked`` is the record being checked, whose key and
    time place the window. With no caps fold there is no window to hold. The
    Go guard holds the same window with the same reasons, in this order
    (go/guard/engine.go windowCoverage)."""
    if caps_fold is None or caps_fold.window is None or not caps_fold.window.duration:
        return None
    window = caps_fold.window.duration
    days = scope.get("days")
    days = days if isinstance(days, int) and not isinstance(days, bool) else 0
    if days * 86400 < parse_duration_seconds(window):
        return f"the history supplied reaches back {days} day(s); the {window} total needs {window}"
    undecided: list[dict] = []
    other = 0
    for entry in entries:
        if _statement_record(entry) is not None:
            continue
        shown = bound_agent_input(entry)
        if not counts_executed_acts(caps_fold) and _undecided_unreadable(entry, shown):
            undecided.append(entry)
        version = _act_taxonomy_version(shown)
        if version is not None and not same_classes(version):
            other += 1
    held = _in_window(caps_fold, undecided, checked)
    if held:
        return (
            f"{held} earlier act(s) carry no recorded decision; the {window} total as the engine computes it "
            "counts accepted acts only, so it cannot be computed"
        )
    if other:
        return (
            f"{other} earlier act(s) were classed under another taxonomy version than the engine's "
            f"({ENGINE_TAXONOMY.version}), so the {window} total cannot be computed"
        )
    return None


def _undecided_unreadable(entry: dict, shown: dict | None) -> bool:
    """Whether a history act is one the window cannot leave out: checked,
    with no recorded decision, not moving money in, and with a spend that
    cannot be read. As the Go guard reads it (``DecodeHistoryAct``,
    ``windowCoverage``): a decision is any ``disposition.decision`` the
    capsule seals, and an act naming two deals is decided (refused); an act
    taken without a check is counted as spend, and one that moved money in
    never adds to the window; the spend is the act's ``spend_minor``, an
    integer of at least 0. A capsule that does not bind its ``agent_input``
    states no spend that can be read. Under a fold that counts executed acts
    the window's own records decide this instead (``ExecutedActs``), so it
    is not read there."""
    disposition = entry.get("disposition")
    if isinstance(disposition, dict) and disposition.get("decision") is not None:
        return False
    acted, unchecked = _act_member(shown)
    if acted is None:
        return True
    if unchecked or names_two_deals(shown) or acted.get("direction") == MONEY_IN:
        return False
    spend = acted.get("spend_minor")
    return not (isinstance(spend, int) and not isinstance(spend, bool) and spend >= 0)


def _in_window(caps_fold: FoldDefinition, acts: list[dict], checked: object) -> int:
    """How many of ``acts`` the caps window would count, by key and time
    alone: under the checked record's value for the fold's key, in the window
    as of its time (``caps.in_window_count``). When the checked record's time
    cannot be read, every one: caps cannot place the window."""
    if not acts:
        return 0
    checked = checked if isinstance(checked, dict) else {}
    if _time(checked.get("timestamp")) is None:
        return len(acts)
    key_value = checked.get(caps_fold.key, "") if caps_fold.key is not None else None
    placed = [{"timestamp": act.get("timestamp"), "developer": act.get("developer"), "operator": act.get("operator")}
              for act in acts]
    return in_window_count(caps_fold, placed, key_value, checked["timestamp"])


def _act_member(shown: object) -> tuple[dict | None, bool]:
    """The member of a disclosed act that records the act, and whether it
    was taken without a check, as the Go guard reads it
    (``DecodeHistoryAct``): ``body.attempted`` of a typed
    ``action-outcome/v0`` or ``body.unchecked``, else the body itself;
    ``(None, False)`` when there is no body."""
    body = shown.get("body") if isinstance(shown, dict) else None
    if not isinstance(body, dict):
        return None, False
    if shown.get("type") == "action-outcome/v0" and isinstance(body.get("attempted"), dict):
        return body["attempted"], True
    if isinstance(body.get("unchecked"), dict):
        return body["unchecked"], True
    return body, False


def _act_taxonomy_version(shown: object) -> object:
    """The taxonomy version an act names, as the Go guard reads it
    (``DecodeHistoryAct``): an act taken without a check names it in the
    member that records the act (``_act_member``), and the body's own stands
    in when that member names none."""
    acted, unchecked = _act_member(shown)
    if acted is None:
        return None
    version = acted.get("taxonomy_version") if unchecked else None
    return version if version is not None else shown["body"].get("taxonomy_version")


def _window_held_record(checked: object, reason: str) -> dict:
    """The one record saying the caps window cannot be summed over this
    history, why, under the checked record's operator, at its time."""
    checked = checked if isinstance(checked, dict) else {}
    body = {
        "operator": checked.get("operator", ""),
        "timestamp": checked.get("timestamp"),
        "asg_payload": {LIVE_HISTORY: WINDOW_HELD, "held_reason": reason},
    }
    return {**body, "capsule_id": json_digest(body)}


def _is_item_ref(value: object) -> bool:
    """Whether ``value`` is an ``item_ref`` in its agreed shape."""
    return isinstance(value, str) and _HEX64.fullmatch(value) is not None
