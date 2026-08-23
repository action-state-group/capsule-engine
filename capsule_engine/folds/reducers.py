# SPDX-License-Identifier: Apache-2.0
"""The closed, registered reducer set (spec §2): count, sum, min, max, last.

Deliberately excludes ``distinct_count`` — spec §7 open question 1: it needs
the equivalence index or a sketch, and sketches break byte-exact determinism,
so it stays out until an exact implementation exists.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .errors import FLOAT_IN_REDUCE_FIELD, NON_NUMERIC_REDUCE_FIELD, FoldDeterminismError


def _check_integer(value: Any, field: str) -> int:
    """Amounts are integer minor units (spec §2, §3 rule 2) — floats MUST-FAIL."""
    if isinstance(value, bool):
        raise FoldDeterminismError(
            NON_NUMERIC_REDUCE_FIELD, f"field {field!r} is a bool, not an integer amount"
        )
    if isinstance(value, float):
        raise FoldDeterminismError(
            FLOAT_IN_REDUCE_FIELD,
            f"field {field!r} carries a float ({value!r}); amounts MUST be integer minor units "
            "(spec §3 rule 2 — no floating-point arithmetic)",
        )
    if not isinstance(value, int):
        raise FoldDeterminismError(
            NON_NUMERIC_REDUCE_FIELD, f"field {field!r} is {type(value).__name__}, not an integer"
        )
    return value


def _count_step(acc: int, value: Any, field: str) -> int:
    return acc + 1


def _sum_step(acc: int, value: Any, field: str) -> int:
    return acc + _check_integer(value, field)


def _min_step(acc: int | None, value: Any, field: str) -> int:
    v = _check_integer(value, field)
    return v if acc is None else min(acc, v)


def _max_step(acc: int | None, value: Any, field: str) -> int:
    v = _check_integer(value, field)
    return v if acc is None else max(acc, v)


def _last_step(acc: Any, value: Any, field: str) -> Any:
    # Well-defined because the engine iterates strictly in ledger order
    # (spec §3 rule 3) — "last" means the last record in that order, not the
    # highest-valued one, so no type check beyond what the reads path already did.
    return value


@dataclass(frozen=True)
class Reducer:
    initial: Callable[[], Any]
    step: Callable[[Any, Any, str], Any]
    finalize: Callable[[Any], Any] = staticmethod(lambda acc: acc)
    needs_field: bool = True


REDUCERS: dict[str, Reducer] = {
    "count": Reducer(initial=lambda: 0, step=_count_step, needs_field=False),
    "sum": Reducer(initial=lambda: 0, step=_sum_step),
    "min": Reducer(initial=lambda: None, step=_min_step),
    "max": Reducer(initial=lambda: None, step=_max_step),
    "last": Reducer(initial=lambda: None, step=_last_step),
}
