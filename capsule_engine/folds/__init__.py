# SPDX-License-Identifier: Apache-2.0
"""Fold definitions, reducers, and replay evaluation over the ledger (spec §1-4)."""
from .catalog import Catalog
from .definition import FilterClause, FoldDefinition, ReadField, Reduce, Window, parse_definition
from .engine import EvaluationTrace, evaluate_all, evaluate_one
from .errors import FoldDefinitionError, FoldDeterminismError
from .loader import load_definition_file, load_definition_text
from .reducers import REDUCERS

__all__ = [
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
]
