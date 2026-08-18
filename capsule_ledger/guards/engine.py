# SPDX-License-Identifier: Apache-2.0
"""``GuardEngine``: orchestrates the reference checks, decides allow/deny/
escalate, and appends the decision as a capsule.

Implements the gating-decisions doc §1 failure-semantics table literally --
see ``docs/failure-semantics.md`` for the public short version. Every branch
below cites the table row it implements.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ..folds.definition import FoldDefinition
from ..ledger.api import LedgerAPI
from .action import Action
from .capsule import ALLOW, DENY, ESCALATE, ConstraintOutcome, build_decision_capsule, build_event_capsule
from .checks import CheckOutcome, check_caps, check_dedupe, check_verify_before_dispatch
from .classes import ActionClass, classify
from .signing import Signer, SigningKeyUnavailable

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
        freshness_bound_ms: int = 5_000,
        fail_open_classes: frozenset[str] = frozenset(),
        engine_available: Callable[[], bool] = lambda: True,
        view_healthy: Callable[[], bool] = lambda: True,
        witness_reachable: Callable[[], bool] = lambda: True,
        checkpoint_age_ms: Callable[[], int] = lambda: 0,
        manifest_digest: str | None = None,
    ) -> None:
        self._ledger = ledger
        self._caps_fold = caps_fold
        self._signer_provider = signer_provider
        self._caps_minor = caps_minor or {}
        self._freshness_bound_ms = freshness_bound_ms
        self._fail_open_classes = fail_open_classes
        self._engine_available = engine_available
        self._view_healthy = view_healthy
        self._witness_reachable = witness_reachable
        self._checkpoint_age_ms = checkpoint_age_ms
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
    ) -> GuardDecision:
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

        # -- the three reference checks --------------------------------
        since_dedupe = _shift(action.resolved_timestamp(), days=_DEDUPE_WINDOW_DAYS)
        dedupe_out = check_dedupe(action, self._ledger, since=since_dedupe)

        cap_minor = self._caps_minor.get(action.action_class)
        if cap_minor is not None:
            caps_out = check_caps(action, self._ledger, definition=self._caps_fold, cap_minor=cap_minor)
        else:
            caps_out = CheckOutcome(
                constraint=ConstraintOutcome(
                    id="caps",
                    result="n/a",
                    reason="no cap configured for this action class",
                    check_type="policy",
                    method=self._caps_fold.fold_id,
                )
            )

        vbd_out = check_verify_before_dispatch(action, self._ledger)

        constraints = (dedupe_out.constraint, caps_out.constraint, vbd_out.constraint)
        fold_envelopes = tuple(caps_out.fold_envelopes)
        outcome = _decide(constraints, ac)

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
            reason=_summarize(constraints, outcome),
        )

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


def _decide(constraints: tuple[ConstraintOutcome, ...], action_class: ActionClass) -> str:
    """allow/deny/escalate per D2 (2026-08-05): a clean run allows. A hold
    escalates only when the *sole* failing constraint is `caps` and the
    triggering class has an `approver_role` configured -- an integrity
    failure (`verify_before_dispatch`, whether the cited mandate is missing
    or fails re-verification), a dedupe hit, or a cap breach on a class with
    no approver configured all hard-deny, unconditionally."""
    fails = {c.id for c in constraints if c.result == "fail"}
    if not fails:
        return ALLOW
    if fails == {"caps"} and action_class.approver_role is not None:
        return ESCALATE
    return DENY


def _summarize(constraints: tuple[ConstraintOutcome, ...], outcome: str) -> str:
    parts = [f"{c.id}={c.result}" for c in constraints]
    return f"{outcome}: " + ", ".join(parts)
