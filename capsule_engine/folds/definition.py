# SPDX-License-Identifier: Apache-2.0
"""Fold definitions (spec §2): parsing, validation, and the definition_digest.

A definition is a plain dict on the wire (YAML in, via ``loader.py``). This
module turns that dict into a validated, immutable ``FoldDefinition`` and
computes its ``definition_digest`` — SHA-256 over the JCS bytes of the
canonical form, reusing ``agent_action_capsule.canonical`` rather than
reimplementing JCS (the same canonicalization the capsule format itself uses,
per spec §2: "canonicalized (JCS) and identified by its digest").
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from agent_action_capsule.canonical import FloatInDigestError, UnsafeIntegerError, json_digest

from .duration import parse_duration_seconds
from .errors import (
    DUPLICATE_READ_PATH,
    FLOAT_IN_DEFINITION,
    INVALID_FOLD_ID,
    MALFORMED_DEFINITION,
    MISSING_REDUCE_FIELD,
    UNBOUNDED_FILTER_OP,
    UNDECLARED_FIELD_READ,
    UNKNOWN_ERASURE_CLASS,
    UNKNOWN_REDUCER,
    UNSAFE_INTEGER_IN_DEFINITION,
    WALL_CLOCK_REFERENCE,
    FoldDefinitionError,
)
from .paths import validate_path

# fold_id (spec §2): "human name + semver", namespace-dotted, e.g. "spend.weekly/1.0.0".
FOLD_ID_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*/\d+\.\d+\.\d+$")

ERASURE_CLASSES = frozenset({"commitment-ok", "preimage"})

# Reducers: a closed, registered set (spec §2). distinct_count is deliberately
# excluded (spec §7 open question 1: sketches break byte-exact determinism).
FIELD_REDUCERS = frozenset({"sum", "min", "max", "last"})
NO_FIELD_REDUCERS = frozenset({"count"})
KNOWN_REDUCERS = FIELD_REDUCERS | NO_FIELD_REDUCERS

# filter (spec §2): "Bounded operations only: equality, ranges, set membership,
# prefix. No regex, no user-supplied code."
BOUNDED_FILTER_OPS = frozenset({"eq", "ne", "in", "not_in", "prefix", "gt", "gte", "lt", "lte"})

# Pseudo-fields that would make a fold consult something other than the
# declared capsule range (spec §3 rule 1). `evaluated_at` is explicitly
# output-only per spec §4 ("informational, never an input").
WALL_CLOCK_TOKENS = frozenset({"now", "current_time", "wall_clock", "_now", "evaluated_at", "system_time"})


@dataclass(frozen=True)
class ReadField:
    path: str
    erasure_class: str
    default: Any = None
    has_default: bool = False


@dataclass(frozen=True)
class FilterClause:
    field: str
    op: str
    value: Any


@dataclass(frozen=True)
class Window:
    mode: str  # "rolling" | "explicit"
    duration: str | None = None  # rolling mode: e.g. "7d"
    start: str | None = None  # explicit mode: ISO-8601 timestamp
    end: str | None = None  # explicit mode: ISO-8601 timestamp


@dataclass(frozen=True)
class Reduce:
    reducer: str
    field: str | None = None


@dataclass(frozen=True)
class FoldDefinition:
    fold_id: str
    reads: tuple[ReadField, ...]
    reduce: Reduce
    emit: str
    filter: tuple[FilterClause, ...] = ()
    key: str | None = None
    window: Window | None = None

    def read_paths(self) -> frozenset[str]:
        return frozenset(r.path for r in self.reads)

    def canonical_dict(self) -> dict:
        """The JCS-canonicalizable form of this definition — drives definition_digest."""
        out: dict[str, Any] = {
            "fold_id": self.fold_id,
            "reads": [
                {
                    "path": r.path,
                    "erasure_class": r.erasure_class,
                    **({"default": r.default} if r.has_default else {}),
                }
                for r in self.reads
            ],
            "reduce": {
                "reducer": self.reduce.reducer,
                **({"field": self.reduce.field} if self.reduce.field else {}),
            },
            "emit": self.emit,
        }
        if self.filter:
            out["filter"] = [{"field": f.field, "op": f.op, "value": f.value} for f in self.filter]
        if self.key is not None:
            out["key"] = self.key
        if self.window is not None:
            w: dict[str, Any] = {"mode": self.window.mode}
            if self.window.duration is not None:
                w["duration"] = self.window.duration
            if self.window.start is not None:
                w["start"] = self.window.start
            if self.window.end is not None:
                w["end"] = self.window.end
            out["window"] = w
        return out

    def definition_digest(self) -> str:
        """SHA-256 over the JCS bytes of the canonical definition (spec §2)."""
        try:
            return json_digest(self.canonical_dict())
        except FloatInDigestError as exc:
            raise FoldDefinitionError(FLOAT_IN_DEFINITION, str(exc)) from exc
        except UnsafeIntegerError as exc:
            raise FoldDefinitionError(UNSAFE_INTEGER_IN_DEFINITION, str(exc)) from exc


def _reject_wall_clock(path: str) -> None:
    for part in path.split("."):
        if part in WALL_CLOCK_TOKENS:
            raise FoldDefinitionError(
                WALL_CLOCK_REFERENCE,
                f"reads path {path!r} references a wall-clock/output-only pseudo-field {part!r}; "
                "folds MUST NOT consult a wall clock or anything outside the declared capsule "
                "range (spec §3 rule 1)",
            )


def _reject_float(value: Any, context: str) -> None:
    if isinstance(value, float):
        raise FoldDefinitionError(FLOAT_IN_DEFINITION, f"{context} is a float ({value!r}); floats are forbidden (spec §3 rule 2)")
    if isinstance(value, list):
        for item in value:
            _reject_float(item, context)


def parse_definition(data: Any) -> FoldDefinition:
    """Validate a plain dict (as loaded from YAML) into a ``FoldDefinition``."""
    if not isinstance(data, dict):
        raise FoldDefinitionError(MALFORMED_DEFINITION, "definition must be a mapping")

    fold_id = data.get("fold_id")
    if not isinstance(fold_id, str) or not FOLD_ID_RE.match(fold_id):
        raise FoldDefinitionError(
            INVALID_FOLD_ID,
            f"fold_id {fold_id!r} must match '<namespace>[.<namespace>...]/<major>.<minor>.<patch>' "
            "(e.g. 'spend.weekly/1.0.0')",
        )

    raw_reads = data.get("reads")
    if not isinstance(raw_reads, list) or not raw_reads:
        raise FoldDefinitionError(MALFORMED_DEFINITION, "reads must be a non-empty list")

    reads: list[ReadField] = []
    seen_paths: set[str] = set()
    for entry in raw_reads:
        if not isinstance(entry, dict) or "path" not in entry:
            raise FoldDefinitionError(MALFORMED_DEFINITION, f"each reads entry needs a 'path': {entry!r}")
        path = entry["path"]
        try:
            validate_path(path)
        except ValueError as exc:
            raise FoldDefinitionError(MALFORMED_DEFINITION, str(exc)) from exc
        _reject_wall_clock(path)
        if path in seen_paths:
            raise FoldDefinitionError(DUPLICATE_READ_PATH, f"path {path!r} declared more than once in reads")
        seen_paths.add(path)
        erasure_class = entry.get("erasure_class")
        if erasure_class not in ERASURE_CLASSES:
            raise FoldDefinitionError(
                UNKNOWN_ERASURE_CLASS,
                f"reads[{path!r}].erasure_class must be one of {sorted(ERASURE_CLASSES)}, got {erasure_class!r}",
            )
        has_default = "default" in entry
        if has_default:
            _reject_float(entry["default"], f"reads[{path!r}].default")
        reads.append(
            ReadField(path=path, erasure_class=erasure_class, default=entry.get("default"), has_default=has_default)
        )

    declared = {r.path for r in reads}

    raw_filter = data.get("filter") or []
    if not isinstance(raw_filter, list):
        raise FoldDefinitionError(MALFORMED_DEFINITION, "filter must be a list")
    filters: list[FilterClause] = []
    for clause in raw_filter:
        if not isinstance(clause, dict):
            raise FoldDefinitionError(MALFORMED_DEFINITION, f"filter clause must be a mapping: {clause!r}")
        f_field, op, value = clause.get("field"), clause.get("op"), clause.get("value")
        if op not in BOUNDED_FILTER_OPS:
            raise FoldDefinitionError(
                UNBOUNDED_FILTER_OP,
                f"filter op {op!r} is not a bounded operation; allowed: {sorted(BOUNDED_FILTER_OPS)} "
                "(spec §2: no regex, no user-supplied code)",
            )
        if f_field not in declared:
            raise FoldDefinitionError(UNDECLARED_FIELD_READ, f"filter references undeclared field {f_field!r}")
        if op in ("in", "not_in") and not isinstance(value, list):
            raise FoldDefinitionError(UNBOUNDED_FILTER_OP, f"filter op {op!r} requires a list value")
        _reject_float(value, f"filter[{f_field!r}].value")
        filters.append(FilterClause(field=f_field, op=op, value=value))

    key = data.get("key")
    if key is not None:
        if not isinstance(key, str):
            raise FoldDefinitionError(MALFORMED_DEFINITION, "key must be a string field path")
        if key not in declared:
            raise FoldDefinitionError(UNDECLARED_FIELD_READ, f"key references undeclared field {key!r}")

    window: Window | None = None
    raw_window = data.get("window")
    if raw_window is not None:
        if not isinstance(raw_window, dict) or "mode" not in raw_window:
            raise FoldDefinitionError(MALFORMED_DEFINITION, "window must be a mapping with a 'mode'")
        mode = raw_window["mode"]
        if mode == "rolling":
            duration = raw_window.get("duration")
            if not isinstance(duration, str):
                raise FoldDefinitionError(MALFORMED_DEFINITION, "window.duration is required for mode 'rolling'")
            try:
                parse_duration_seconds(duration)
            except ValueError as exc:
                raise FoldDefinitionError(MALFORMED_DEFINITION, str(exc)) from exc
            window = Window(mode="rolling", duration=duration)
        elif mode == "explicit":
            start, end = raw_window.get("start"), raw_window.get("end")
            if not isinstance(start, str) or not isinstance(end, str):
                raise FoldDefinitionError(
                    MALFORMED_DEFINITION, "window.start/end (ISO-8601 timestamp strings) are required for mode 'explicit'"
                )
            window = Window(mode="explicit", start=start, end=end)
        else:
            raise FoldDefinitionError(MALFORMED_DEFINITION, f"window.mode {mode!r} must be 'rolling' or 'explicit'")
        if "timestamp" not in declared:
            raise FoldDefinitionError(
                UNDECLARED_FIELD_READ,
                "a window is evaluated against record timestamps (spec §2); declare 'timestamp' in reads",
            )

    raw_reduce = data.get("reduce")
    if not isinstance(raw_reduce, dict) or "reducer" not in raw_reduce:
        raise FoldDefinitionError(MALFORMED_DEFINITION, "reduce.reducer is required")
    reducer = raw_reduce["reducer"]
    if reducer not in KNOWN_REDUCERS:
        raise FoldDefinitionError(
            UNKNOWN_REDUCER,
            f"reducer {reducer!r} is not in the closed registered set {sorted(KNOWN_REDUCERS)} "
            "(e.g. distinct_count is deliberately excluded — spec §7 open question 1)",
        )
    reduce_field = raw_reduce.get("field")
    if reducer in FIELD_REDUCERS:
        if not isinstance(reduce_field, str):
            raise FoldDefinitionError(MISSING_REDUCE_FIELD, f"reducer {reducer!r} requires reduce.field")
        if reduce_field not in declared:
            raise FoldDefinitionError(UNDECLARED_FIELD_READ, f"reduce.field {reduce_field!r} is not declared in reads")
    elif reduce_field is not None:
        raise FoldDefinitionError(MISSING_REDUCE_FIELD, f"reducer {reducer!r} does not take a reduce.field")

    emit = data.get("emit")
    if not isinstance(emit, str) or not emit:
        raise FoldDefinitionError(MALFORMED_DEFINITION, "emit is required and must be a non-empty string")

    return FoldDefinition(
        fold_id=fold_id,
        reads=tuple(reads),
        filter=tuple(filters),
        key=key,
        window=window,
        reduce=Reduce(reducer=reducer, field=reduce_field),
        emit=emit,
    )
