# SPDX-License-Identifier: Apache-2.0
"""``Action``: the guard API's own input contract for ``GuardEngine.check()``.

Not a Capsule. An Action is the thing a caller wants to do, *before* any
decision has been made about it. ``GuardEngine.check()`` evaluates one and
produces a decision, which is what becomes a Capsule (``guards/capsule.py``).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone


def _new_action_id(verb: str) -> str:
    return f"{verb}/{uuid.uuid4()}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class Action:
    """One candidate action, as presented to the guard for a decision.

    ``action_class`` is looked up in the starter taxonomy (``classes.py``);
    absent or unrecognized both resolve to the consequential/fail-closed
    default. ``amount_minor``/``currency`` are integer-minor-units money
    fields (never floats, per the fold engine's own determinism rule) read
    by the ``caps`` check. ``target`` is an optional dedupe discriminator
    (e.g. a counterparty or recipient reference). ``cited_mandate_capsule_id``
    is the prior capsule this action claims authorization from, checked by
    ``verify_before_dispatch``. ``equivalence_key`` lets a caller override the
    dedupe check's default equivalence formula for this action.
    """

    verb: str
    operator: str
    developer: str
    action_class: str | None = None
    action_id: str | None = None
    action_type: str = "decide"
    timestamp: str | None = None
    amount_minor: int | None = None
    currency: str | None = None
    target: str | None = None
    cited_mandate_capsule_id: str | None = None
    equivalence_key: str | None = None
    model_id: str | None = None
    provider: str | None = None
    extra: dict = field(default_factory=dict)

    def resolved_action_id(self) -> str:
        return self.action_id or _new_action_id(self.verb)

    def resolved_timestamp(self) -> str:
        return self.timestamp or _utc_now()

    @classmethod
    def from_capsule(
        cls,
        capsule: dict,
        *,
        action_class: str | None = None,
        cited_mandate_capsule_id: str | None = None,
    ) -> Action:
        """Build an ``Action`` from a capsule already sitting in a ledger.

        Used to replay a historical or foreign capsule back through the
        guard for a dry-run or end-to-end reproduction. A truly foreign
        capsule (one this guard did not itself produce) carries none of
        ``asg_payload``'s extension fields, so its ``action_class`` must be
        supplied by the caller -- there is nothing in the -02 schema to
        infer it from. But a capsule THIS guard (or a pack installed
        through it) originally produced carries its own ``action_class`` in
        ``asg_payload`` (``build_decision_capsule``), so replaying one of
        those back (the normal dry-run-report path) reads it from the
        capsule itself when the caller doesn't override it -- an explicit
        ``action_class`` argument always wins, for the genuinely-foreign
        case this was written for.
        """
        action_id = capsule.get("action_id", "")
        verb = action_id.split("/", 1)[0] if action_id else "unknown"
        payload = capsule.get("asg_payload") or {}
        return cls(
            verb=verb,
            operator=capsule.get("operator", ""),
            developer=capsule.get("developer", ""),
            action_class=action_class or payload.get("action_class"),
            action_id=action_id or None,
            action_type=capsule.get("action_type", "decide"),
            timestamp=capsule.get("timestamp"),
            amount_minor=payload.get("amount_minor"),
            currency=payload.get("currency"),
            target=payload.get("target"),
            cited_mandate_capsule_id=cited_mandate_capsule_id,
        )
