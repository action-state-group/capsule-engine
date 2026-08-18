# SPDX-License-Identifier: Apache-2.0
"""Replay a JSONL capsule ledger through T3's ``GuardEngine`` in dry-run mode.

Every decision here comes from actually running ``GuardEngine.check(...,
dry_run=True)`` over the real fixture/ledger records -- this module never
fabricates an outcome. The guard's own replay-local view (an ephemeral
``LedgerStore``) accumulates the decision capsules this replay itself
produces, so a dedupe hit fires the same way it would in ``test_guard_dry_run.py``:
against a *prior decision in this same replay*, not against the source
ledger's original capsules (which the guard never produced and has no
decision-history relationship to).

``_bridge_transfer_funds`` is the one non-default action mapping, and it
mirrors the pattern already established by ``tests/test_guard_dry_run.py``
and ``tests/test_guard_eur150k_bridge.py``: ``transfer_funds`` capsules in
these fixtures carry their amount only as free text inside
``model_attestation.compute_attestation.note`` (a field this schema version
has no structured place for), so the bridge regexes the real embedded
values out of that note rather than hardcoding them.
"""
from __future__ import annotations

import json
import re
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from ..folds.definition import FoldDefinition
from ..folds.duration import parse_duration_seconds
from ..guards import Action, GuardDecision, GuardEngine, LocalSigner
from ..ledger import LedgerStore

__all__ = ["SourcedDecision", "ReplayResult", "load_records", "filter_since", "action_for_record", "replay"]

_TRANSFER_NOTE_RE = re.compile(r"amount_eur:\s*(\d+).*?target_iban:\s*([A-Z0-9]+)")


def load_records(paths: Sequence[str | Path]) -> list[dict]:
    """Load every record from one or more sources, in the given order.

    Each source is either a plain JSONL ledger file (read in file-internal
    line order) or a ``LedgerStore`` directory (read via ``scan()``, its own
    append order) -- the CLI's real-deployment path, since an operator's
    actual local ledger is a store directory, not a loose JSONL file.
    """
    records: list[dict] = []
    for path in paths:
        p = Path(path)
        if p.is_dir():
            store = LedgerStore(p)
            try:
                for rec in store.scan():
                    records.append(rec.capsule)
            finally:
                store.close()
        else:
            with p.open(encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        records.append(json.loads(line))
    return records


def _parse_ts(ts: str) -> datetime:
    text = ts[:-1] + "+00:00" if ts.endswith("Z") else ts
    return datetime.fromisoformat(text)


def filter_since(records: list[dict], since: str | None) -> list[dict]:
    """Keep only records within ``since`` (e.g. ``"7d"``) of the *latest*
    timestamp in the set -- anchored to real ledger data, never the system
    wall clock (same determinism principle as the fold engine's own rolling
    windows: ``folds/engine.py`` refuses to invent an anchor from
    ``datetime.now()``)."""
    if since is None:
        return list(records)
    seconds = parse_duration_seconds(since)
    timestamps = [r["timestamp"] for r in records if r.get("timestamp")]
    if not timestamps:
        return list(records)
    anchor = max(_parse_ts(t) for t in timestamps)
    cutoff = anchor - timedelta(seconds=seconds)
    return [r for r in records if r.get("timestamp") and _parse_ts(r["timestamp"]) >= cutoff]


def _bridge_transfer_funds(record: dict) -> Action | None:
    action_id = record.get("action_id") or ""
    verb = action_id.split("/", 1)[0] if action_id else ""
    if verb != "transfer_funds":
        return None
    note = ((record.get("model_attestation") or {}).get("compute_attestation") or {}).get("note", "")
    match = _TRANSFER_NOTE_RE.search(note)
    if not match:
        return None
    amount_eur, target_iban = match.groups()
    return Action(
        verb=verb,
        operator=record.get("operator", ""),
        developer=record.get("developer", ""),
        action_class="money.transfer",
        amount_minor=int(amount_eur) * 100,
        currency="EUR",
        target=target_iban,
        timestamp=record.get("timestamp"),
        action_id=action_id or None,
    )


def action_for_record(record: dict) -> Action:
    bridged = _bridge_transfer_funds(record)
    if bridged is not None:
        return bridged
    return Action.from_capsule(record)


@dataclass(frozen=True)
class SourcedDecision:
    """One replayed record, its resulting ``Action``, its real
    ``GuardDecision``, and (when a check matched a prior record) that prior
    record's full capsule -- fetched from the replay's own ephemeral store
    while it is still open, so the report can carry it for re-verification."""

    record: dict
    action: Action
    decision: GuardDecision
    cited_capsule: dict | None


@dataclass(frozen=True)
class ReplayResult:
    decisions: tuple[SourcedDecision, ...]
    record_range: tuple[int, int]


def replay(
    records: list[dict],
    *,
    caps_fold: FoldDefinition,
    caps_minor: dict[str, int] | None = None,
    manifest_digest: str | None = None,
) -> ReplayResult:
    """Feed every record through a fresh ``GuardEngine`` in dry-run mode, in
    order. Never blocks (``dry_run=True``) -- see ``engine.py``'s own
    guarantee that a dry-run decision still produces and appends a real,
    signed capsule. ``record_range`` on the result is this replayed set's own
    1-based position range (i.e. positions within the ``--since``-filtered
    window actually replayed, not the source ledger's absolute positions)."""
    if not records:
        return ReplayResult(decisions=(), record_range=(0, -1))

    signer = LocalSigner(key_id="dry-run-report", secret=b"asg-guard-dry-run-report")

    sourced: list[SourcedDecision] = []
    with tempfile.TemporaryDirectory() as tmp, LedgerStore(tmp) as store:
        engine = GuardEngine(
            ledger=store,
            caps_fold=caps_fold,
            signer_provider=lambda: signer,
            caps_minor=caps_minor or {},
            manifest_digest=manifest_digest,
        )
        for record in records:
            action = action_for_record(record)
            decision = engine.check(action, dry_run=True)
            cited_capsule = None
            for constraint in decision.constraints:
                cited_id = (constraint.evidence or {}).get("matched_capsule_id") or (
                    constraint.evidence or {}
                ).get("cited_capsule_id")
                if cited_id:
                    found = store.fetch(cited_id)
                    if found is not None:
                        cited_capsule = found.capsule
                        break
            sourced.append(SourcedDecision(record=record, action=action, decision=decision, cited_capsule=cited_capsule))

    return ReplayResult(decisions=tuple(sourced), record_range=(1, len(records)))
