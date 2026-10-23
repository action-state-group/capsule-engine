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
  so an act may be missing from the ledger;
- ``window_held``: written once when the history is complete but the caps
  window cannot be summed over it (``report/live_history.py``): it does not
  reach back over the window, or holds acts classed under a taxonomy whose
  classes are not the engine's. It carries ``held_reason``. Only ``caps``
  reads it (it asks rather than allows); every act is still in the ledger.

An act read from the history (``disposition`` or ``no_disposition``) also
carries ``thread_counterparty``, ``{fp_alg, ids}``, when its check sealed a
counterparty block: the buyer's keyed fingerprints as the check sealed them
(capsulectl gives the block beside the act). It is internal state of this
ledger, beside what a decision seals (``act_payload``) and never in it, so no
decision seals it and no export carries it; ``release_on_acceptance``
reads it to tell the buyer who accepted.

A record without the member is not from a live history: a decision, or a
replay's own record. A check that needs to know whether an act was accepted
reads the state. Every history act was carried out, so an act in state
``disposition`` or ``no_disposition`` counts as an earlier act a repeat can
match (``counts_as_act``); an unread entry, a statement and the incomplete
and window-held markers never do.
"""
from __future__ import annotations

import re
from typing import TypedDict

__all__ = [
    "DISPOSITION",
    "INCOMPLETE",
    "LIVE_HISTORY",
    "NO_DISPOSITION",
    "RULES_HISTORY_DAYS",
    "STATED_AMOUNT",
    "STATEMENT",
    "CHECK_FP_ALGS",
    "COUNTERPARTY_KINDS",
    "THREAD_COUNTERPARTY",
    "UNREAD",
    "WINDOW_HELD",
    "ThreadCounterparty",
    "counts_as_act",
    "history_state",
    "thread_counterparty",
    "well_formed_counterparty",
]

LIVE_HISTORY = "live_history"
DISPOSITION = "disposition"
NO_DISPOSITION = "no_disposition"
UNREAD = "unread"
STATEMENT = "statement"
INCOMPLETE = "incomplete"
WINDOW_HELD = "window_held"
THREAD_COUNTERPARTY = "thread_counterparty"
# The amount a history act states (its body's amount_minor) when it seals no
# spend_minor: the history copy's own amount_minor is the spend its deal
# bridge takes, so it has none, and a caps window that reads the copy counts
# this in its place (caps.py, capsule-engine#214 hold A, ruling A: an
# accepted act with no spend_minor counts at its stated amount under every
# fold). Like THREAD_COUNTERPARTY it is this ledger's own state, never in
# act_payload, so no decision seals it and dedupe never reads it.
STATED_AMOUNT = "stated_amount_minor"
# How far back a live check's history reaches: capsulectl sends the acts
# sealed in the last 31 days (capsule-cli internal/cli RulesHistoryDays,
# plugins/internal/plugin/rules_checker.go rulesHistory). A replay of a sale
# gives ``release_on_acceptance`` the same window (``report/replay.py``).
RULES_HISTORY_DAYS = 31


# The kinds a check's counterparty block fingerprints (capsule-cli
# deal_rules.go whoFields), the algorithms a check seals it under (an
# x-deal-v0 check's, a typed check's), and a fingerprint's shape.
COUNTERPARTY_KINDS = frozenset({"payee", "name", "domain", "phone", "email", "relay_address", "profile_id"})
CHECK_FP_ALGS = frozenset({"hmac-sha256-deal-key", "hmac-sha256-chain-key"})
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class ThreadCounterparty(TypedDict):
    """The counterparty block a history act's check sealed: its keyed
    fingerprints by kind, and the algorithm that made them."""

    fp_alg: str
    ids: dict[str, str]


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


def well_formed_counterparty(fp_alg: object, ids: object) -> bool:
    """Whether a counterparty block is a check's, whole: ``fp_alg`` one a
    check seals, and ``ids`` a non-empty mapping whose every member is a
    known kind with a 64-hex fingerprint. A block with any other member is
    not read as a buyer at all, so a member dropped for its shape never
    reads as absent."""
    if not isinstance(fp_alg, str) or fp_alg not in CHECK_FP_ALGS or not isinstance(ids, dict) or not ids:
        return False
    return all(k in COUNTERPARTY_KINDS and isinstance(v, str) and _HEX64.fullmatch(v) is not None
               for k, v in ids.items())


def thread_counterparty(capsule: dict) -> ThreadCounterparty | None:
    """The ``thread_counterparty`` a history act carries, or ``None`` when
    it carries none, or one that is not whole (``well_formed_counterparty``)."""
    block = (capsule.get("asg_payload") or {}).get(THREAD_COUNTERPARTY)
    if not isinstance(block, dict) or not well_formed_counterparty(block.get("fp_alg"), block.get("ids")):
        return None
    return ThreadCounterparty(fp_alg=block["fp_alg"], ids=dict(block["ids"]))
