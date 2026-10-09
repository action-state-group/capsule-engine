# SPDX-License-Identifier: Apache-2.0
"""``GuardEngine``: orchestrates the reference checks, decides allow/deny/
escalate, and appends the decision as a capsule.

Implements the gating-decisions doc §1 failure-semantics table literally --
see ``docs/failure-semantics.md`` for the public short version. Every branch
below cites the table row it implements.

An action names the taxonomy its ``action_class`` was drawn from
(``Action.taxonomy_version``). A live decision on an action naming another
version than the engine's table never evaluates its class: ``caps`` and every
configured check keyed on ``action_class`` is ``n/a``, in scope, with evidence
naming both versions (``TaxonomyMismatch``). A held decision that nothing else
fails is refused and sealed ``reject``, its ``verdict`` ``not_evaluable``: an
action never evaluated is never allowed, and no later check counts it as an
accepted act. A replay (``evaluate_under_record_taxonomy``) evaluates a record
under the table it was sealed with when the engine carries that version
(``classes.carried_taxonomy``), and holds it the same way when it does not.

An action whose record states its deal twice, with different values
(``Action.deal_id_conflict``), names no deal, so no check can place it in one.
It is decided as any action without a deal, except that what would be allowed
is refused and sealed ``reject``, its ``verdict`` ``not_evaluable``
(``DealIdConflict``): neither value is picked, and the act is never counted
later as seen or as spend.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, TypedDict

from capsule_ledger.ledger.api import LedgerAPI

from ..events.capsule import build_event_capsule
from ..folds.definition import FoldDefinition
from ..policy.errors import PolicyManifestError
from .action import Action
from .capsule import ALLOW, DENY, ESCALATE, ConstraintOutcome, build_decision_capsule, not_applicable_evidence
from .checks import (
    AUTHORIZATION_CHECKS,
    COMMERCIAL_BOUNDS_CHECKS,
    CONFIGURED_CHECKS,
    RUNNABLE_CHECKS,
    TASK_AUTHORITY_CHECKS,
    AuthorizationRecord,
    CheckOutcome,
    CommercialBoundsOpening,
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
from .checks.action_class_gate import Selector, check_action_class_gate
from .classes import (
    CARRIED_TAXONOMY_VERSIONS,
    ENGINE_TAXONOMY,
    ActionClass,
    TaxonomyTable,
    carried_taxonomy,
    classify,
)
from .plan import PlanDefinition
from .signing import Signer, SigningKeyUnavailable
from .wickets.definition import WicketDefinition

if TYPE_CHECKING:
    # ``policy`` imports ``guards``; the engine only calls ``in_force``.
    from ..policy.limits import CapsLimits

__all__ = [
    "ASK_RULE_EXCLUDED_CHECKS",
    "NOT_EVALUABLE",
    "DealIdConflict",
    "GuardDecision",
    "GuardEngine",
    "TaxonomyMismatch",
]

NOT_EVALUABLE = "not_evaluable"

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
    # Set when the action's taxonomy version is not the one its class was
    # evaluated under: the class-keyed checks were not evaluated.
    taxonomy_mismatch: TaxonomyMismatch | None = None
    # Set when the action's record states its deal twice, with different
    # values: the action is in no deal, and is never allowed.
    deal_id_conflict: DealIdConflict | None = None

    @property
    def verdict(self) -> str:
        """``outcome``, except that a decision refused only because its
        class-keyed checks were left unevaluated (``taxonomy_mismatch``) or
        its deal could not be read (``deal_id_conflict``), with no constraint
        failed, is ``not_evaluable``."""
        unevaluated = self.taxonomy_mismatch is not None or self.deal_id_conflict is not None
        if unevaluated and not any(c.result == "fail" for c in self.constraints):
            return NOT_EVALUABLE
        return self.outcome


class TaxonomyMismatch(TypedDict):
    """The taxonomy version an action names, and the engine's."""

    record_taxonomy_version: str
    engine_taxonomy_version: str


class DealIdConflict(TypedDict):
    """The fields an action's record states its deal in, which differ.
    Names only, never the values."""

    fields: tuple[str, ...]


class TaxonomyHeldEvidence(TypedDict):
    """``not_applicable_evidence`` for a class-keyed check left unevaluated
    because of ``taxonomy_mismatch``."""

    constraint_id: str
    in_scope: bool
    missing_field: None
    taxonomy_mismatch: TaxonomyMismatch


class GuardEngine:
    def __init__(
        self,
        *,
        ledger: LedgerAPI,
        caps_fold: FoldDefinition | None,
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
        ask_wickets: frozenset[str] = frozenset(),
        evaluate_under_record_taxonomy: bool = False,
    ) -> None:
        if caps_limits is not None and (caps_minor or per_action_minor):
            raise ValueError("give caps limits as caps_limits or as caps_minor/per_action_minor, not both")
        if caps_fold is None and (caps_minor or per_action_minor):
            raise ValueError("caps limits were given but no caps fold definition to read spend with")
        self._ledger = ledger
        # ``None`` when the pack cites no ``caps`` definition: a pack may omit
        # it. ``caps`` is then recorded ``n/a``, out of scope, on every
        # decision, and no limit is read for it.
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
        # The configured checks whose obligations all declare
        # ``default_disposition: ASK`` (``packs/install.py`` derives them from
        # the installed pack): a failure of one asks an approver. An integrity
        # check never asks, and the two checks that carry their own ask rule
        # (the gate's selectors, the list's ``disposition``) are refused here.
        refused = ask_wickets & ASK_RULE_EXCLUDED_CHECKS
        if refused:
            raise ValueError(
                f"these checks cannot be made to ask an approver by a declared disposition: {sorted(refused)}"
            )
        unconfigured = ask_wickets - {w.check for w in wickets}
        if unconfigured:
            raise ValueError(f"ask_wickets names checks no configured wicket runs: {sorted(unconfigured)}")
        self._ask_wickets = ask_wickets
        escalatable |= ask_wickets
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
        # A replay of sealed history evaluates each record under the
        # taxonomy it names, where the engine carries it; live, an action
        # naming another taxonomy than the engine's is not evaluated.
        self._evaluate_under_record_taxonomy = evaluate_under_record_taxonomy
        self._open: dict[str, _OpenDegradation] = {}

    # -- introspection (tests / recovery bookkeeping) -----------------------

    @property
    def ask_wickets(self) -> frozenset[str]:
        """The configured checks whose failure asks an approver because every
        obligation bound to them declares ASK; fixed when the engine is built."""
        return self._ask_wickets

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
        commercial_bounds_opening: CommercialBoundsOpening | None = None,
    ) -> GuardDecision:
        """``task_authority_record`` is the whole sealed task-authority record
        ``action.task_authority_ref`` names, read only by a
        ``task_authority`` wicket and only when the engine's own digest of it
        equals the reference (``guards/checks/task_authority.py``).
        ``authorization_record`` is the whole sealed approval record
        ``action.authorized_by`` names, read only by a
        ``promise_requires_approval`` wicket and only when the engine's own
        digest of it equals that reference
        (``guards/checks/promise_requires_approval.py``).
        ``commercial_bounds_opening`` is the checker input's opening of the
        user's private floor, read only by a ``price_floor`` wicket and only
        when it opens the ``bounds_commitment`` the bound task-authority
        record seals (``guards/checks/price_floor.py``). It is never sealed."""
        table, mismatch, mismatch_reason = self._taxonomy_for(action)
        ac = table.classify(action.action_class)
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

        def held(constraint: ConstraintOutcome) -> ConstraintOutcome:
            if mismatch is None or constraint.id in _NEVER_HELD_CHECKS:
                return constraint
            return _taxonomy_held(constraint, mismatch, mismatch_reason)

        caps_out = self._check_caps(action, caps_limits)

        vbd_out = check_verify_before_dispatch(action, self._ledger)

        # With no caps definition, caps reads nothing keyed on the class, so a
        # taxonomy mismatch does not hold it: it stays out of scope.
        caps_constraint = held(caps_out.constraint) if self._caps_fold is not None else caps_out.constraint
        constraints = (dedupe_out.constraint, caps_constraint, vbd_out.constraint)
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
            elif wicket.check in COMMERCIAL_BOUNDS_CHECKS:
                out = COMMERCIAL_BOUNDS_CHECKS[wicket.check](
                    action, task_authority_record, commercial_bounds_opening, wicket.config
                )
            elif wicket.check in AUTHORIZATION_CHECKS:
                out = AUTHORIZATION_CHECKS[wicket.check](
                    action, task_authority_record, authorization_record, wicket.config
                )
            elif wicket.check == _GATE:
                out = check_action_class_gate(action, selectors=wicket.config["selectors"], table=table)
            else:
                out = CONFIGURED_CHECKS[wicket.check](action, self._ledger, wicket.config)
            constraint = held(out.constraint)
            constraints = (*constraints, constraint)
            if wicket.check == _GATE:
                gate_runs.append((wicket, constraint))
        # A held caps result is not reported, so neither is the fold it read.
        fold_envelopes = tuple(caps_out.fold_envelopes) if mismatch is None else ()
        escalatable = self._escalatable
        if self._gate_failures_ask(gate_runs):
            escalatable = escalatable | {_GATE}
        if dedupe_out.asks_approver:
            escalatable = escalatable | {"dedupe"}
        outcome = _decide(constraints, ac, escalatable)
        if mismatch is not None and outcome == ALLOW:
            # Its class was never evaluated: refused, never sealed as accepted.
            outcome = DENY
        conflict = DealIdConflict(fields=action.deal_id_conflict) if action.deal_id_conflict else None
        if conflict is not None and outcome == ALLOW:
            # Its deal is unknown, and neither stated value is picked.
            outcome = DENY

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
            reason=_summarize(constraints, outcome, ac, escalatable, mismatch_reason, conflict),
            taxonomy_mismatch=mismatch,
            deal_id_conflict=conflict,
        )

    def _taxonomy_for(self, action: Action) -> tuple[TaxonomyTable, TaxonomyMismatch | None, str | None]:
        """The table ``action``'s class is evaluated under and, when its
        class-keyed checks are not evaluated at all, the mismatch and why.
        An action naming no taxonomy version is read under the engine's."""
        version = action.taxonomy_version
        engine_version = ENGINE_TAXONOMY.version
        if version is None or version == engine_version:
            return ENGINE_TAXONOMY, None, None
        mismatch = TaxonomyMismatch(record_taxonomy_version=version, engine_taxonomy_version=engine_version)
        if not isinstance(version, str):
            # A version is a string; a sealed number names no table, even one
            # that reads like a version.
            return ENGINE_TAXONOMY, mismatch, (
                f"taxonomy_version {version!r} is not a version string; the engine's action taxonomy is "
                f"version {engine_version}: action_class is not evaluated against a different table"
            )
        if not self._evaluate_under_record_taxonomy:
            return ENGINE_TAXONOMY, mismatch, (
                f"taxonomy_version {version} on the action; the engine's action taxonomy is version "
                f"{engine_version}: action_class is not evaluated against a different table"
            )
        table = carried_taxonomy(version)
        if table is not None:
            return table, None, None
        return ENGINE_TAXONOMY, mismatch, (
            f"taxonomy_version {version} on the record; this engine carries action taxonomy versions "
            f"{', '.join(CARRIED_TAXONOMY_VERSIONS)} and not {version}: action_class is not evaluated "
            "against a different table"
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

    def _check_caps(self, action: Action, caps_limits: CapsLimits | None) -> CheckOutcome:
        """``caps`` for ``action``: ``n/a``, out of scope, when the pack cites
        no caps definition or no limit is configured for the action's class."""
        if self._caps_fold is None:
            return _caps_out_of_scope("no caps definition is configured", method=None)
        cap_minor, per_action_cap_minor, limit_sources = self._limits_for(action, caps_limits)
        if cap_minor is None:
            return _caps_out_of_scope("no cap configured for this action class", method=self._caps_fold.fold_id)
        return check_caps(
            action,
            self._ledger,
            definition=self._caps_fold,
            cap_minor=cap_minor,
            per_action_cap_minor=per_action_cap_minor,
            per_action_reads=self._per_action_reads,
            limit_sources=limit_sources,
        )

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


def _caps_out_of_scope(reason: str, *, method: str | None) -> CheckOutcome:
    return CheckOutcome(
        constraint=ConstraintOutcome(
            id="caps",
            result="n/a",
            reason=reason,
            evidence=not_applicable_evidence("caps", in_scope=False),
            check_type="policy",
            method=method,
        )
    )


# Failures that ask an approver rather than refuse: an over-limit spend and
# a first-time counterparty. These two ask whatever a pack declares for them.
_ESCALATABLE = frozenset({"caps", "counterparty_seen_before"})
_GATE = "action_class_gate"
# Integrity checks: a failure is refused whatever a pack declares (a dedupe hit
# on the same act in another deal asks through ``CheckOutcome.asks_approver``).
_INTEGRITY_CHECKS = frozenset(
    {"dedupe", "verify_before_dispatch", "single_commitment", "promise_never", "release_on_acceptance"}
)
# Checks whose own config decides whether a failure asks.
_OWN_ASK_RULE = frozenset({_GATE, "counterparty_list"})
# The checks a declared ASK disposition never makes ask (``ask_wickets``).
ASK_RULE_EXCLUDED_CHECKS = _INTEGRITY_CHECKS | _OWN_ASK_RULE
# The checks a taxonomy mismatch never holds: ``credential_pattern`` and
# ``plan_containment`` read no ``action_class``, and an integrity check
# refuses whatever the taxonomy says (``dedupe``, ``single_commitment`` and
# ``promise_never`` compare the class name as sealed, resolving it through no
# table). ``caps`` and every other configured check is keyed on the class, so a
# mismatch leaves it unevaluated (``_taxonomy_held``), including any check
# added later.
_NEVER_HELD_CHECKS = _INTEGRITY_CHECKS | {"credential_pattern", "plan_containment"}


def _taxonomy_held(constraint: ConstraintOutcome, mismatch: TaxonomyMismatch, reason: str | None) -> ConstraintOutcome:
    """``constraint`` not evaluated: ``n/a``, in scope, naming both versions."""
    evidence = TaxonomyHeldEvidence(
        constraint_id=constraint.id, in_scope=True, missing_field=None, taxonomy_mismatch=mismatch
    )
    return ConstraintOutcome(
        id=constraint.id,
        result="n/a",
        reason=reason,
        evidence=evidence,
        severity=constraint.severity,
        blocking=constraint.blocking,
        check_type=constraint.check_type,
        method=constraint.method,
    )


def _decide(
    constraints: tuple[ConstraintOutcome, ...],
    action_class: ActionClass,
    escalatable: frozenset[str] = _ESCALATABLE,
) -> str:
    """allow/deny/escalate per D2 (2026-08-05): a clean run allows. A hold
    escalates only when every failing constraint is in ``escalatable``
    (``_ESCALATABLE`` -- `caps`, `counterparty_seen_before` -- plus a
    `counterparty_list` configured to ask, plus an `action_class_gate`
    failure whose failing selectors' obligations all declare ASK, plus a
    configured check whose obligations all declare ASK, plus a
    `dedupe` hit on the same act in another deal) and the
    triggering class has an
    `approver_role` configured -- an integrity failure
    (`verify_before_dispatch`, whether the cited mandate is missing or fails
    re-verification; `single_commitment`; `promise_never`;
    `release_on_acceptance`), a dedupe hit in
    the same deal, or an escalatable failure on a class with
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
    not_evaluated: str | None = None,
    deal_id_conflict: DealIdConflict | None = None,
) -> str:
    parts = [f"{c.id}={c.result}" for c in constraints]
    summary = f"{outcome}: " + ", ".join(parts)
    fails = {c.id for c in constraints if c.result == "fail"}
    if not_evaluated is not None:
        summary += f"; not evaluable: {not_evaluated}"
        if not fails:
            summary += "; refused because its action class was not evaluated"
    if deal_id_conflict is not None:
        summary += (
            f"; not evaluable: the record states its deal in {' and '.join(deal_id_conflict['fields'])} "
            "with different values, so it is in no deal"
        )
        if not fails:
            summary += "; refused because its deal is not known"
    if outcome == DENY and fails and fails <= escalatable and action_class.approver_role is None:
        summary += (
            f"; every failure may ask an approver, but action class {action_class.name!r} "
            "names no approver_role, so it is refused"
        )
    return summary
