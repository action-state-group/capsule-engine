# SPDX-License-Identifier: Apache-2.0
"""Fold definitions, reducers, and replay evaluation over the ledger (spec §1-4)."""

from .account_core import (
    DEFAULT_DERIVATION_CLASS,
    DERIVATION_CLASSES,
    DERIVATION_DETERMINISTIC,
    DERIVATION_MODEL_ASSISTED,
    SELECTION_CHAIN_SEGMENT,
    SELECTION_EXPLICIT_SET,
    SELECTION_RANGE,
    AccountDefinition,
    build_account,
    verify_account,
)
from .catalog import Catalog
from .counterparty_signals import (
    BASIC_QUESTION_COUNT,
    CLARIFICATION_TURN_COUNT,
    CounterpartySignal,
    compute_clarification_turn_count,
    counterparty_trajectory_for_signal,
    signal_record,
)
from .definition import FilterClause, FoldDefinition, ReadField, Reduce, Window, parse_definition
from .engine import EvaluationTrace, evaluate_all, evaluate_one
from .errors import FoldDefinitionError, FoldDeterminismError
from .loader import load_definition_file, load_definition_text
from .reducers import REDUCERS
from .taxonomy import (
    AgentTrajectory,
    CohortComparison,
    CounterpartyTrajectory,
    SessionPoint,
    TrendPoint,
    agent_trajectory,
    cohort_comparison,
    counterparty_change,
)

__all__ = [
    # de-fork: the neutral account/fold core, re-imported through the folds
    # public interface (Amendment E) — not re-implemented in the ledger.
    "AccountDefinition",
    "build_account",
    "verify_account",
    "DERIVATION_DETERMINISTIC",
    "DERIVATION_MODEL_ASSISTED",
    "DERIVATION_CLASSES",
    "DEFAULT_DERIVATION_CLASS",
    "SELECTION_RANGE",
    "SELECTION_EXPLICIT_SET",
    "SELECTION_CHAIN_SEGMENT",
    "Catalog",
    "FoldDefinition",
    "ReadField",
    "FilterClause",
    "Window",
    "Reduce",
    "parse_definition",
    "load_definition_text",
    "load_definition_file",
    "EvaluationTrace",
    "evaluate_all",
    "evaluate_one",
    "REDUCERS",
    "FoldDefinitionError",
    "FoldDeterminismError",
    "AgentTrajectory",
    "CohortComparison",
    "CounterpartyTrajectory",
    "SessionPoint",
    "TrendPoint",
    "agent_trajectory",
    "cohort_comparison",
    "counterparty_change",
    "CounterpartySignal",
    "CLARIFICATION_TURN_COUNT",
    "BASIC_QUESTION_COUNT",
    "compute_clarification_turn_count",
    "signal_record",
    "counterparty_trajectory_for_signal",
]
