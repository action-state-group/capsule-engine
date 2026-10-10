# SPDX-License-Identifier: Apache-2.0
"""The ledger a live check decides on, built from its check input's history.

capsulectl hands its rules checker one external-check-input/v0 envelope per
check: the ``record`` being checked and, as ``history``, the profile's
earlier acts from every deal, newest first, each one the sealed capsule of a
typed act record (``action-record/v0``) with its disclosed ``agent_input``
and, beside it, the deal's ``item_ref`` when the deal is a sale's thread.
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

Each record states what is known about it (``guards/history_state.py``). Its
``disposition`` is the one the act's capsule seals, and nothing else: an act
sealed with none is written as ``no_disposition``, never as accepted and
never as declined, and no decision is inferred from its ``authority_basis``.
An entry whose act cannot be read is written as ``unread``, with its
``item_ref`` when that is in its agreed shape (64 lowercase hex). When the
history is missing, is not a list, is not declared complete, or holds an
entry with no readable time or an ``item_ref`` in another shape, one
``incomplete`` record is written after the acts.

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
from capsule_ledger.ledger.api import LedgerAPI

from ..folds.definition import FoldDefinition
from ..guards.capsule import act_payload
from ..guards.checks.caps import counts_executed_acts
from ..guards.history_state import DISPOSITION, INCOMPLETE, LIVE_HISTORY, NO_DISPOSITION, STATEMENT, UNREAD
from ..guards.statements import StatementRecord, claim_of
from .replay import (
    ExecutedActs,
    action_for_history_entry,
    bound_agent_input,
    deal_claim_statements,
    statement_for_history_entry,
)

__all__ = ["HistoryLedger", "history_ledger"]

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
    executed = ExecutedActs([entry for _, _, entry in dated]) if counts_executed_acts(caps_fold) else None
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
        payload = {**act_payload(action), LIVE_HISTORY: DISPOSITION if decision is not None else NO_DISPOSITION}
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


def _is_item_ref(value: object) -> bool:
    """Whether ``value`` is an ``item_ref`` in its agreed shape."""
    return isinstance(value, str) and _HEX64.fullmatch(value) is not None
