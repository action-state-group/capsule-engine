# SPDX-License-Identifier: Apache-2.0
"""Named-reason errors for wicket definitions (mirrors ``folds/errors.py``)."""
from __future__ import annotations

INVALID_WICKET_ID = "invalid_wicket_id_namespace"
UNKNOWN_CHECK = "unknown_check"
MALFORMED_DEFINITION = "malformed_definition"
FLOAT_IN_DEFINITION = "float_in_definition"
UNSAFE_INTEGER_IN_DEFINITION = "unsafe_integer_in_definition"


class WicketDefinitionError(ValueError):
    """A wicket definition fails to parse or validate. Carries a stable reason code."""

    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        super().__init__(f"{reason}: {message}")
