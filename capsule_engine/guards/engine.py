# SPDX-License-Identifier: Apache-2.0
"""``GuardEngine``: orchestrates the reference checks, decides allow/deny/
escalate, and appends the decision as a capsule.

Implements the gating-decisions doc §1 failure-semantics table literally --
see ``docs/failure-semantics.md`` for the public short version. Every branch
below cites the table row it implements.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from capsule_ledger.ledger.api import LedgerAPI

from ..events.capsule import build_event_capsule
from ..folds.definition import FoldDefinition
from ..policy.errors import PolicyManifestError
from .action import Action
from .capsule import ALLOW, DENY, ESCALATE, ConstraintOutcome, build_decision_capsule, not_applicable_evidence
from .checks import (
    AUTHORIZATION_CHECKS,
    CONFIGURED_CHECKS,
    RUNNABLE_CHECKS,
    TASK_AUTHORITY_CHECKS,
    AuthorizationRecord,
    CheckOutcome,
    LimitSources,
    TaskAuthorityRecord,
    cap_for,
    check_caps,
    check_dedupe,
    check_plan_containment,
    check_verify_before_dispatch,
    require_disposition,
    require_per_action_reads,
    resolve_caps_minor,
)
from .checks.action_class_gate import Selector
from .classes import ActionClass, classify
from .plan import PlanDefinition
from .signing import Signer, SigningKeyUnavailable
from .wickets.definition import WicketDefinition

if TYPE_CHECKING:
    # ``policy`` imports ``guards``; the engine only calls ``in_force``.
    from ..policy.limits import CapsLimits

__all__ = ["GuardDecision", "GuardEngine"]

_DEDUPE_WINDOW_DAYS = 30


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _shift(ts: str, *, days: int) -> str:
    text = ts[:-1] + "+00:00" if ts.endswith("Z") else ts
    dt = datetime.fromisoformat(text) - timedelta(days=days)
    return dt.isoformat().replace("+00:00", "Z")


@dataclass
class _OpenDegradation:
    kind: str
    started_at: str
    cause: str


@dataclass(frozen=True)
class GuardDecision:
    """Every field the failure-semantics table's "Recorded" column promises,
    plus the ordinary decision shape (dev-persona doc: status-check output)."""

    outcome: str  # allow | deny | escalate
    dry_run: bool
    degraded: bool
    degradation_kind: str | None
    constraints: tuple[ConstraintOutcome, ...]
    fold_envelopes: tuple[dict, ...]
    checkpoint: dict
    capsule: dict | None
    reason: str


class GuardEngine:
    def __init__(
        self,
        *,
        ledger: LedgerAPI,
        caps_fold: FoldDefinition,
        signer_provider: Callable[[], Signer | None],
        caps_minor: dict[str, int] | None = None,
        per_action_minor: dict[str, int] | None = None,
        per_action_reads: str | None = None,
        freshness_bound_ms: int = 5_000,
        fail_open_classes: frozenset[str] = frozenset(),
        engine_available: Callable[[], bool] = lambda: True,
        view_healthy: Callable[[], bool] = lambda: True,
        witness_reachable: Callable[[], bool] = lambda: True,
        checkpoint_age_ms: Callable[[], int] = lambda: 0,
        manifest_digest: str | None = None,
        plan: PlanDefinition | None = None,
        wickets: tuple[WicketDefinition, ...] = (),
        caps_limits: Callable[[Signer], CapsLimits] | None = None,
        clock: Callable[[], str] | None = None,
        ask_gate_selectors: frozenset[str] = frozenset(),
    ) -> None:
        if caps_limits is not None and (caps_minor or per_action_minor):
            raise ValueError("give caps limits as caps_limits or as caps_minor/per_action_minor, not both")
        self._ledger = ledger
        self._caps_fold = caps_fold
        self._signer_provider = signer_provider
        self._caps_minor = resolve_caps_minor(caps_minor or {})
        # Per-action limits, compared against the proposed amount alone; a
        # class applies one only where it also has a window limit.
        self._per_action_minor = resolve_caps_minor(per_action_minor or {})
        # The caps wicket's ``per_action_reads``: ``None`` reads the capture.
        require_per_action_reads(per_action_reads)
        self._per_action_reads = per_action_reads
        # Limits read per decision from activated policy (``policy/limits.py``),
        # given this engine's signer to verify the activation records with:
        # the value in force at the action's timestamp and at ``clock()``, and
        # where it came from. ``None``: the two tables above, with no
        # provenance in the evidence.
        self._caps_limits = caps_limits
        self._clock = clock or _utc_now
        self._freshness_bound_ms = freshness_bound_ms
        self._fail_open_classes = fail_open_classes
        self._engine_available = engine_available
        self._view_healthy = view_healthy
        self._witness_reachable = witness_reachable
        self._checkpoint_age_ms = checkpoint_age_ms
        # The forward-compiled plan this engine checks containment against
        # -- ``None`` when no plan is configured
        # for this engine instance, in which case ``check_plan_containment``
        # reports ``n/a`` for every action (same "absent config -> n/a"
        # shape ``caps`` already uses when no per-class cap is configured).
        self._plan = plan
        # Wickets configuring a ``CONFIGURED_CHECKS`` check, run in this
        # order after every reference check. Empty by default, so an engine
        # with none configured produces byte-for-byte the decisions it
        # always has (same reasoning as ``plan`` above).
        unknown = [w.check for w in wickets if w.check not in RUNNABLE_CHECKS]
        if unknown:
            raise ValueError(f"wickets configure checks the engine cannot run per decision: {unknown}")
        self._wickets = wickets
        # A counterparty_list wicket configured ``disposition: ask`` joins
        # the failures that ask an approver; ``deny`` leaves it refusing.
        escalatable = set(_ESCALATABLE)
        for wicket in wickets:
            if wicket.check == "counterparty_list":
                require_disposition(wicket.config["disposition"])
                if wicket.config["disposition"] == "ask":
                    escalatable.add("counterparty_list")
        self._escalatable = frozenset(escalatable)
        # The action_class_gate selectors whose obligations all declare
        # ``default_disposition: ASK`` (``packs/install.py`` derives them
        # from the installed pack). An action_class_gate failure asks an
        # approver only when every selector that failed it is one of these.
        # Empty by default: with no pack, the gate refuses as it always has.
        self.ask_gate_selectors = ask_gate_selectors
        # The active policy manifest's own digest (``capsule_ledger.policy.
        # resolve_manifest(...).manifest_digest``), pinned onto every
        # decision capsule this engine produces (``build_decision_capsule``'s
        # ``manifest_digest`` param) so "which policy governed this
        # decision" is checkable directly off the capsule. ``None`` when no
        # manifest is configured for this engine instance.
        self._manifest_digest = manifest_digest
        self._open: dict[str, _OpenDegradation] = {}

    # -- introspection (tests / recovery bookkeeping) -----------------------

    def open_degradations(self) -> dict[str, str]:
        return {kind: deg.cause for kind, deg in self._open.items()}

    def _get_signer(self) -> Signer | None:
        try:
            return self._signer_provider()
        except SigningKeyUnavailable:
            return None

    def _tree_size(self) -> int:
        return sum(1 for _ in self._ledger.scan())

    # -- the public API -------------------------------------------------

    def check(
        self,
        action: Action,
        *,
        dry_run: bool = False,
        chain_parent: str | None = None,
        chain_relation: str | None = None,
        task_authority_record: TaskAuthorityRecord | None = None,
        authorization_record: AuthorizationRecord | None = None,
    ) -> GuardDecision:
        """``task_authority_record`` is the whole sealed task-authority record
        ``action.task_authority_ref`` names, read only by a
        ``task_authority`` wicket and only when the engine's own digest of it
        equals the reference (``guards/checks/task_authority.py``).
        ``authorization_record`` is the whole sealed approval record
        ``action.authorized_by`` names, read only by a
        ``promise_requires_approval`` wicket and only when the engine's own
        digest of it equals that reference
        (``guards/checks/promise_requires_approval.py``)."""
        ac = classify(action.action_class)
        consequential = ac.consequential
        may_fail_open = ac.fail_open_allowed and action.action_class in self._fail_open_classes
        age_ms = self._checkpoint_age_ms()

        # Row: "Local view unavailable or corrupt" -- fail closed; rebuild by
        # replay, then resume. Checked first: every other check reads the
        # ledger through this same view.
        if not self._view_healthy():
            before = self._tree_size()
            reindex = getattr(self._ledger, "reindex", None)
            if reindex is not None:
                reindex()
            after = self._tree_size()
            self._open["view_rebuild"] = _OpenDegradation(
                kind="view_rebuild",
                started_at=_utc_now(),
                cause=f"local view unavailable/corrupt; rebuilt by replay, range_replayed=[0,{after}] (was {before})",
            )
            self._try_flush_recoveries(action)
            return GuardDecision(
                outcome=DENY,
                dry_run=dry_run,
                degraded=True,
                degradation_kind="view_rebuild",
                constraints=(),
                fold_envelopes=(),
                checkpoint={"age_ms": age_ms, "anchor_status": "unanchored"},
                capsule=None,
                reason="local view was unavailable/corrupt; rebuilt by replay; fail closed for this decision",
            )

        # Row: "Signing key unavailable" -- fail closed; an unsigned record
        # is not a record, so nothing can be persisted for this decision.
        signer = self._get_signer()
        if signer is None:
            self._open["signing_key"] = _OpenDegradation(
                kind="signing_key", started_at=_utc_now(), cause="signing key unavailable"
            )
            return GuardDecision(
                outcome=DENY,
                dry_run=dry_run,
                degraded=True,
                degradation_kind="signing_key",
                constraints=(),
                fold_envelopes=(),
                checkpoint={"age_ms": age_ms, "anchor_status": "unanchored"},
                capsule=None,
                reason="signing key unavailable; fail closed, an unsigned record is not a record",
            )

        # Row: "View is stale beyond the declared freshness bound" -- default
        # fail closed for consequential classes; fail-open only for a class
        # explicitly configured for it, and every fail-open dispatch is
        # recorded as reduced-assurance.
        reduced_assurance = False
        stale = age_ms > self._freshness_bound_ms
        if stale and (consequential or not may_fail_open):
            return self._infra_deny(
                action,
                dry_run=dry_run,
                signer=signer,
                age_ms=age_ms,
                constraint_id="freshness",
                reason=f"view is stale ({age_ms}ms) beyond the freshness bound ({self._freshness_bound_ms}ms)",
            )
        if stale:
            reduced_assurance = True

        # Row: "Sidecar or engine unreachable" -- fail closed by default;
        # fail-open requires an explicit per-class opt-in.
        if not self._engine_available() and (consequential or not may_fail_open):
            return self._infra_deny(
                action,
                dry_run=dry_run,
                signer=signer,
                age_ms=age_ms,
                constraint_id="engine_availability",
                reason="fold engine is unreachable; fail closed (no per-class fail-open configured)",
            )
        if not self._engine_available():
            reduced_assurance = True

        # Limits from activated policy are re-read for every decision, so an
        # activation appended after this engine was built applies at once. A
        # history they cannot be read from fails closed, recorded.
        try:
            caps_limits = self._caps_limits(signer) if self._caps_limits is not None else None
        except PolicyManifestError as exc:
            return self._infra_deny(
                action,
                dry_run=dry_run,
                signer=signer,
                age_ms=age_ms,
                constraint_id="policy_binding",
                reason=f"the limits in force could not be read from the activation records ({exc.reason}): {exc}",
            )

        # -- the three reference checks --------------------------------
        since_dedupe = _shift(action.resolved_timestamp(), days=_DEDUPE_WINDOW_DAYS)
        dedupe_out = check_dedupe(action, self._ledger, since=since_dedupe)

        cap_minor, per_action_cap_minor, limit_sources = self._limits_for(action, caps_limits)
        if cap_minor is not None:
            caps_out = check_caps(
                action,
                self._ledger,
                definition=self._caps_fold,
                cap_minor=cap_minor,
                per_action_cap_minor=per_action_cap_minor,
                per_action_reads=self._per_action_reads,
                limit_sources=limit_sources,
            )
        else:
            caps_out = CheckOutcome(
                constraint=ConstraintOutcome(
                    id="caps",
                    result="n/a",
                    reason="no cap configured for this action class",
                    evidence=not_applicable_evidence("caps", in_scope=False),
                    check_type="policy",
                    method=self._caps_fold.fold_id,
                )
            )

        vbd_out = check_verify_before_dispatch(action, self._ledger)

        constraints = (dedupe_out.constraint, caps_out.constraint, vbd_out.constraint)
        if self._plan is not None:
            # Pure function of (action, plan) -- no ledger read (module
            # docstring, guards/checks/plan_containment.py). Only added to
            # this decision's constraints when a plan is actually configured
            # -- unlike ``caps`` (present, as ``n/a``, since this engine's
            # very first release), adding a constraint unconditionally here
            # would change the ``capsule_id`` of every decision capsule any
            # existing caller has ever produced, plan or no plan. An engine
            # with no plan configured (the default, and every caller that
            # predates this check) is byte-for-byte unchanged.
            plan_out = check_plan_containment(action, self._plan)
            constraints = (*constraints, plan_out.constraint)
        gate_runs: list[tuple[WicketDefinition, ConstraintOutcome]] = []
        for wicket in self._wickets:
            if wicket.check in TASK_AUTHORITY_CHECKS:
                out = TASK_AUTHORITY_CHECKS[wicket.check](action, task_authority_record, wicket.config)
            elif wicket.check in AUTHORIZATION_CHECKS:
                out = AUTHORIZATION_CHECKS[wicket.check](
                    action, task_authority_record, authorization_record, wicket.config
                )
            else:
                out = CONFIGURED_CHECKS[wicket.check](action, self._ledger, wicket.config)
            constraints = (*constraints, out.constraint)
            if wicket.check == _GATE:
                gate_runs.append((wicket, out.constraint))
        fold_envelopes = tuple(caps_out.fold_envelopes)
        escalatable = self._escalatable
        if self._gate_failures_ask(gate_runs):
            escalatable = escalatable | {_GATE}
        outcome = _decide(constraints, ac, escalatable)

        resolved_parent, resolved_relation = chain_parent, chain_relation
        if resolved_parent is None:
            for out in (vbd_out, dedupe_out):
                if out.chain_parent is not None:
                    resolved_parent, resolved_relation = out.chain_parent, out.chain_relation
                    break

        # Row: "Anchor or witness unreachable" -- NEVER blocks (anchoring is
        # async; the record is complete without it). v0 has not built
        # anchoring at all yet, so every checkpoint is unanchored regardless
        # of witness reachability -- that unconditionality (never a
        # fail-closed branch keyed on it) is the property under test.
        checkpoint = {
            "tree_size": self._tree_size(),
            "age_ms": age_ms,
            "anchor_status": "unanchored",
            "witness_reachable": self._witness_reachable(),
        }
        if reduced_assurance:
            checkpoint["reduced_assurance"] = True
        if dry_run:
            checkpoint["dry_run"] = True

        reason_obj = {
            "outcome": outcome,
            "constraints": [{"id": c.id, "result": c.result} for c in constraints],
        }

        capsule = build_decision_capsule(
            action=action,
            outcome=outcome,
            constraints=constraints,
            signer=signer,
            checkpoint=checkpoint,
            reason=reason_obj,
            chain_parent=resolved_parent,
            chain_relation=resolved_relation,
            manifest_digest=self._manifest_digest,
        )

        # Row: "Ledger append fails (disk full, WAL error)" -- fail closed
        # for consequential classes; the action does not dispatch.
        try:
            self._ledger.append(capsule, consequential=consequential and not dry_run)
        except OSError as exc:
            self._open["ledger_append"] = _OpenDegradation(
                kind="ledger_append", started_at=_utc_now(), cause=str(exc)
            )
            return GuardDecision(
                outcome=DENY,
                dry_run=dry_run,
                degraded=True,
                degradation_kind="ledger_append",
                constraints=constraints,
                fold_envelopes=fold_envelopes,
                checkpoint=checkpoint,
                capsule=None,
                reason=f"ledger append failed ({exc}); fail closed, action does not dispatch",
            )

        self._try_flush_recoveries(action)

        return GuardDecision(
            outcome=outcome,
            dry_run=dry_run,
            degraded=False,
            degradation_kind=None,
            constraints=constraints,
            fold_envelopes=fold_envelopes,
            checkpoint=checkpoint,
            capsule=capsule,
            reason=_summarize(constraints, outcome, ac, escalatable),
        )

    def _gate_failures_ask(self, gate_runs: list[tuple[WicketDefinition, ConstraintOutcome]]) -> bool:
        """Whether some action_class_gate outcome failed and every failed one
        failed only on selectors declared ASK. A failure naming no selector
        (a class the taxonomy cannot resolve fails closed) never asks."""
        failed = [(w, c) for w, c in gate_runs if c.result == "fail"]
        for wicket, outcome in failed:
            failing = _failing_selectors(wicket.config["selectors"], outcome)
            if not failing or not failing <= self.ask_gate_selectors:
                return False
        return bool(failed)

    def _limits_for(
        self, action: Action, caps_limits: CapsLimits | None
    ) -> tuple[int | None, int | None, LimitSources | None]:
        """The window and per-action limits for ``action`` and, when read from
        activated policy, their sources. A per-action limit applies only to a
        class that also has a window limit."""
        if caps_limits is None:
            cap_minor = cap_for(self._caps_minor, action.action_class)
            per_action = cap_for(self._per_action_minor, action.action_class) if cap_minor is not None else None
            return cap_minor, per_action, None
        at, now = action.resolved_timestamp(), self._clock()
        window = caps_limits.in_force("caps_minor", action.action_class, at=at, now=now)
        if window is None:
            return None, None, None
        sources = LimitSources(window=window.source)
        per_action = caps_limits.in_force("per_action_minor", action.action_class, at=at, now=now)
        if per_action is None:
            return window.value_minor, None, sources
        sources["per_action"] = per_action.source
        return window.value_minor, per_action.value_minor, sources

    # -- degradation recovery --------------------------------------------

    def _try_flush_recoveries(self, action: Action) -> None:
        """Append a degradation/recovery record for every open degradation,
        now that we're in a position to sign and append one. Best-effort:
        a nested failure leaves the entry open for the next successful call
        (never raises out of here -- recovery bookkeeping must not itself
        become a new source of check() failures)."""
        if not self._open:
            return
        signer = self._get_signer()
        if signer is None:
            return
        for kind in list(self._open):
            deg = self._open[kind]
            event = "operator_alert" if kind == "signing_key" else "degradation_recovered"
            detail = {"kind": kind, "started_at": deg.started_at, "recovered_at": _utc_now(), "cause": deg.cause}
            try:
                record = build_event_capsule(
                    operator=action.operator, developer=action.developer, signer=signer, event=event, detail=detail
                )
                self._ledger.append(record, consequential=False)
            except OSError:
                continue
            del self._open[kind]

    def _infra_deny(
        self,
        action: Action,
        *,
        dry_run: bool,
        signer: Signer,
        age_ms: int,
        constraint_id: str,
        reason: str,
    ) -> GuardDecision:
        """Staleness/engine-unreachable fail-closed: unlike ledger-append or
        signing-key failures, this IS recordable -- the table's own
        "Recorded" column for this row is "staleness recorded in the
        outcome", not a degradation-on-recovery record."""
        constraint = ConstraintOutcome(id=constraint_id, result="fail", reason=reason, check_type="infra")
        checkpoint = {"tree_size": self._tree_size(), "age_ms": age_ms, "anchor_status": "unanchored"}
        capsule = build_decision_capsule(
            action=action,
            outcome=DENY,
            constraints=(constraint,),
            signer=signer,
            checkpoint=checkpoint,
            reason={"outcome": DENY, "constraints": [{"id": constraint_id, "result": "fail"}]},
            manifest_digest=self._manifest_digest,
        )
        try:
            self._ledger.append(capsule, consequential=classify(action.action_class).consequential and not dry_run)
        except OSError as exc:
            self._open["ledger_append"] = _OpenDegradation(kind="ledger_append", started_at=_utc_now(), cause=str(exc))
            capsule = None
        return GuardDecision(
            outcome=DENY,
            dry_run=dry_run,
            degraded=False,
            degradation_kind=None,
            constraints=(constraint,),
            fold_envelopes=(),
            checkpoint=checkpoint,
            capsule=capsule,
            reason=reason,
        )


# Failures that ask an approver rather than refuse: an over-limit spend and
# a first-time counterparty.
_ESCALATABLE = frozenset({"caps", "counterparty_seen_before"})
_GATE = "action_class_gate"


def _decide(
    constraints: tuple[ConstraintOutcome, ...],
    action_class: ActionClass,
    escalatable: frozenset[str] = _ESCALATABLE,
) -> str:
    """allow/deny/escalate per D2 (2026-08-05): a clean run allows. A hold
    escalates only when every failing constraint is in ``escalatable``
    (``_ESCALATABLE`` -- `caps`, `counterparty_seen_before` -- plus a
    `counterparty_list` configured to ask, plus an `action_class_gate`
    failure whose failing selectors' obligations all declare ASK) and the
    triggering class has an
    `approver_role` configured -- an integrity failure
    (`verify_before_dispatch`, whether the cited mandate is missing or fails
    re-verification), a dedupe hit, or an escalatable failure on a class with
    no approver configured all hard-deny, unconditionally."""
    fails = {c.id for c in constraints if c.result == "fail"}
    if not fails:
        return ALLOW
    if fails <= escalatable and action_class.approver_role is not None:
        return ESCALATE
    return DENY


def _failing_selectors(selectors: Mapping[str, Selector], outcome: ConstraintOutcome) -> frozenset[str]:
    """The selectors a failed action_class_gate outcome failed on: those its
    evidence lists as matched whose ``on_match`` is ``fail``."""
    matched = (outcome.evidence or {}).get("matched_selectors", ())
    return frozenset(sid for sid in matched if selectors[sid]["on_match"] == "fail")


def _summarize(
    constraints: tuple[ConstraintOutcome, ...],
    outcome: str,
    action_class: ActionClass,
    escalatable: frozenset[str],
) -> str:
    parts = [f"{c.id}={c.result}" for c in constraints]
    summary = f"{outcome}: " + ", ".join(parts)
    fails = {c.id for c in constraints if c.result == "fail"}
    if outcome == DENY and fails <= escalatable and action_class.approver_role is None:
        summary += (
            f"; every failure may ask an approver, but action class {action_class.name!r} "
            "names no approver_role, so it is refused"
        )
    return summary
