# SPDX-License-Identifier: Apache-2.0
"""``HoldEngine``: atomic evaluate-and-reserve (#51), expiry-terminal +
resume (#52), and planned-vs-executed reconciliation (#53).

Local (in-process, one ledger) single-writer semantics only -- a distributed
sequencer across processes/nodes is capsule-emit's Dapr-side job, not this
module's. This engine reuses ``GuardEngine``'s own reference checks
(``check_dedupe``/``check_caps``/``check_verify_before_dispatch``) and its D2
allow/deny/escalate routing rule (``guards/engine.py``'s ``_decide``) rather
than reimplementing them -- "verdict vocabulary stays consistent with the
existing set" (the cross-cutting requirement shared by #51/#52/#53) is
enforced by literally calling the same function, not by parallel logic that
could drift from it.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

from capsule_ledger.ledger.api import LedgerAPI

from ..folds.definition import FoldDefinition
from ..guards.action import Action
from ..guards.capsule import ALLOW, DENY, ESCALATE, ConstraintOutcome, build_decision_capsule
from ..guards.checks import CheckOutcome, check_caps, check_dedupe, check_verify_before_dispatch
from ..guards.classes import classify
from ..guards.engine import _decide
from ..guards.signing import Signer, SigningKeyUnavailable
from .capsules import (
    SUPERSEDES,
    build_hold_expire_capsule,
    build_hold_reconcile_capsule,
    build_hold_release_capsule,
    build_hold_reserve_capsule,
    check_integer_amount,
)
from .errors import (
    HOLD_ALREADY_TERMINAL,
    HOLD_NOT_FOUND,
    HOLD_STATUS_AMBIGUOUS,
    OVER_TOLERANCE,
    RECONCILE_AFTER_EXPIRY,
    SEQUENCER_UNAVAILABLE,
)
from .scope import ScopeLocks

__all__ = ["HoldStatus", "HoldDecision", "HoldEngine"]

# Verbs a chained `supersedes` record over a reserve capsule may carry, and
# the terminal status each one closes the hold into.
_TERMINAL_VERBS = {"hold.release": "released", "hold.expire": "expired", "hold.reconcile": "reconciled"}


def _verb(capsule: dict) -> str:
    action_id = capsule.get("action_id") or ""
    return action_id.split("/", 1)[0] if action_id else ""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class HoldStatus(str, Enum):
    ACTIVE = "active"
    RELEASED = "released"
    EXPIRED = "expired"
    RECONCILED = "reconciled"
    # #52.4: a hold's expiry status could not be determined (missing record,
    # a chained terminal record that fails independent verification). Every
    # caller in this module treats AMBIGUOUS as terminal/deny for
    # consequential classes -- never as "probably still active".
    AMBIGUOUS = "ambiguous"


_STATUS_BY_VERB = {v: HoldStatus(s) for v, s in _TERMINAL_VERBS.items()}


@dataclass(frozen=True)
class HoldDecision:
    """A hold-lifecycle operation's result -- same allow/deny/escalate
    vocabulary as ``GuardDecision`` (``guards/engine.py``), extended with the
    hold's own status where relevant.

    ``reason_code`` is the machine-readable counterpart to ``reason``
    (``holds/errors.py``'s named-reason constants) -- it lives here, on the
    Python-side decision object, rather than on the sealed capsule: like
    every other reason this codebase produces (``guards/capsule.py``'s
    ``ConstraintOutcome.evidence``), the capsule only carries a
    ``disposition.reason_digest`` digest of the reason object, never the
    reason in the clear."""

    outcome: str  # allow | deny | escalate
    capsule: dict | None
    reason: str
    reason_code: str | None = None
    hold_status: HoldStatus | None = None
    fold_envelope: dict | None = None


class HoldEngine:
    def __init__(
        self,
        *,
        ledger: LedgerAPI,
        hold_fold: FoldDefinition,
        fold_digest: str,
        signer_provider: Callable[[], Signer | None],
        cap_minor: dict[str, int] | None = None,
        tolerance_minor: dict[str, int] | None = None,
        freshness_bound_ms: int = 5_000,
        engine_available: Callable[[], bool] = lambda: True,
        view_healthy: Callable[[], bool] = lambda: True,
        checkpoint_age_ms: Callable[[], int] = lambda: 0,
        manifest_digest: str | None = None,
        scope_locks: ScopeLocks | None = None,
    ) -> None:
        self._ledger = ledger
        self._hold_fold = hold_fold
        self._fold_digest = fold_digest
        self._signer_provider = signer_provider
        self._cap_minor = cap_minor or {}
        self._tolerance_minor = tolerance_minor or {}
        self._freshness_bound_ms = freshness_bound_ms
        self._engine_available = engine_available
        self._view_healthy = view_healthy
        self._checkpoint_age_ms = checkpoint_age_ms
        self._manifest_digest = manifest_digest
        self._scope_locks = scope_locks or ScopeLocks()

    def _get_signer(self) -> Signer | None:
        try:
            return self._signer_provider()
        except SigningKeyUnavailable:
            return None

    def _tree_size(self) -> int:
        return sum(1 for _ in self._ledger.scan())

    def _checkpoint(self) -> dict:
        return {"tree_size": self._tree_size(), "age_ms": self._checkpoint_age_ms(), "anchor_status": "unanchored"}

    # -- #51: atomic evaluate-and-reserve --------------------------------

    def evaluate_and_reserve(self, action: Action) -> HoldDecision:
        """Fail-closed preflight mirrors ``GuardEngine.check()``'s own
        failure-semantics table (view health, signing key, staleness, engine/
        sequencer availability) -- #51.4: "a gate that cannot reach the
        scope's serialization point must deny/escalate, never evaluate
        against an unserialized read." The reservation decision itself runs
        under this scope's lock (``scope.py``): the read (fold evaluation),
        the decide, and the append are one atomic critical section, so N
        concurrent calls for the same scope can never jointly over-reserve
        it (#51.2)."""
        age_ms = self._checkpoint_age_ms()

        if not self._view_healthy():
            return HoldDecision(
                outcome=DENY, capsule=None, reason_code=SEQUENCER_UNAVAILABLE,
                reason="local view unavailable/corrupt; fail closed, no reserve",
            )

        signer = self._get_signer()
        if signer is None:
            return HoldDecision(
                outcome=DENY, capsule=None, reason_code=SEQUENCER_UNAVAILABLE,
                reason="signing key unavailable; fail closed, an unsigned record is not a record",
            )

        if age_ms > self._freshness_bound_ms:
            return self._sequencer_unavailable_deny(
                action, signer,
                reason=f"view is stale ({age_ms}ms) beyond the freshness bound ({self._freshness_bound_ms}ms); "
                "fail closed, no reserve",
            )

        if not self._engine_available():
            return self._sequencer_unavailable_deny(
                action, signer,
                reason="fold engine / scope serialization point is unreachable; fail closed (no reserve -> no pass)",
            )

        scope = (self._fold_digest, action.developer)
        with self._scope_locks.get(scope):
            return self._evaluate_and_reserve_locked(action, signer=signer, age_ms=age_ms)

    def _sequencer_unavailable_deny(self, action: Action, signer: Signer, *, reason: str) -> HoldDecision:
        """#51.4's recordable fail-closed case (staleness / engine-
        unreachable): unlike the view/signer rows above, this IS recordable
        -- a plain DENY decision capsule, never a hold record (nothing was
        ever reserved)."""
        constraint = ConstraintOutcome(id="sequencer_availability", result="fail", reason=reason, check_type="infra")
        capsule = build_decision_capsule(
            action=action, outcome=DENY, constraints=(constraint,), signer=signer, checkpoint=self._checkpoint(),
            reason={"outcome": DENY, "constraints": [{"id": "sequencer_availability", "result": "fail"}]},
            manifest_digest=self._manifest_digest,
        )
        self._ledger.append(capsule, consequential=True)
        return HoldDecision(outcome=DENY, capsule=capsule, reason=reason, reason_code=SEQUENCER_UNAVAILABLE)

    def _evaluate_and_reserve_locked(self, action: Action, *, signer: Signer, age_ms: int) -> HoldDecision:
        ac = classify(action.action_class)
        dedupe_out = check_dedupe(action, self._ledger)
        cap_minor = self._cap_minor.get(action.action_class)
        if cap_minor is not None:
            caps_out = check_caps(
                action, self._ledger, definition=self._hold_fold, cap_minor=cap_minor,
                as_of=action.resolved_timestamp(),
            )
        else:
            caps_out = CheckOutcome(
                constraint=ConstraintOutcome(
                    id="caps", result="n/a", reason="no cap configured for this action class",
                    check_type="policy", method=self._hold_fold.fold_id,
                )
            )
        vbd_out = check_verify_before_dispatch(action, self._ledger)

        constraints = (dedupe_out.constraint, caps_out.constraint, vbd_out.constraint)
        outcome = _decide(constraints, ac)
        checkpoint = {"tree_size": self._tree_size(), "age_ms": age_ms, "anchor_status": "unanchored"}

        if outcome == ALLOW:
            reserved_amount = action.amount_minor if action.amount_minor is not None else 0
            envelope = caps_out.fold_envelopes[0] if caps_out.fold_envelopes else {}
            capsule = build_hold_reserve_capsule(
                action=action, reserved_amount_minor=reserved_amount, fold_id=self._hold_fold.fold_id,
                fold_digest=self._fold_digest, fold_envelope=envelope, checkpoint=checkpoint, signer=signer,
                manifest_digest=self._manifest_digest,
            )
            self._ledger.append(capsule, consequential=True)
            return HoldDecision(
                outcome=ALLOW, capsule=capsule, reason="reserved", hold_status=HoldStatus.ACTIVE, fold_envelope=envelope,
            )

        resolved_parent, resolved_relation = None, None
        for out in (vbd_out, dedupe_out):
            if out.chain_parent is not None:
                resolved_parent, resolved_relation = out.chain_parent, out.chain_relation
                break
        capsule = build_decision_capsule(
            action=action, outcome=outcome, constraints=constraints, signer=signer, checkpoint=checkpoint,
            reason={"outcome": outcome, "constraints": [{"id": c.id, "result": c.result} for c in constraints]},
            chain_parent=resolved_parent, chain_relation=resolved_relation, manifest_digest=self._manifest_digest,
        )
        self._ledger.append(capsule, consequential=True)
        return HoldDecision(outcome=outcome, capsule=capsule, reason=f"not reserved: {outcome}")

    # -- hold status (earliest chained terminal record wins) -------------

    def hold_status(self, reserve_capsule_id: str) -> tuple[HoldStatus, dict | None]:
        """(status, terminal_capsule_or_None). AMBIGUOUS (#52.4) if the
        reserve record itself is missing/fails verification, or a chained
        terminal record fails independent verification (clock skew,
        corruption) -- never reported as ACTIVE in that case. Mirrors
        ``agent_action_capsule.verify_store``'s own concurrent-supersedes
        precedent: the earliest chained ``supersedes`` record in ledger
        order is authoritative."""
        reserve_record = self._ledger.fetch(reserve_capsule_id)
        if reserve_record is None:
            return HoldStatus.AMBIGUOUS, None
        reserve_result = self._ledger.verify(reserve_capsule_id)
        if reserve_result is None or not reserve_result.ok:
            return HoldStatus.AMBIGUOUS, None

        for record in self._ledger.scan():
            chain = record.capsule.get("chain") or {}
            if chain.get("parent_capsule_id") != reserve_capsule_id or chain.get("relation") != SUPERSEDES:
                continue
            verb = _verb(record.capsule)
            if verb not in _TERMINAL_VERBS:
                continue
            result = self._ledger.verify(record.capsule_id)
            if result is None or not result.ok:
                return HoldStatus.AMBIGUOUS, record.capsule
            return _STATUS_BY_VERB[verb], record.capsule  # earliest wins (scan is ledger order)

        return HoldStatus.ACTIVE, None

    # -- release / expire --------------------------------------------------

    def release(self, reserve_capsule_id: str, *, reason: str | None = None) -> HoldDecision:
        return self._close(reserve_capsule_id, verb="release", reason=reason)

    def expire(self, reserve_capsule_id: str, *, reason: str | None = None) -> HoldDecision:
        """#52.1: TERMINAL for this hold -- after this call succeeds, nothing
        may dispatch citing the original reservation. Enforced by
        ``hold_status``/``_deny_terminal`` on every later release/expire/
        reconcile attempt against the same reserve capsule."""
        return self._close(reserve_capsule_id, verb="expire", reason=reason)

    def _close(self, reserve_capsule_id: str, *, verb: str, reason: str | None) -> HoldDecision:
        reserve_record = self._ledger.fetch(reserve_capsule_id)
        if reserve_record is None:
            return HoldDecision(
                outcome=DENY, capsule=None, reason_code=HOLD_NOT_FOUND,
                reason=f"{HOLD_NOT_FOUND}: hold {reserve_capsule_id[:16]}… not found", hold_status=None,
            )
        subject = reserve_record.capsule.get("developer", "")
        with self._scope_locks.get((self._fold_digest, subject)):
            status, terminal_capsule = self.hold_status(reserve_capsule_id)
            signer = self._get_signer()
            if status != HoldStatus.ACTIVE:
                return self._deny_terminal(reserve_record, status, terminal_capsule, signer, verb=verb)
            if signer is None:
                return HoldDecision(
                    outcome=DENY, capsule=None,
                    reason="signing key unavailable; fail closed, an unsigned record is not a record",
                    hold_status=status,
                )

            payload = reserve_record.capsule.get("asg_payload") or {}
            reserved_amount = payload.get("reserved_amount_minor", 0)
            attempt_action = _attempt_action(reserve_record.capsule, verb=verb)
            builder = build_hold_release_capsule if verb == "release" else build_hold_expire_capsule
            capsule = builder(
                action=attempt_action, reserve_capsule_id=reserve_capsule_id,
                reserved_amount_minor=reserved_amount, signer=signer, reason=reason,
            )
            self._ledger.append(capsule, consequential=True)
            new_status = HoldStatus.RELEASED if verb == "release" else HoldStatus.EXPIRED
            return HoldDecision(outcome=ALLOW, capsule=capsule, reason=f"hold {verb}d", hold_status=new_status)

    # -- reconcile (#53) ------------------------------------------------

    def reconcile(
        self,
        reserve_capsule_id: str,
        *,
        action_class: str | None,
        executed_amount_minor: int,
        execution_capsule_id: str | None = None,
    ) -> HoldDecision:
        """Planned vs. executed (#53.1): in-tolerance conversions append a
        ``hold.reconcile`` capsule (delta-algebra makes the
        ``hold.active_exposure`` fold read ``executed_amount_minor`` for this
        hold once this lands). Over-tolerance conversions NEVER build that
        record -- they route through the existing ALLOW/DENY/ESCALATE
        vocabulary as a limit event instead (#53.3), same D2 escalate-only-
        with-an-approver-role rule ``_decide`` already implements for
        ``caps``, applied here to the ``tolerance`` constraint."""
        check_integer_amount(executed_amount_minor, "executed_amount_minor")
        reserve_record = self._ledger.fetch(reserve_capsule_id)
        if reserve_record is None:
            return HoldDecision(
                outcome=DENY, capsule=None, reason_code=HOLD_NOT_FOUND,
                reason=f"{HOLD_NOT_FOUND}: hold {reserve_capsule_id[:16]}… not found", hold_status=None,
            )
        subject = reserve_record.capsule.get("developer", "")
        with self._scope_locks.get((self._fold_digest, subject)):
            status, terminal_capsule = self.hold_status(reserve_capsule_id)
            signer = self._get_signer()
            if status != HoldStatus.ACTIVE:
                return self._deny_terminal(reserve_record, status, terminal_capsule, signer, verb="reconcile")
            if signer is None:
                return HoldDecision(
                    outcome=DENY, capsule=None,
                    reason="signing key unavailable; fail closed, an unsigned record is not a record",
                    hold_status=status,
                )

            payload = reserve_record.capsule.get("asg_payload") or {}
            reserved_amount = payload.get("reserved_amount_minor", 0)
            delta = executed_amount_minor - reserved_amount
            tolerance = self._tolerance_minor.get(action_class, 0)

            attempt_action = _attempt_action(reserve_record.capsule, verb="reconcile", action_class=action_class)

            if delta <= tolerance:
                capsule = build_hold_reconcile_capsule(
                    action=attempt_action, reserve_capsule_id=reserve_capsule_id,
                    execution_capsule_id=execution_capsule_id, reserved_amount_minor=reserved_amount,
                    executed_amount_minor=executed_amount_minor, tolerance_minor=tolerance, signer=signer,
                    manifest_digest=self._manifest_digest,
                )
                self._ledger.append(capsule, consequential=True)
                return HoldDecision(outcome=ALLOW, capsule=capsule, reason="reconciled", hold_status=HoldStatus.RECONCILED)

            ac = classify(action_class)
            outcome = ESCALATE if ac.approver_role is not None else DENY
            constraint = ConstraintOutcome(
                id="tolerance", result="fail",
                reason=f"executed {executed_amount_minor} exceeds reserved {reserved_amount} by {delta} "
                f"(minor units), beyond tolerance {tolerance}",
                evidence={
                    "reserved_amount_minor": reserved_amount,
                    "executed_amount_minor": executed_amount_minor,
                    "delta_minor": delta,
                    "tolerance_minor": tolerance,
                    "reason_code": OVER_TOLERANCE,
                },
                check_type="policy",
            )
            capsule = build_decision_capsule(
                action=attempt_action, outcome=outcome, constraints=(constraint,), signer=signer,
                checkpoint=self._checkpoint(),
                reason={"outcome": outcome, "constraints": [{"id": "tolerance", "result": "fail"}], "reason_code": OVER_TOLERANCE},
                chain_parent=reserve_capsule_id, chain_relation="confirms", manifest_digest=self._manifest_digest,
            )
            self._ledger.append(capsule, consequential=True)
            return HoldDecision(
                outcome=outcome, capsule=capsule,
                reason="over-tolerance conversion; routed as a limit event, aggregate not silently adjusted",
                reason_code=OVER_TOLERANCE, hold_status=HoldStatus.ACTIVE,
            )

    def _deny_terminal(
        self, reserve_record, status: HoldStatus, terminal_capsule: dict | None, signer: Signer | None, *, verb: str,
    ) -> HoldDecision:
        if status == HoldStatus.AMBIGUOUS:
            reason_code = HOLD_STATUS_AMBIGUOUS
            reason_text = (
                f"hold {reserve_record.capsule_id[:16]}… status could not be determined "
                "(missing or unverifiable record); failing closed as terminal for this consequential class"
            )
        elif status == HoldStatus.EXPIRED and verb == "reconcile":
            reason_code = RECONCILE_AFTER_EXPIRY
            reason_text = (
                f"hold {reserve_record.capsule_id[:16]}… already expired; a late approval is authentication, "
                "not authorization -- resume requires a fresh evaluate_and_reserve (HoldEngine."
                "evaluate_and_reserve), not resumption/reconciliation of the expired hold"
            )
        else:
            reason_code = HOLD_ALREADY_TERMINAL
            reason_text = f"hold {reserve_record.capsule_id[:16]}… is already {status.value}; cannot {verb} it again"

        if signer is None:
            return HoldDecision(outcome=DENY, capsule=None, reason=reason_text, reason_code=reason_code, hold_status=status)

        attempt_action = _attempt_action(reserve_record.capsule, verb=verb)
        constraint = ConstraintOutcome(
            id="hold_status", result="fail", reason=reason_text,
            evidence={"reason_code": reason_code, "hold_status": status.value, "reserve_capsule_id": reserve_record.capsule_id},
            check_type="policy",
        )
        chain_parent = (terminal_capsule or {}).get("capsule_id") or reserve_record.capsule_id
        capsule = build_decision_capsule(
            action=attempt_action, outcome=DENY, constraints=(constraint,), signer=signer, checkpoint=self._checkpoint(),
            reason={"outcome": DENY, "constraints": [{"id": "hold_status", "result": "fail"}], "reason_code": reason_code},
            chain_parent=chain_parent, chain_relation="confirms", manifest_digest=self._manifest_digest,
        )
        self._ledger.append(capsule, consequential=True)
        return HoldDecision(outcome=DENY, capsule=capsule, reason=reason_text, reason_code=reason_code, hold_status=status)


def _attempt_action(reserve_capsule: dict, *, verb: str, action_class: str | None = None) -> Action:
    """A minimal ``Action`` representing an attempt (release/expire/reconcile/
    a denied-terminal retry) against an existing hold -- carries the reserve
    capsule's own operator/developer/currency/target context, not the
    original reservation's verb (record-type identity for *this* attempt is
    ``verb``, resolved into ``action_id`` by the caller)."""
    payload = reserve_capsule.get("asg_payload") or {}
    return Action(
        verb=verb if verb.startswith("hold.") else f"hold.{verb}",
        operator=reserve_capsule.get("operator", ""),
        developer=reserve_capsule.get("developer", ""),
        action_class=action_class,
        currency=payload.get("currency"),
        target=payload.get("target"),
    )
