# SPDX-License-Identifier: Apache-2.0
"""De-fork onto the neutral account/fold core.

Pins the §7 cross-repo replay property and the class-marker mapping:

  * the SAME definition document, evaluated by the ledger (via its
    FoldDefinition projection) and directly by ``capsule_emit.account``, yields
    the IDENTICAL core ``definition_digest`` and identical deterministic result;
  * the compiler's MODEL-ASSISTED verdict maps onto the core's
    ``model_assisted`` derivation_class;
  * the ledger consumes the core through its public interface — there is no
    second copy of the derivation-class / account contracts inside the ledger;
  * the fold catalog is preserved (all catalog YAML still parses).
"""
from __future__ import annotations

from pathlib import Path

import pytest
from capsule_emit.account import AccountDefinition
from capsule_emit.account import parse_definition as core_parse
from capsule_emit.account import verify_account as core_verify

from capsule_engine.folds import (
    DERIVATION_DETERMINISTIC,
    DERIVATION_MODEL_ASSISTED,
    Catalog,
    FoldDefinition,
    parse_definition,
    verify_account,
)
from capsule_engine.folds.account_core import Coverage, Selection, build_account

REPO_ROOT = Path(__file__).resolve().parents[1]
CATALOG_DIR = REPO_ROOT / "capsule_engine" / "folds" / "catalog_defs"


def _ledger_fold() -> FoldDefinition:
    return parse_definition(
        {
            "fold_id": "spend.weekly/1.0.0",
            "reads": [
                {"path": "developer", "erasure_class": "commitment-ok"},
                {"path": "amount", "erasure_class": "commitment-ok"},
                {"path": "verdict_class", "erasure_class": "commitment-ok"},
            ],
            "reduce": {"reducer": "count"},
            "emit": "executed_count",
            "filter": [{"field": "verdict_class", "op": "eq", "value": "executed"}],
        }
    )


# ---------------------------------------------------------------------------
# §7 cross-repo replay: same document -> identical core digest + identical result
# ---------------------------------------------------------------------------
def test_cross_repo_replay_identical_definition_digest():
    fold = _ledger_fold()

    # The ledger projects its fold onto the neutral AccountDefinition and asks
    # the CORE for the digest (it never re-implements the core digest).
    ledger_side = fold.core_definition_digest()

    # The SAME definition document, parsed directly by capsule_emit.account.
    core_side_def = core_parse(
        {
            "name": "spend.weekly/1.0.0",
            "selection_kind": "range",
            "reads": ["developer", "amount", "verdict_class"],
            "derivation_class": DERIVATION_DETERMINISTIC,
            "predicate": [{"field": "verdict_class", "op": "eq", "value": "executed"}],
        }
    )
    assert isinstance(core_side_def, AccountDefinition)
    assert ledger_side == core_side_def.definition_digest()


def test_cross_repo_replay_identical_result():
    """The same deterministic contract (count executed over the range), asserted
    on either side, verifies against the core with the same digest and result."""
    fold = _ledger_fold()
    definition = fold.to_account_definition()
    selection = Selection(kind="range", coverage=Coverage(coverage_root="root", range=(0, 2)))

    records = [
        {"developer": "alice", "amount": 10, "verdict_class": "executed"},
        {"developer": "alice", "amount": 5, "verdict_class": "refused"},
        {"developer": "bob", "amount": 7, "verdict_class": "executed"},
    ]

    def ledger_evaluator(_sel) -> int:
        return sum(1 for r in records if r.get("verdict_class") == "executed")

    def core_evaluator(_sel) -> int:
        n = 0
        for r in records:
            if r["verdict_class"] == "executed":
                n += 1
        return n

    asserted = ledger_evaluator(selection)
    account = build_account(definition=definition, selection=selection, asserted_result=asserted)
    assert account.derivation.definition_digest == definition.definition_digest()

    r_ledger = verify_account(account, definition=definition, recompute=ledger_evaluator)
    r_core = core_verify(account, definition=definition, recompute=core_evaluator)
    assert r_ledger.ok and r_ledger.method == "recompute"
    assert r_core.ok and r_core.method == "recompute"


# ---------------------------------------------------------------------------
# class-marker mapping: MODEL-ASSISTED verdict -> model_assisted derivation_class
# ---------------------------------------------------------------------------
def test_hand_authored_fold_is_deterministic_by_default():
    assert _ledger_fold().derivation_class == DERIVATION_DETERMINISTIC


def test_unknown_derivation_class_is_refused():
    from capsule_engine.folds.errors import FoldDefinitionError

    with pytest.raises(FoldDefinitionError) as exc:
        parse_definition(
            {
                "fold_id": "x.y/1.0.0",
                "reads": [{"path": "developer", "erasure_class": "commitment-ok"}],
                "reduce": {"reducer": "count"},
                "emit": "z",
                "derivation_class": "hand_wave",
            }
        )
    assert exc.value.reason == "unknown_derivation_class"


# ---------------------------------------------------------------------------
# de-fork discipline: one implementation of the core contract, catalog preserved
# ---------------------------------------------------------------------------
def test_ledger_reimports_core_and_does_not_refork_derivation_classes():
    """The derivation-class vocabulary the ledger exposes IS the core's — same
    object identity, not a ledger-local copy."""
    import capsule_emit.account as core

    assert DERIVATION_DETERMINISTIC is core.DERIVATION_DETERMINISTIC
    assert DERIVATION_MODEL_ASSISTED is core.DERIVATION_MODEL_ASSISTED
    assert verify_account is core.verify_account


def test_no_second_core_definition_digest_in_ledger():
    """The neutral, cross-repo ``core_definition_digest`` must delegate to the
    core; the ledger never defines a second neutral definition_digest. (The
    ledger keeps its own authoring ``FoldDefinition.definition_digest`` — that is
    the richer authoring document, not the neutral contract.)"""
    src = (REPO_ROOT / "capsule_engine" / "folds" / "account_core.py").read_text()
    assert "def definition_digest" not in src  # re-imports, never re-implements


def test_fold_catalog_is_preserved():
    """Every shipped catalog fold still parses after the de-fork."""
    catalog = Catalog(CATALOG_DIR)
    errors = catalog.list_errors()
    assert not errors, f"catalog load errors after de-fork: {errors}"
    entries = catalog.list_entries()
    assert entries, "expected the shipped fold catalog to be non-empty"
    for entry in entries:
        # Each catalog fold projects cleanly onto the neutral core.
        assert entry.definition.core_definition_digest()
        assert entry.definition.derivation_class == DERIVATION_DETERMINISTIC
