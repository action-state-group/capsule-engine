# SPDX-License-Identifier: Apache-2.0
"""The counterparty-change value-props (standard-outcome-pack design §5) as a
TWO-LAYER composition, not a sixth judgment mode of their own:

  layer 1 (signal)     one per-session outcome this pack already has reason
                        to produce -- structural (a count over emitted
                        fields, no model) or judged (an LLM already scored
                        it against a digest-pinned prompt, sealed BEFORE this
                        module ever runs).
  layer 2 (trajectory)  a design §10 type-2 ``counterparty_change`` fold
                        (``folds/taxonomy.py``, PR #92) over that signal's
                        sealed per-session values, min-N gated,
                        correlation-not-cause framed.

This module only ever touches layer 2. It reduces over already-sealed
per-session signal capsules (dicts with the signal's own numeric field) --
it has no scorer, no prompt, no model call anywhere in it, which is §10.1's
"folds CONSUME judgment capsules and NEVER call the judge harness" invariant
applied to this family specifically. This module deliberately does not
import a judge/scorer package at all: not "avoids calling it today", but
structurally cannot call it, by construction -- it only imports from
``folds/definition.py`` and ``folds/taxonomy.py``.

Each signal here ``feeds_outcome_id``s one of the pack's own ``C*``
fold_counterparty rows (standard-vendor pack.yaml family E) -- this module
does not re-declare those rows or their tier/statement, it only wires the
fold that would make one of them measured instead of WITH-INSTRUMENTATION.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .definition import FoldDefinition, parse_definition
from .taxonomy import CounterpartyTrajectory, counterparty_change

__all__ = [
    "SIGNAL_MODES",
    "CounterpartySignal",
    "CLARIFICATION_TURN_COUNT",
    "BASIC_QUESTION_COUNT",
    "compute_clarification_turn_count",
    "signal_record",
    "counterparty_trajectory_for_signal",
]

# The two modes a per-session signal feeding a counterparty-change fold can
# be declared in (design §3/§5) -- "value" isn't listed because every C-family
# signal in the design table is either a plain count (structural) or an
# LLM read of free text (judged), never sealed arithmetic over a declared
# bound.
SIGNAL_MODES = frozenset({"structural", "judged"})


@dataclass(frozen=True)
class CounterpartySignal:
    """Layer 1's declaration (design §5): the per-session signal a
    counterparty-trajectory fold reduces over. ``field`` is the key a sealed
    per-session signal capsule carries its numeric value under --
    ``standard-vendor/pack.yaml``'s ``C*.evidence_instrument.field`` names
    the corresponding row's TREND field (``f"{field}_trend"``): the pack
    outcome is about the trajectory this fold computes, one level up from
    the raw per-session value declared here."""

    signal_id: str
    feeds_outcome_id: str
    statement: str
    mode: str
    field: str

    def __post_init__(self) -> None:
        if self.mode not in SIGNAL_MODES:
            raise ValueError(f"signal {self.signal_id!r} mode must be one of {sorted(SIGNAL_MODES)}, got {self.mode!r}")


# C5 "Growing autonomy" (design §4 E): deterministic IF turn roles/turn-type
# are emitted -- a structural count, no model, matching pack.yaml's
# `clarification_turn_count_trend` evidence_instrument field.
CLARIFICATION_TURN_COUNT = CounterpartySignal(
    signal_id="clarification_turn_count",
    feeds_outcome_id="C5",
    statement=(
        "Per-session count of counterparty turns marked as a clarifying "
        "question -- structural, present/absent over an emitted turn-type "
        "field, no model (design §5)."
    ),
    mode="structural",
    field="clarification_turn_count",
)

# C1 "Capability growth" (design §4 E): an LLM judge reads the sealed
# free-text session prose and scores how many basic/introductory questions
# the counterparty asked -- that judgment is sealed BEFORE a fold ever sees
# it (design §10.1), matching pack.yaml's `basic_question_count_trend`
# evidence_instrument field. This module never runs that judge itself.
BASIC_QUESTION_COUNT = CounterpartySignal(
    signal_id="basic_question_count",
    feeds_outcome_id="C1",
    statement=(
        "Per-session LLM-judged count of basic/introductory questions the "
        "counterparty asked, scored against a digest-pinned prompt and "
        "sealed before any counterparty-trajectory fold runs over it "
        "(design §5; §10.1: the fold never re-judges)."
    ),
    mode="judged",
    field="basic_question_count",
)


def compute_clarification_turn_count(turns: list[dict], *, counterparty_role: str = "counterparty") -> int:
    """Layer 1 for ``CLARIFICATION_TURN_COUNT`` -- structural, no model:
    count ``turns`` where the counterparty's own turn is marked
    ``is_clarification`` (an emitted turn-type field, design §5's "IF turn
    roles are emitted"). A turn missing either field simply doesn't count --
    skip, never an error, the same discipline ``folds/engine.py`` applies to
    an absent declared field."""
    return sum(1 for t in turns if t.get("role") == counterparty_role and t.get("is_clarification") is True)


def signal_record(
    signal: CounterpartySignal,
    *,
    session: Any,
    counterparty: Any,
    value: int,
    session_path: str = "session",
    counterparty_path: str = "counterparty",
) -> dict:
    """Build one sealed per-session signal capsule for ``signal`` -- the
    shape ``counterparty_trajectory_for_signal`` (and, upstream, a real
    emitter) reduces over. For a ``mode="judged"`` signal, ``value`` is
    assumed already scored and sealed by the judge run this module never
    performs itself."""
    return {session_path: session, counterparty_path: counterparty, signal.field: value}


def _fold_definition_for_signal(signal: CounterpartySignal) -> FoldDefinition:
    """The design §10 type-2 fold definition for ``signal``: one declared
    read (the signal's own field), reduced with ``last`` -- well-defined
    because each session contributes exactly one sealed signal capsule, so
    "the last record in ledger order" and "that session's value" coincide."""
    return parse_definition(
        {
            "fold_id": f"standard_vendor.counterparty_signal.{signal.signal_id}/1.0.0",
            "reads": [{"path": signal.field, "erasure_class": "commitment-ok"}],
            "reduce": {"reducer": "last", "field": signal.field},
            "emit": signal.field,
        }
    )


def counterparty_trajectory_for_signal(
    signal: CounterpartySignal,
    records: list[dict],
    *,
    counterparty: Any,
    session_path: str = "session",
    min_n: int,
    as_of: str | None = None,
) -> CounterpartyTrajectory:
    """Layer 2 (design §5/§10 type 2): reduce ``signal``'s sealed per-session
    capsules -- already scoped to one counterparty, same contract as
    ``taxonomy.counterparty_change`` -- into a min-N gated, correlation-not-
    cause-framed trajectory. This function's only inputs are ``signal``
    (a field name + mode label) and already-sealed ``records``; it builds the
    fold definition itself and calls straight through to
    ``taxonomy.counterparty_change`` -- there is no branch here, or anywhere
    else in this module, that could reach a ``Scorer``/``JudgeHarness`` call,
    satisfying §10.1's "folds ... NEVER call the judge harness" for this
    family specifically, structurally rather than by convention."""
    definition = _fold_definition_for_signal(signal)
    return counterparty_change(
        definition,
        records,
        counterparty=counterparty,
        session_path=session_path,
        min_n=min_n,
        as_of=as_of,
    )
