# SPDX-License-Identifier: Apache-2.0
"""``capsule setup enforce`` (design §3.5/§4/§6b): promotion, per check,
never bulk, always shadow-first. Only an accepted (T1) candidate whose
forward verdict is ``DETERMINISTIC`` has anything to enforce -- everything
else in this compiler's vocabulary is forward-unavailable or refused by
construction (design §2.2: the judge is never in the enforcement path), so
this module only ever governs attainment-kind outcomes.

**Containment is promotable ahead of the caps work** (design §2.3): a plan
is checked with ``guards/checks/plan_containment.py``'s
``check_plan_containment``, a pure function of ``(action, plan)`` with no
ledger read, so shadow replay and live dispatch here never touch the caps
lock at all -- this module never imports ``guards/engine.py``.

**Every forward refusal ships its reproduction command** (design §6b): the
plan a live dispatch checked against is never persisted -- it is recompiled
fresh from the accepted candidate every time (``compile_bridge``), so
``reproduce_refusal`` (backing ``capsule verify --refusal``) replays the
identical check from sealed inputs and can only ever agree or flag drift,
never merely re-assert.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.parse import quote

from capsule_ledger.ledger.api import LedgerAPI

from ..guards.action import Action
from ..guards.capsule import ALLOW, DENY, build_decision_capsule, build_event_capsule
from ..guards.checks.base import CheckOutcome
from ..guards.checks.plan_containment import check_plan_containment
from ..guards.plan import PlanDefinition
from ..guards.signing import Signer
from .candidates import AttainmentCandidate
from .compile_bridge import compiled_declaration_for
from .declarations import DeclarationStore, StoredCandidate
from .observe import EVENT_DISPATCH
from .scan import detail as _detail
from .scan import scan_event as _scan

__all__ = [
    "ENFORCE_MODES",
    "EVENT_ENFORCE_PROMOTED",
    "EnforceError",
    "EnforceStateStore",
    "ShadowResult",
    "ShadowReport",
    "DispatchResult",
    "ReproductionResult",
    "historical_actions_for",
    "run_shadow_report",
    "promote",
    "dispatch",
    "reproduce_refusal",
    "reproduction_command",
]

EVENT_ENFORCE_PROMOTED = "setup.enforce_promoted"
ENFORCE_MODES = frozenset({"shadow", "enforced"})
ENFORCE_STATE_DIRNAME = "enforce_state"


class EnforceError(ValueError):
    """A promotion or dispatch was attempted out of order -- not accepted
    yet, not forward-checkable at all, or not yet promoted past shadow."""


class EnforceStateStore:
    """One JSON file per outcome_id under ``<root>/enforce_state/``,
    recording only ``mode`` -- ``shadow`` (the only state a freshly
    accepted candidate starts in) or ``enforced`` (T3: a human promoted it
    after reading a shadow report). This is local operational state, not
    evidence -- the evidentiary record of a promotion is the
    ``setup.enforce_promoted`` capsule ``promote`` appends; this store just
    remembers which mode to dispatch in next time the CLI runs."""

    def __init__(self, root: str | Path) -> None:
        self._dir = Path(root) / ENFORCE_STATE_DIRNAME

    def _path(self, outcome_id: str) -> Path:
        return self._dir / f"{quote(outcome_id, safe='')}.json"

    def mode(self, outcome_id: str) -> str:
        path = self._path(outcome_id)
        if not path.is_file():
            return "shadow"
        return json.loads(path.read_text())["mode"]

    def set_mode(self, outcome_id: str, mode: str) -> None:
        if mode not in ENFORCE_MODES:
            raise ValueError(f"mode must be one of {sorted(ENFORCE_MODES)}; got {mode!r}")
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path(outcome_id).write_text(json.dumps({"mode": mode}, indent=2, sort_keys=True) + "\n")


def _plan_for(stored: StoredCandidate) -> PlanDefinition:
    if not isinstance(stored.candidate, AttainmentCandidate):
        raise EnforceError(
            f"{stored.candidate.outcome_id!r} has kind={stored.candidate.kind!r} -- only attainment "
            "candidates compile to a forward-checkable plan"
        )
    if stored.forward_verdict != "DETERMINISTIC":
        raise EnforceError(f"{stored.candidate.outcome_id!r} forward verdict is {stored.forward_verdict!r}, not DETERMINISTIC -- nothing to enforce")
    compiled = compiled_declaration_for(stored)
    if compiled.forward.plan is None:  # pragma: no cover - implied by DETERMINISTIC forward, defensive
        raise EnforceError(f"{stored.candidate.outcome_id!r} compiled with no plan despite a DETERMINISTIC forward verdict")
    return compiled.forward.plan


@dataclass(frozen=True)
class ShadowResult:
    action: Action
    outcome: CheckOutcome
    would_pass: bool


@dataclass(frozen=True)
class ShadowReport:
    outcome_id: str
    plan_digest: str
    results: tuple[ShadowResult, ...]

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def would_fail_count(self) -> int:
        return sum(1 for r in self.results if not r.would_pass)


def historical_actions_for(ledger: LedgerAPI, action_class: str) -> list[Action]:
    """Every observed dispatch of ``action_class`` (``observe``'s own
    ``EVENT_DISPATCH`` record), replayed back as an ``Action`` -- the
    default action source for ``run_shadow_report``'s "replay against
    history" (design §3.5), so a shadow report needs no separate fixture
    file: it reads exactly what ``observe`` already recorded."""
    dispatches = _scan(ledger, EVENT_DISPATCH)
    return [
        Action(
            verb=action_class,
            operator=r.capsule.get("operator", ""),
            developer=r.capsule.get("developer", ""),
        )
        for r in dispatches
        if _detail(r).get("action_class") == action_class
    ]


def run_shadow_report(outcome_id: str, actions: list[Action], *, store: DeclarationStore) -> ShadowReport:
    """Replay-before-merge (design §3.5): a pure, read-only replay of
    ``actions`` (typically every historical dispatch for this outcome's
    action_class, pulled from the observed ledger) against the plan an
    accepted candidate compiles to TODAY. Appends nothing -- "run the
    check, record what it would have refused, change nothing" is exactly
    what a caller does with the returned report (print it, attach it to a
    PR), not something this function does on its own."""
    stored = store.load(outcome_id)
    plan = _plan_for(stored)
    results = []
    for a in actions:
        outcome = check_plan_containment(a, plan)
        results.append(ShadowResult(action=a, outcome=outcome, would_pass=outcome.constraint.result == "pass"))
    return ShadowReport(outcome_id=outcome_id, plan_digest=plan.definition_digest(), results=tuple(results))


def promote(
    outcome_id: str,
    *,
    shadow_report: ShadowReport,
    store: DeclarationStore,
    enforce_state: EnforceStateStore,
    ledger: LedgerAPI,
    signer: Signer,
    operator: str,
    developer: str,
) -> dict:
    """T3: per-check, never bulk. Requires the candidate to already be T1
    ``accepted`` and requires a shadow report for the SAME outcome_id --
    there is no path to ``enforced`` that skips shadow."""
    stored = store.load(outcome_id)
    if stored.acceptance_state != "accepted":
        raise EnforceError(f"{outcome_id!r} is not accepted (T1) -- run `capsule setup confirm --accept` first")
    if shadow_report.outcome_id != outcome_id:
        raise EnforceError(f"shadow report is for {shadow_report.outcome_id!r}, not {outcome_id!r}")
    plan = _plan_for(stored)
    if shadow_report.plan_digest != plan.definition_digest():
        raise EnforceError(
            f"{outcome_id!r}'s shadow report was computed against a different plan_digest "
            f"({shadow_report.plan_digest} != {plan.definition_digest()}) -- re-run the shadow report before promoting"
        )
    capsule = build_event_capsule(
        operator=operator,
        developer=developer,
        signer=signer,
        event=EVENT_ENFORCE_PROMOTED,
        detail={
            "outcome_id": outcome_id,
            "plan_digest": shadow_report.plan_digest,
            "shadow_total": shadow_report.total,
            "shadow_would_fail": shadow_report.would_fail_count,
        },
    )
    ledger.append(capsule, consequential=False)
    enforce_state.set_mode(outcome_id, "enforced")
    return capsule


@dataclass(frozen=True)
class DispatchResult:
    capsule: dict
    passed: bool
    reproduction_command: str | None


def reproduction_command(capsule_id: str, *, setup_dir: str | Path) -> str:
    """``setup_dir`` is the ``capsule setup init`` root (what ``--declarations``
    on ``capsule verify --refusal`` expects) -- NOT ``DeclarationStore.directory``,
    which is one level deeper (``<setup_dir>/declarations``); ``DeclarationStore``
    appends that suffix itself, so passing its own ``.directory`` here would
    double it."""
    return f"capsule verify {capsule_id} --refusal --declarations {setup_dir}"


def dispatch(
    outcome_id: str,
    action: Action,
    *,
    store: DeclarationStore,
    enforce_state: EnforceStateStore,
    ledger: LedgerAPI,
    signer: Signer,
    setup_dir: str | Path,
) -> DispatchResult:
    """The live forward check, once promoted. ``action.action_class`` is
    overwritten to ``outcome_id`` before checking and before sealing --
    disclosed on the decision capsule (``guards/capsule.py``'s
    ``_payload_extension``), which is what lets a later reproduction
    identify which accepted candidate governed this decision from the
    sealed capsule alone, with no side-channel state to keep in sync."""
    if enforce_state.mode(outcome_id) != "enforced":
        raise EnforceError(
            f"{outcome_id!r} is still in shadow mode -- promote it first (`capsule setup enforce promote`) "
            "before dispatching live traffic through it"
        )
    stored = store.load(outcome_id)
    plan = _plan_for(stored)
    action = replace(action, action_class=outcome_id)
    check = check_plan_containment(action, plan)
    passed = check.constraint.result == "pass"
    capsule = build_decision_capsule(
        action=action,
        outcome=ALLOW if passed else DENY,
        constraints=(check.constraint,),
        signer=signer,
        checkpoint={"outcome_id": outcome_id, "plan_digest": plan.definition_digest()},
        chain_parent=check.chain_parent,
        chain_relation=check.chain_relation,
    )
    ledger.append(capsule, consequential=True)
    repro = None if passed else reproduction_command(capsule["capsule_id"], setup_dir=setup_dir)
    return DispatchResult(capsule=capsule, passed=passed, reproduction_command=repro)


@dataclass(frozen=True)
class ReproductionResult:
    capsule_id: str
    outcome_id: str
    original_decision: str
    reproduced_result: str
    matches: bool


def reproduce_refusal(capsule_id: str, *, ledger: LedgerAPI, store: DeclarationStore) -> ReproductionResult:
    """Backs ``capsule verify --refusal``: fetch the sealed decision
    capsule, recompile the accepted declaration it names back into a fresh
    ``PlanDefinition`` (never a persisted one), reconstruct the ``Action``
    from the capsule's own disclosed fields (``Action.from_capsule`` --
    already this codebase's own dry-run-report replay path), and re-run
    ``check_plan_containment``. Containment's purity (design §2.3) is what
    makes this an EXACT reproduction rather than a re-assertion: the same
    plan, the same action, the same pure function, every time."""
    record = ledger.fetch(capsule_id)
    if record is None:
        raise EnforceError(f"no such capsule {capsule_id!r} in this ledger")
    capsule = record.capsule
    payload = capsule.get("asg_payload") or {}
    outcome_id = payload.get("action_class")
    if not outcome_id or not store.exists(outcome_id):
        raise EnforceError(
            f"capsule {capsule_id!r} does not disclose a known outcome_id in asg_payload.action_class "
            "-- it was not produced by `capsule setup enforce dispatch`"
        )
    stored = store.load(outcome_id)
    plan = _plan_for(stored)
    action = Action.from_capsule(capsule, action_class=outcome_id)
    check = check_plan_containment(action, plan)
    reproduced_result = check.constraint.result
    original_decision = (capsule.get("disposition") or {}).get("decision", "")
    original_passed = original_decision == "accept"
    return ReproductionResult(
        capsule_id=capsule_id,
        outcome_id=outcome_id,
        original_decision=original_decision,
        reproduced_result=reproduced_result,
        matches=(reproduced_result == "pass") == original_passed,
    )
