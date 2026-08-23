# SPDX-License-Identifier: Apache-2.0
"""``PlanDefinition``: the forward compile of a declared outcome into the set
of actions that serve it (design doc: ``plan-containment-demo-design-
2026-08-12.md`` §1 -- "one declaration ... compiled forward into a
containment check at act time; compiled backward into an attainment fold at
report time").

Today only the backward direction exists in this codebase (the hand-written
attainment folds in ``examples/conversation_outcome_demo.py`` and similar).
This module is the forward half's own artifact: ``{outcome_id,
allowed_actions, preconditions, binding, window}``, digest-pinned the same
way ``folds/definition.py``'s ``FoldDefinition`` and ``guards/wickets/
definition.py``'s ``WicketDefinition`` are -- SHA-256 over the JCS-canonical
bytes of ``canonical_dict()``, via the same ``agent_action_capsule.canonical.
json_digest`` every other digest-pinned definition in this codebase uses.

Hand-declared for the demo (``[ldg-plan-containment]`` C1) -- there is no
outcome-declaration compiler yet (``[ldg-outcome-declaration-schema]``,
Wave 2). This shape is kept schema-compatible on purpose: when a real
compiler exists, it emits exactly this shape, and every caller of
``parse_plan_definition``/``PlanDefinition`` is a straight substitution, not
a rewrite.

A wicket's ``config`` can hold this artifact's fields directly (a wicket is
``{wicket_id, check, config}`` -- ``guards/wickets/definition.py``), which is
how ``guards/checks/plan_containment.py`` gets a real ``PlanDefinition`` at
decision time: ``parse_plan_definition(wicket_definition.config)``. The
plan's own ``definition_digest()`` is computed over ``canonical_dict()``
alone (never wrapped in the wicket's own ``wicket_id``/``check``), so the
same plan digests identically whether it is quoted as a wicket's config or
handed to ``PlanDefinition`` directly by a caller that has no wicket at all.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from agent_action_capsule.canonical import FloatInDigestError, UnsafeIntegerError, json_digest

__all__ = [
    "OUTCOME_ID_RE",
    "ACTION_VERB_RE",
    "PlanDefinitionError",
    "PlanPrecondition",
    "PlanDefinition",
    "parse_plan_definition",
]

# Same "human name + semver" shape as fold_id/wicket_id (folds/definition.py,
# guards/wickets/definition.py) -- outcome ids are the same namespaced-id
# family, just a different registry.
OUTCOME_ID_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*/\d+\.\d+\.\d+$")

# An allowed-action entry is a bare verb (``Action.verb`` -- guards/action.py),
# never a namespaced/versioned id: the plan cites the SAME verb strings an
# ``Action`` carries, so ``action.verb in plan.allowed_actions`` is a direct
# string comparison, not a lookup through another id scheme.
ACTION_VERB_RE = re.compile(r"^[a-z][a-z0-9_]*$")

# -- reason codes (mirrors guards/wickets/errors.py's shape) ----------------
INVALID_OUTCOME_ID = "invalid_outcome_id"
INVALID_ACTION_VERB = "invalid_action_verb"
EMPTY_ALLOWED_ACTIONS = "empty_allowed_actions"
DUPLICATE_ALLOWED_ACTION = "duplicate_allowed_action"
INVALID_PRECONDITION = "invalid_precondition"
PRECONDITION_ACTION_NOT_ALLOWED = "precondition_action_not_allowed"
MALFORMED_DEFINITION = "malformed_definition"
FLOAT_IN_DEFINITION = "float_in_definition"
UNSAFE_INTEGER_IN_DEFINITION = "unsafe_integer_in_definition"


class PlanDefinitionError(ValueError):
    """A plan definition fails to parse, validate, or digest. Carries a
    stable reason code (mirrors ``WicketDefinitionError``)."""

    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        super().__init__(f"{reason}: {message}")


@dataclass(frozen=True)
class PlanPrecondition:
    """One gate on one allowed action: ``action`` MUST cite a prior capsule
    before it is contained (design doc: "enable_mfa REQUIRES a recorded
    agreement judgment citing this session"). ``citing`` is a human-readable
    label of what the citation is supposed to be (narrative + evidence detail
    only -- see ``guards/checks/plan_containment.py``'s module docstring for
    why the runtime check can only verify a citation's PRESENCE, never its
    content, without ceasing to be a pure function of ``(action, plan)``)."""

    action: str
    citing: str

    def canonical_dict(self) -> dict:
        return {"action": self.action, "citing": self.citing}


@dataclass(frozen=True)
class PlanDefinition:
    outcome_id: str
    allowed_actions: tuple[str, ...]
    preconditions: tuple[PlanPrecondition, ...] = field(default_factory=tuple)
    binding: dict[str, Any] = field(default_factory=dict)
    window: str | None = None

    def canonical_dict(self) -> dict:
        """The JCS-canonicalizable form of this plan -- drives
        ``definition_digest()``. ``window`` is omitted (never emitted as
        ``null``) when unset, same "absent, not null" convention every other
        digest-bearing structure in this codebase follows."""
        out: dict[str, Any] = {
            "outcome_id": self.outcome_id,
            "allowed_actions": list(self.allowed_actions),
            "preconditions": [p.canonical_dict() for p in self.preconditions],
            "binding": dict(self.binding),
        }
        if self.window is not None:
            out["window"] = self.window
        return out

    def definition_digest(self) -> str:
        """SHA-256 over the JCS bytes of ``canonical_dict()`` -- "the plan
        digest", cited by ``guards/checks/plan_containment.py``'s evidence
        object and re-derivable by any stranger holding the plan (design doc
        §1: "hand a stranger the plan and the capsule and they re-derive the
        verdict")."""
        try:
            return json_digest(self.canonical_dict())
        except FloatInDigestError as exc:
            raise PlanDefinitionError(FLOAT_IN_DEFINITION, str(exc)) from exc
        except UnsafeIntegerError as exc:
            raise PlanDefinitionError(UNSAFE_INTEGER_IN_DEFINITION, str(exc)) from exc

    def allowed_set_digest(self) -> str:
        """A narrower digest over the allowed-action set alone, so "was this
        verb in the allowed set at the digest cited here" is checkable
        without also depending on preconditions/binding/window content."""
        return json_digest({"allowed_actions": list(self.allowed_actions)})

    def admitted_action_space_size(self) -> int:
        """The over-breadth measure: the cardinality of ``allowed_actions``,
        sealed at digest-freeze (a pure function of this frozen dataclass's
        own field, computed once and disclosed on every decision -- never
        re-derived from a mutable source). Recorded so a vacuously broad
        plan is VISIBLE in the receipt rather than flattered by one that
        only ever shows contained/departed verdicts.

        **This measures breadth, not satisfiability.** There is no analyzer
        in this codebase, so a plan that admits every verb in the taxonomy
        (trivially permissive -- containment would rubber-stamp almost
        anything) and a plan whose preconditions can never jointly be
        satisfied (unsatisfiable -- containment would refuse almost
        everything) are NOT distinguished or flagged by this number alone;
        both are just a count. Recording that limitation here, honestly,
        rather than letting the field's name imply a soundness check that
        does not exist."""
        return len(self.allowed_actions)

    def step_index(self, verb: str) -> int | None:
        """The 0-based position of ``verb`` in ``allowed_actions``, or
        ``None`` if it is not a contained verb at all."""
        try:
            return self.allowed_actions.index(verb)
        except ValueError:
            return None

    def precondition_for(self, verb: str) -> PlanPrecondition | None:
        for precondition in self.preconditions:
            if precondition.action == verb:
                return precondition
        return None


def parse_plan_definition(data: Any) -> PlanDefinition:
    """Validate a plain dict (as loaded from YAML, or quoted verbatim as a
    wicket's ``config``) into a ``PlanDefinition``."""
    if not isinstance(data, dict):
        raise PlanDefinitionError(MALFORMED_DEFINITION, "plan definition must be a mapping")

    outcome_id = data.get("outcome_id")
    if not isinstance(outcome_id, str) or not OUTCOME_ID_RE.match(outcome_id):
        raise PlanDefinitionError(
            INVALID_OUTCOME_ID,
            f"outcome_id {outcome_id!r} must match '<namespace>[.<namespace>...]/<major>.<minor>.<patch>' "
            "(e.g. 'workforce.remediation_completed/1.0.0')",
        )

    raw_actions = data.get("allowed_actions")
    if not isinstance(raw_actions, list) or not raw_actions:
        raise PlanDefinitionError(EMPTY_ALLOWED_ACTIONS, "allowed_actions must be a non-empty list of verbs")
    seen: set[str] = set()
    allowed_actions: list[str] = []
    for verb in raw_actions:
        if not isinstance(verb, str) or not ACTION_VERB_RE.match(verb):
            raise PlanDefinitionError(
                INVALID_ACTION_VERB, f"allowed_actions entry {verb!r} must be a lowercase_snake_case verb"
            )
        if verb in seen:
            raise PlanDefinitionError(DUPLICATE_ALLOWED_ACTION, f"allowed_actions lists {verb!r} more than once")
        seen.add(verb)
        allowed_actions.append(verb)

    raw_preconditions = data.get("preconditions") if "preconditions" in data else []
    if not isinstance(raw_preconditions, list):
        raise PlanDefinitionError(MALFORMED_DEFINITION, "preconditions must be a list")
    preconditions: list[PlanPrecondition] = []
    for entry in raw_preconditions:
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("action"), str)
            or not isinstance(entry.get("citing"), str)
        ):
            raise PlanDefinitionError(
                INVALID_PRECONDITION, f"precondition {entry!r} must be a mapping with string 'action' and 'citing'"
            )
        if entry["action"] not in seen:
            raise PlanDefinitionError(
                PRECONDITION_ACTION_NOT_ALLOWED,
                f"precondition gates {entry['action']!r}, which is not in allowed_actions {allowed_actions}",
            )
        preconditions.append(PlanPrecondition(action=entry["action"], citing=entry["citing"]))

    binding = data.get("binding") if "binding" in data else {}
    if not isinstance(binding, dict):
        raise PlanDefinitionError(MALFORMED_DEFINITION, "binding must be a mapping")

    window = data.get("window")
    if window is not None and not isinstance(window, str):
        raise PlanDefinitionError(MALFORMED_DEFINITION, "window must be a string or omitted")

    return PlanDefinition(
        outcome_id=outcome_id,
        allowed_actions=tuple(allowed_actions),
        preconditions=tuple(preconditions),
        binding=dict(binding),
        window=window,
    )
