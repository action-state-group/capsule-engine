# SPDX-License-Identifier: Apache-2.0
"""Named-reason errors for fold definitions and fold evaluation.

Every rejection carries a stable ``reason`` code (spec §6: "MUST-FAIL vectors
... with named failure reasons") so a vector can pin the reason string, not
just "it raised something".
"""
from __future__ import annotations

# Definition-time (parse/validate) reasons.
INVALID_FOLD_ID = "invalid_fold_id_namespace"
UNKNOWN_ERASURE_CLASS = "unknown_erasure_class"
UNKNOWN_REDUCER = "unknown_reducer"
UNBOUNDED_FILTER_OP = "unbounded_filter_operation"
UNDECLARED_FIELD_READ = "undeclared_field_read"
WALL_CLOCK_REFERENCE = "wall_clock_reference_forbidden"
FLOAT_IN_DEFINITION = "float_in_definition"
UNSAFE_INTEGER_IN_DEFINITION = "unsafe_integer_in_definition"
MISSING_REDUCE_FIELD = "missing_reduce_field"
DUPLICATE_READ_PATH = "duplicate_read_path"
MALFORMED_DEFINITION = "malformed_definition"

# Evaluation-time (replay) reasons.
FLOAT_IN_REDUCE_FIELD = "float_in_reduce_field"
NON_NUMERIC_REDUCE_FIELD = "non_numeric_reduce_field"
AS_OF_REQUIRED_NOT_WALL_CLOCK = "as_of_required_not_wall_clock"


class FoldDefinitionError(ValueError):
    """A fold definition fails to parse or validate. Carries a named reason."""

    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        super().__init__(f"{reason}: {message}")


class FoldDeterminismError(RuntimeError):
    """A fold evaluation hit a determinism rule (spec §3). Carries a named reason."""

    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        super().__init__(f"{reason}: {message}")
