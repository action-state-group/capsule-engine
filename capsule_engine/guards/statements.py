# SPDX-License-Identifier: Apache-2.0
"""The record of a statement made to the counterparty in a deal.

A seller's agent tells a buyer something about the deal (the item's
condition, a delivery date) in a capsulectl ``claim`` record that seals
``body.source_kind`` ``agent`` and a ``body.class``. It states no act, so no
decision carries it. The replay and a live check each write one record for
such a claim to the ledger the engine reads (``report/replay.py``,
``statement_record``), in ledger order, so a check that needs a statement
made first (``required_disclosure/1.1.0``) finds it in the deal.

The record carries no disposition, no target and no action class, so no
fold that reads decisions, payees or spend counts it.
"""
from __future__ import annotations

from typing import TypedDict

from agent_action_capsule import json_digest

__all__ = ["CLASS_ALIASES", "STATED", "StatementRecord", "make_statement_record"]

# The ``asg_payload`` member that holds a statement.
STATED = "stated"

# A sealed claim class read as another: capsulectl sealed ``delivery_promise``
# from rc13 until it adopted the seller pack's representation classes, and
# writes ``delivery_date`` for new claims since. The only alias.
CLASS_ALIASES = {"delivery_promise": "delivery_date"}


class _StatementBody(TypedDict):
    operator: str
    timestamp: str | None
    asg_payload: dict[str, object]


class StatementRecord(_StatementBody):
    capsule_id: str


def make_statement_record(
    *, operator: str, timestamp: str | None, sealed_class: str, source_kind: str, deal_id: str, claim: str
) -> StatementRecord:
    """The record of one statement; its ``capsule_id`` is the digest of the
    rest, so the replay and a live check write the same record for one
    claim."""
    stated = {
        "class": CLASS_ALIASES.get(sealed_class, sealed_class),
        "source_kind": source_kind,
        "deal_id": deal_id,
        "claim": claim,
    }
    body = _StatementBody(operator=operator, timestamp=timestamp, asg_payload={STATED: stated})
    return StatementRecord(**body, capsule_id=json_digest(body))
