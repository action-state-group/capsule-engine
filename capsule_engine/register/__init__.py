# SPDX-License-Identifier: Apache-2.0
"""Obligation register format v0 ([batch1-obligation-register-v0-sample-compiler]):
a loadable, GRC-shaped list of register rows, plus the OSS sample compiler that
turns one into an obligation-profile ``packs.schema.EvidenceContract``.

See the module docstrings in ``schema.py`` (``RegisterRow``/``ObligationRegister``,
the parsed register shape), ``loader.py`` (a register file/dict -> a validated
``ObligationRegister``), and ``compiler.py`` (``EvidenceCompiler``, the row ->
requirement path) for the pieces.
"""
from .compiler import EVIDENCE_CLASS_DEFAULTS, EvidenceCompiler
from .errors import RegisterCompilerError, RegisterDefinitionError
from .loader import load_register_dict, load_register_file
from .schema import EVIDENCE_CLASS_VALUES, ObligationRegister, RegisterRow

__all__ = [
    "RegisterRow",
    "ObligationRegister",
    "EVIDENCE_CLASS_VALUES",
    "load_register_dict",
    "load_register_file",
    "RegisterDefinitionError",
    "EvidenceCompiler",
    "EVIDENCE_CLASS_DEFAULTS",
    "RegisterCompilerError",
]
