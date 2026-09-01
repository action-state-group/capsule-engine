# SPDX-License-Identifier: Apache-2.0
"""De-fork seam onto the neutral account/fold core (Amendment E, 2026-08-31).

The definition-as-DATA + ``definition_digest`` + replay/verify contracts, and
the ``deterministic`` / ``model_assisted`` split, are OWNED by
``capsule_emit.account`` (merged to capsule-emit ``main`` in #132). capsule-ledger
consumes that neutral core through its public interface rather than forking the
contracts — exactly as ``mmr/checkpoint.py`` and ``conversation/merkle.py``
consume ``capsule_emit.checkpoint``.

What re-imports here (the core contracts — NOT re-implemented in the ledger):

  * the derivation-class vocabulary (``deterministic`` / ``model_assisted``) —
    the ledger's compiler MODEL-ASSISTED / DETERMINISTIC verdict marker maps
    onto this ``derivation_class``;
  * the account object types + ``build_account`` + ``verify_account`` — for
    cross-repo replay against a core ``AccountDefinition``;
  * the selection-kind vocabulary, including the additive ``chain_segment``.

What STAYS ledger-side (authoring model, not the neutral contract): the richer
``FoldDefinition`` (reads-with-erasure-class, window, reduce, YAML loader), the
replay ``engine``, the ``reducers``, and the ``taxonomy``. Those are how the
ledger AUTHORS and EVALUATES folds; they are not the neutral definition/digest
contract and do not migrate.

The bridge is ``FoldDefinition.to_account_definition()`` (see ``definition.py``):
it projects a ledger fold onto a core ``AccountDefinition`` so that the SAME
definition document, evaluated by the ledger or via the core, yields the
identical core ``definition_digest`` and the identical result — the §7
cross-repo replay property. The ledger keeps its own richer
``FoldDefinition.definition_digest()`` for its catalog/fixtures; the CORE digest
(the cross-repo-replayable one) flows through this module, so there is exactly
one implementation of the neutral ``definition_digest`` in the stack.
"""
from __future__ import annotations

from capsule_emit.account import (
    DERIVATION_CLASSES,
    DERIVATION_DETERMINISTIC,
    DERIVATION_MODEL_ASSISTED,
    SELECTION_CHAIN_SEGMENT,
    SELECTION_EXPLICIT_SET,
    SELECTION_KINDS,
    SELECTION_RANGE,
    Account,
    AccountConstructionError,
    AccountDefinition,
    AccountVerificationError,
    Coverage,
    Derivation,
    Provenance,
    Selection,
    VerificationResult,
    build_account,
    verify_account,
)
from capsule_emit.account import (
    parse_definition as parse_account_definition,
)

# The default derivation class for a hand-authored / precondition-decomposable
# fold: its asserted result is a pure function of the selected records
# (recompute+match). The compiler's model-judgment branch overrides this to
# DERIVATION_MODEL_ASSISTED — that is the class-marker mapping the de-fork asks
# for (see compiler/compile.py::_model_assisted_fold).
DEFAULT_DERIVATION_CLASS = DERIVATION_DETERMINISTIC

__all__ = [
    "DERIVATION_CLASSES",
    "DERIVATION_DETERMINISTIC",
    "DERIVATION_MODEL_ASSISTED",
    "DEFAULT_DERIVATION_CLASS",
    "SELECTION_RANGE",
    "SELECTION_EXPLICIT_SET",
    "SELECTION_CHAIN_SEGMENT",
    "SELECTION_KINDS",
    "Account",
    "AccountDefinition",
    "AccountConstructionError",
    "AccountVerificationError",
    "Coverage",
    "Derivation",
    "Provenance",
    "Selection",
    "VerificationResult",
    "build_account",
    "verify_account",
    "parse_account_definition",
]
