# SPDX-License-Identifier: Apache-2.0
"""What a live check's history ledger says about each record it wrote.

A live check decides on a ledger built from its check input's history
(``report/live_history.py``), not on the engine's own decisions. Each record
that builder writes carries ``asg_payload.live_history``, one of:

- ``disposition``: an act whose capsule seals a disposition, read as any
  decision is read;
- ``no_disposition``: an act whose capsule seals none (capsulectl seals a
  disposition only on an act whose action maps to a registered effect type);
  what was decided on it is not known, which is not the same as "not
  accepted";
- ``unread``: a history entry whose act could not be read;
- ``statement``: a claim the user's agent made to the counterparty
  (``guards/statements.py``), which states no act;
- ``incomplete``: written once when the history is not known to be complete,
  so an act may be missing from the ledger.

A record without the member is not from a live history: a decision, or a
replay's own record. A check that needs to know whether an act was accepted
reads the state. Every history act was carried out, so an act in state
``disposition`` or ``no_disposition`` counts as an earlier act a repeat can
match (``counts_as_act``); an unread entry, a statement and the incomplete
marker never do.
"""
from __future__ import annotations

__all__ = [
    "DISPOSITION",
    "INCOMPLETE",
    "LIVE_HISTORY",
    "NO_DISPOSITION",
    "STATEMENT",
    "UNREAD",
    "counts_as_act",
    "history_state",
]

LIVE_HISTORY = "live_history"
DISPOSITION = "disposition"
NO_DISPOSITION = "no_disposition"
UNREAD = "unread"
STATEMENT = "statement"
INCOMPLETE = "incomplete"


def history_state(capsule: dict) -> str | None:
    """The ``live_history`` state a record carries, or ``None`` for a record
    that is not from a live history."""
    state = (capsule.get("asg_payload") or {}).get(LIVE_HISTORY)
    return state if isinstance(state, str) else None


def counts_as_act(capsule: dict) -> bool:
    """Whether a check may match ``capsule`` as an earlier act: any record
    not from a live history, and a history act that was read, whether or not
    its capsule seals a disposition."""
    return history_state(capsule) in (None, DISPOSITION, NO_DISPOSITION)
