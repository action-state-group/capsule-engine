# SPDX-License-Identifier: Apache-2.0
"""Replay evaluation: a FoldDefinition + a ledger-order capsule stream -> the
result envelope (spec §4).

Determinism (spec §3), enforced here, not just documented:

1. No wall clock / randomness / network / anything outside the declared
   range. The only wall-clock read in this module is the ``evaluated_at``
   fallback below, and it flows *only* into the informational envelope
   field — never into filtering, windowing, or reduction. A rolling window's
   anchor (``as_of``) MUST be supplied explicitly by the caller, derived from
   ledger data; the engine refuses to invent one from the system clock.
2. No floats — enforced in ``reducers.py`` at the point a value enters
   arithmetic.
3. Ledger-order iteration only — records are walked in the order given,
   never re-sorted.
4. Unknown/absent fields never raise: a record missing a *declared* read
   field with no default is skipped (counted), never an error. Fields
   present on a record but not declared in ``reads`` are simply never looked
   at.
5. Bounded per-record work — one pass, no recursion, no cross-record joins.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from agent_action_capsule.canonical import FloatInDigestError, UnsafeIntegerError, json_digest

from .definition import FoldDefinition
from .duration import parse_duration_seconds
from .errors import AS_OF_REQUIRED_NOT_WALL_CLOCK, FoldDeterminismError
from .paths import ABSENT, get_path
from .reducers import REDUCERS

_GLOBAL_KEY = object()  # sentinel: the single accumulator for a key-less fold

_FILTER_OPS = {
    "eq": lambda v, target: v == target,
    "ne": lambda v, target: v != target,
    "in": lambda v, target: v in target,
    "not_in": lambda v, target: v not in target,
    "prefix": lambda v, target: isinstance(v, str) and v.startswith(target),
    "gt": lambda v, target: v is not ABSENT and v > target,
    "gte": lambda v, target: v is not ABSENT and v >= target,
    "lt": lambda v, target: v is not ABSENT and v < target,
    "lte": lambda v, target: v is not ABSENT and v <= target,
}


def _parse_timestamp(ts: str) -> datetime:
    text = ts[:-1] + "+00:00" if ts.endswith("Z") else ts
    return datetime.fromisoformat(text)


def _record_identity(record: dict) -> str:
    """The digest a fold cites for one input record (design §10.1): a sealed
    ledger capsule's own ``capsule_id`` when present -- every real capsule
    carries one, already computed over its own canonical form, so this never
    re-digests real ledger data -- else a content digest over the record
    itself, so synthetic/fixture records (no capsule_id) used in unit tests
    still get a stable, deterministic identity.

    The fallback tries the JCS digest first, but a bare identity lookup must
    never enforce spec §3 rule 2 ("no floats") on a record's *entire*
    content -- that rule is about values entering arithmetic (``reducers.py``),
    not about whether an unrelated field can exist at all. A record with a
    float in a field this fold never reads must still get an identity."""
    capsule_id = record.get("capsule_id")
    if isinstance(capsule_id, str) and capsule_id:
        return capsule_id
    try:
        return json_digest(record)
    except (FloatInDigestError, UnsafeIntegerError):
        return hashlib.sha256(json.dumps(record, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _input_set_digest(capsule_ids: tuple[str, ...]) -> str:
    """SHA-256 (JCS) over the ordered list of a fold's input capsule ids --
    the third leg of fold identity (design §10.1):
    ``(definition_digest, range, input_set_digest)``. The same range over the
    same ledger contents always yields the same digest, which is what makes a
    re-run of the same fold over the same range provably idempotent rather
    than merely assumed so."""
    return json_digest({"input_capsule_ids": list(capsule_ids)})


def _passes_filter(definition: FoldDefinition, values: dict[str, Any]) -> bool:
    for clause in definition.filter:
        v = values.get(clause.field, ABSENT)
        if not _FILTER_OPS[clause.op](v, clause.value):
            return False
    return True


def _within_window(definition: FoldDefinition, values: dict[str, Any], anchor: datetime | None) -> bool:
    window = definition.window
    if window is None:
        return True
    ts_value = values.get("timestamp")
    if not isinstance(ts_value, str):
        return False
    ts = _parse_timestamp(ts_value)
    if window.mode == "rolling":
        duration_seconds = parse_duration_seconds(window.duration)
        return anchor - timedelta(seconds=duration_seconds) <= ts <= anchor
    # mode == "explicit": bounds come from the definition itself (static data).
    return _parse_timestamp(window.start) <= ts <= _parse_timestamp(window.end)


def _resolve_reads(definition: FoldDefinition, record: dict) -> dict[str, Any] | None:
    """Resolve every declared read field. Returns None if the record is
    incomplete (a declared field is absent with no default) — skip-with-count,
    never an error (spec §3 rule 4)."""
    values: dict[str, Any] = {}
    for rf in definition.reads:
        v = get_path(record, rf.path, ABSENT)
        if v is ABSENT:
            if not rf.has_default:
                return None
            v = rf.default
        values[rf.path] = v
    return values


@dataclass(frozen=True)
class EvaluationTrace:
    """The strict result envelope (spec §4) plus v0 replay diagnostics, plus
    the design §10.1 fold-identity fields: ``input_capsule_ids`` (every
    record in the declared range, ledger order), ``matched_capsule_ids`` (the
    subset that actually fed the reducer -- what a fold capsule cites as the
    ones it reduced), and ``input_set_digest`` (a digest over the former)."""

    fold_digest: str
    range_: tuple[int, int]
    tree_size: int
    checkpoint: dict
    result: Any
    evaluated_at: str
    staleness: dict
    skipped_count: int
    considered_count: int
    matched_count: int
    input_capsule_ids: tuple[str, ...]
    matched_capsule_ids: tuple[str, ...]
    input_set_digest: str

    def to_envelope(self) -> dict:
        return {
            "fold": self.fold_digest,
            "range": list(self.range_),
            "tree_size": self.tree_size,
            "checkpoint": self.checkpoint,
            "result": self.result,
            "evaluated_at": self.evaluated_at,
            "staleness": self.staleness,
        }

    def fold_identity(self) -> tuple[str, tuple[int, int], str]:
        """``(definition_digest, range, input_set_digest)`` (design §10.1):
        the triple a fold-capsule builder dedups/idempotency-checks on. The
        same fold run twice over the same range against the same ledger
        contents always yields the same triple -- the second run is a no-op
        replay, never a second capsule."""
        return (self.fold_digest, self.range_, self.input_set_digest)

    def citations(self) -> dict:
        """What a fold capsule cites (design §10.1's "points at its inputs,
        so '23 of 73' expands to the exact 73 sessions and 23 verdicts"):
        every input considered, which of those actually matched, and the
        digest over the input set. Deliberately separate from
        ``to_envelope()``, which is the spec §4 strict result shape and must
        not grow new keys."""
        return {
            "input_set_digest": self.input_set_digest,
            "input_capsule_ids": list(self.input_capsule_ids),
            "cited_capsule_ids": list(self.matched_capsule_ids),
        }


def _compute_groups(
    definition: FoldDefinition, records: list[dict], as_of: str | None
) -> tuple[dict[Any, Any], int, int, int, tuple[str, ...]]:
    anchor: datetime | None = None
    if definition.window is not None and definition.window.mode == "rolling":
        if as_of is None:
            raise FoldDeterminismError(
                AS_OF_REQUIRED_NOT_WALL_CLOCK,
                "rolling window folds require an explicit as_of reference derived from ledger "
                "data; the engine never consults a wall clock to supply one (spec §3 rule 1)",
            )
        anchor = _parse_timestamp(as_of)

    reducer = REDUCERS[definition.reduce.reducer]
    groups: dict[Any, Any] = {}
    skipped = 0
    considered = 0
    matched = 0
    matched_ids: list[str] = []

    for record in records:  # ledger order only (spec §3 rule 3) — never re-sorted
        considered += 1
        values = _resolve_reads(definition, record)
        if values is None:
            skipped += 1
            continue

        if not _within_window(definition, values, anchor):
            continue
        if not _passes_filter(definition, values):
            continue

        matched += 1
        matched_ids.append(_record_identity(record))
        group_key = _GLOBAL_KEY if definition.key is None else values[definition.key]
        acc = groups.get(group_key, reducer.initial())
        field_value = values.get(definition.reduce.field) if reducer.needs_field else None
        groups[group_key] = reducer.step(acc, field_value, definition.reduce.field or "")

    return groups, skipped, considered, matched, tuple(matched_ids)


def evaluate_all(
    definition: FoldDefinition,
    records: list[dict],
    *,
    as_of: str | None = None,
    range_start: int = 0,
    checkpoint: dict | None = None,
    evaluated_at: str | None = None,
    staleness_ms: int = 0,
) -> dict[Any, EvaluationTrace]:
    """Evaluate every group (key value) present in ``records``. Returns a dict
    keyed by the group's key value (or the engine's global-key sentinel when
    the definition declares no ``key``)."""
    groups, skipped, considered, matched, matched_capsule_ids = _compute_groups(definition, records, as_of)
    reducer = REDUCERS[definition.reduce.reducer]

    fold_digest = definition.definition_digest()
    range_end = range_start + len(records) - 1 if records else range_start - 1
    tree_size = range_start + len(records)
    env_checkpoint = checkpoint if checkpoint is not None else {"tree_size": tree_size}
    # The one sanctioned wall-clock read in this module: informational only,
    # never consulted by filter/window/reduce logic above.
    env_evaluated_at = evaluated_at if evaluated_at is not None else datetime.now(timezone.utc).isoformat()
    env_staleness = {"checkpoint_age_ms": staleness_ms}
    input_capsule_ids = tuple(_record_identity(r) for r in records)
    input_digest = _input_set_digest(input_capsule_ids)

    return {
        group_key: EvaluationTrace(
            fold_digest=fold_digest,
            range_=(range_start, range_end),
            tree_size=tree_size,
            checkpoint=env_checkpoint,
            result=reducer.finalize(acc),
            evaluated_at=env_evaluated_at,
            staleness=env_staleness,
            skipped_count=skipped,
            considered_count=considered,
            matched_count=matched,
            input_capsule_ids=input_capsule_ids,
            matched_capsule_ids=matched_capsule_ids,
            input_set_digest=input_digest,
        )
        for group_key, acc in groups.items()
    }


def evaluate_one(
    definition: FoldDefinition,
    records: list[dict],
    *,
    key_value: Any = None,
    as_of: str | None = None,
    range_start: int = 0,
    checkpoint: dict | None = None,
    evaluated_at: str | None = None,
    staleness_ms: int = 0,
) -> EvaluationTrace:
    """Evaluate a single group. If the group never matched (or the fold has
    no ``key``), returns a defined empty result (the reducer's initial value)
    rather than raising — no bystander errors for an unseen group."""
    traces = evaluate_all(
        definition,
        records,
        as_of=as_of,
        range_start=range_start,
        checkpoint=checkpoint,
        evaluated_at=evaluated_at,
        staleness_ms=staleness_ms,
    )
    lookup_key = _GLOBAL_KEY if definition.key is None else key_value
    if lookup_key in traces:
        return traces[lookup_key]

    reducer = REDUCERS[definition.reduce.reducer]
    range_end = range_start + len(records) - 1 if records else range_start - 1
    tree_size = range_start + len(records)
    input_capsule_ids = tuple(_record_identity(r) for r in records)
    return EvaluationTrace(
        fold_digest=definition.definition_digest(),
        range_=(range_start, range_end),
        tree_size=tree_size,
        checkpoint=checkpoint if checkpoint is not None else {"tree_size": tree_size},
        result=reducer.finalize(reducer.initial()),
        evaluated_at=evaluated_at if evaluated_at is not None else datetime.now(timezone.utc).isoformat(),
        staleness={"checkpoint_age_ms": staleness_ms},
        skipped_count=0,
        considered_count=len(records),
        matched_count=0,
        input_capsule_ids=input_capsule_ids,
        matched_capsule_ids=(),
        input_set_digest=_input_set_digest(input_capsule_ids),
    )
