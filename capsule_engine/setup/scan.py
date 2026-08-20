# SPDX-License-Identifier: Apache-2.0
"""Tiny shared helpers for reading this package's own emit-layer ``fyi``
capsules back out of a ledger -- the same linear-scan-plus-filter shape
``conversation/capsules.py``'s ``find_session_turns`` already uses. Kept in
one place so ``propose``/``enforce`` never disagree on how to read what
``observe`` wrote."""
from __future__ import annotations

from capsule_ledger.ledger.api import LedgerAPI, ScanQuery
from capsule_ledger.ledger.records import LedgerRecord

__all__ = ["scan_event", "detail", "parent"]


def scan_event(ledger: LedgerAPI, event: str) -> list[LedgerRecord]:
    return [r for r in ledger.scan(ScanQuery(action_type="fyi")) if (r.capsule.get("asg_payload") or {}).get("event") == event]


def detail(record: LedgerRecord) -> dict:
    return (record.capsule.get("asg_payload") or {}).get("detail") or {}


def parent(record: LedgerRecord) -> str | None:
    return (record.capsule.get("chain") or {}).get("parent_capsule_id")
