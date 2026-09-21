# SPDX-License-Identifier: Apache-2.0
"""Named-reason errors for the obligation register
([batch1-obligation-register-v0-sample-compiler]) -- mirrors ``packs/errors.py``'s
convention: a reason code for tests/tooling, a message that names the field, says
what was expected, and shows a correct example.

Two distinct exception families, same split ``packs/errors.py`` already draws
between ``PackDefinitionError`` (malformed pack.yaml) and ``CorpusVerificationError``
(a declared claim that doesn't hold): ``RegisterDefinitionError`` is a load-time
failure (``loader.py`` -- the register file itself is malformed);
``RegisterCompilerError`` is a compile-time failure (``compiler.py`` -- a
structurally valid row that ``compile_requirement`` still can't turn into a
requirement).
"""
from __future__ import annotations

MALFORMED_REGISTER = "malformed_register"
MISSING_REQUIRED_FIELD = "missing_required_field"
DUPLICATE_ROW_ID = "duplicate_row_id"
INVALID_EVIDENCE_CLASS = "invalid_evidence_class"
INVALID_CLAUSE = "invalid_clause"
INVALID_EFFECTIVE_DATE = "invalid_effective_date"
INVALID_EVIDENCE_INSTRUMENT = "invalid_evidence_instrument"
FLOAT_IN_REGISTER_DIGEST = "float_in_register_digest"
UNSAFE_INTEGER_IN_REGISTER_DIGEST = "unsafe_integer_in_register_digest"


class RegisterDefinitionError(ValueError):
    """A register document fails to parse/validate -- see ``loader.py``."""

    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        super().__init__(f"{reason}: {message}")


class RegisterCompilerError(ValueError):
    """A register row cannot be compiled into a requirement -- see ``compiler.py``."""

    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        super().__init__(f"{reason}: {message}")
