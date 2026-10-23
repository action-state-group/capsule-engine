# SPDX-License-Identifier: Apache-2.0
"""One live check, decided the way a capsulectl rules checker decides it.

capsulectl hands its rules checker one external-check-input/v0 envelope per
check. ``decide_check_input`` decides one envelope under one pack: it installs
the pack in observe mode, builds the engine from the installed manifest as
``build_engine`` does (its runnable checks and ask sets, the caps limits read
from the ledger's activation records, so each limit names its source, and
the per-action reads), writes the envelope's history into a throwaway ledger
(``history_ledger``, with the pack's caps fold), and checks the record entry
(``action_for_check_input``) with what capsulectl passes beside it: the
task-authority record, the floor opening, and the record entry's
representing opening. The Go guard decides the same envelope with
``DecodeCheckInput`` and ``CheckWithHistory``; the shared conformance vectors
hold the two to the same decision.

The ledger lives in a temporary directory and is gone when the call returns;
its records are signed by ``signer`` (a fixed local key when none is given),
and no record leaves the call except as the returned capsules.
"""
from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

from cll.ledger import LedgerStore

from ..guards import Action, GuardDecision, GuardEngine, LocalSigner
from ..guards.checks import RUNNABLE_CHECKS
from ..packs import install_pack
from ..packs.install import engine_ask_sets
from ..packs.schema import PackDefinition
from ..policy.limits import read_caps_limits
from .live_history import HistoryLedger, history_ledger
from .replay import action_for_check_input

__all__ = ["LiveCheck", "decide_check_input", "decide_steps", "live_engine"]


@dataclass(frozen=True)
class LiveCheck:
    """A live check's decision, the ledger it was decided on (each record's
    capsule, in ledger order), and what ``history_ledger`` wrote."""

    decision: GuardDecision
    ledger: list[dict]
    written: HistoryLedger


def _default_signer() -> LocalSigner:
    return LocalSigner(key_id="live-check", secret=b"live-check")


def live_engine(pack: PackDefinition, store: LedgerStore, project_dir: str | Path, *, signer=None) -> GuardEngine:
    """The engine a live check runs over ``store``: ``pack`` installed in
    observe mode under ``project_dir``, wired as ``build_engine`` wires it. A
    live decision never evaluates an action's class under another taxonomy
    than the engine's (``guards/engine.py``); only a replay does."""
    return _wired(install_pack(pack, project_dir=project_dir, mode="observe"), store, signer)


def _wired(installed, store: LedgerStore, signer) -> GuardEngine:
    resolved = installed.resolved
    wickets = resolved.configured_wickets(RUNNABLE_CHECKS)
    gates, asks = engine_ask_sets(installed.pack, wickets)
    signer = signer or _default_signer()
    return GuardEngine(
        ledger=store, caps_fold=resolved.caps_fold(),
        caps_limits=lambda s: read_caps_limits(resolved, store, s),
        per_action_reads=resolved.per_action_reads(), signer_provider=lambda: signer,
        manifest_digest=resolved.manifest_digest, wickets=wickets,
        ask_gate_selectors=gates, ask_wickets=asks,
    )


def decide_check_input(
    pack: PackDefinition, envelope: dict, *, with_history: bool = True, signer=None
) -> LiveCheck:
    """The decision on one external-check-input/v0 envelope under ``pack``,
    by a fresh engine whose ledger is the envelope's history, or empty when
    ``with_history`` is false (the live path before history was passed)."""
    with tempfile.TemporaryDirectory() as tmp, LedgerStore(Path(tmp) / "ledger") as store:
        installed = install_pack(pack, project_dir=Path(tmp) / "live-project", mode="observe")
        engine = _wired(installed, store, signer)
        caps_fold = installed.resolved.caps_fold()
        written = history_ledger(envelope, store, caps_fold=caps_fold) if with_history else HistoryLedger(True, 0, 0, 0)
        ledger = [r.capsule for r in store.scan()]
        record = envelope["record"]
        action = action_for_check_input(record, item_ref=envelope.get("item_ref"))
        decision = engine.check(
            action,
            task_authority_record=envelope.get("task_authority_record"),
            commercial_bounds_opening=envelope.get("commercial_bounds_opening"),
            representing_opening=record.get("representing_opening"),
        )
        return LiveCheck(decision=decision, ledger=ledger, written=written)


def decide_steps(pack: PackDefinition, steps: list[dict], *, signer=None) -> list[GuardDecision]:
    """The decisions on a run of actions under ``pack``, each one an
    ``Action``'s fields as JSON (an array is a tuple), decided in order by one
    engine whose ledger starts empty and holds each decision before the next
    step is checked."""
    with tempfile.TemporaryDirectory() as tmp, LedgerStore(Path(tmp) / "ledger") as store:
        engine = _wired(install_pack(pack, project_dir=Path(tmp) / "live-project", mode="observe"), store, signer)
        return [engine.check(Action(**{k: tuple(v) if isinstance(v, list) else v for k, v in step.items()})) for step in steps]
