# SPDX-License-Identifier: Apache-2.0
"""Pack definitions: the parsed, validated shape of a ``pack.yaml`` plus its
referenced wicket/fold files.

Anatomy (starter-packs plan): obligations -> action semantics -> constraints
(wicket entries) -> folds -> fixtures, one directory per pack, versioned.
This module intentionally does NOT reinvent constraint or fold validation --
``constraints`` are parsed with ``guards.wickets.definition.parse_definition``
and ``folds`` with ``folds.definition.parse_definition``, the exact same
functions the core catalogs use, so a malformed constraint or fold gets the
identical, already-hardened error a hand-written wicket/fold file would.
This module's own errors (``errors.py``) cover only the pack-level shape:
which fields a pack.yaml itself needs, and the obligation/action-semantics
declarations that have no core-repo equivalent yet.

``PackDefinition.definition_digest()`` follows the same "definitions-by-
digest, never definitions-by-copy" rule every other digest in this repo
follows (``policy/manifest.py``'s module docstring): constraints and folds
are cited by their own ``definition_digest()``, never copied into the pack's
canonical form.

**Declared constraint scope** (``constraint_scopes``, generalizing a real
finding): ``capsule-emit`` PR #54 found a cross-class TOCTOU in the holds
engine -- the lock was scoped per ``(developer, action_class)``, the cap was
declared per ``action_class``, and the aggregate query summed
developer-wide across ALL classes with no class filter. Two concurrent
reserves under different classes for the same developer each took a
different lock, both read the same (stale, pre-write) dev-wide aggregate,
and jointly admitted what sequential execution would have denied. Steven's
ruling: a cap is per ``(developer, action_class)`` -- lock, cap, and
aggregate must all agree at that granularity. This repo's own ``caps``
check has the identical shape of risk even without a separate lock: the
wicket's ``caps_minor`` declares a limit PER ACTION CLASS, while the fold
it cites aggregates however its own ``key``/``filter`` say to -- if a pack
ever configures caps for more than one action class, the fold must
genuinely partition by class or the same "declared per-class, enforced
pooled" gap opens. ``loader.py``'s scope validator checks this at
pack-load time, not at incident time.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_action_capsule.canonical import FloatInDigestError, UnsafeIntegerError, json_digest

from ..folds.definition import FoldDefinition
from ..guards.wickets.definition import WicketDefinition
from .errors import FLOAT_IN_PACK_DIGEST, UNSAFE_INTEGER_IN_PACK_DIGEST, PackDefinitionError

__all__ = [
    "PACK_ID_RE",
    "NORMALIZED_ACTION_FIELDS",
    "HOLDS_INTEGRATION_VALUES",
    "KNOWN_SCOPE_DIMENSIONS",
    "Obligation",
    "ActionSemantic",
    "ProposerStub",
    "FixtureScenario",
    "PackFixtures",
    "PackDefinition",
]

# pack_id: publisher/name/semver (registry-architecture ruling, 2026-08-10:
# pack_ids participate in the policy-manifest digest, so the format has to be
# right before the first release -- a flat namespace invites collisions once
# community registration opens, and changing the format later breaks "what
# was in force" continuity). Publisher segment matches the same namespace
# grammar fold_id/wicket_id/manifest_id use (dot/underscore, no hyphens --
# it's a registry-reserved token like "asg", not a display name); the name
# segment stays kebab-case, matching how packs are named in the wild
# (payments-safety, external-comms, ...).
PACK_ID_RE = re.compile(r"^[a-z][a-z0-9_]*/[a-z][a-z0-9_]*(-[a-z0-9_]+)*/\d+\.\d+\.\d+$")

# The normalized ``guards.action.Action`` fields a pack's action semantics may
# declare as required/optional -- the "canonical field basis per action
# family" the architecture rule requires packs to bind to instead of any
# framework object. Closed set, same reasoning as ``KNOWN_CHECKS``/
# ``KNOWN_REDUCERS``: an unrecognized field name is a typo (or a field the
# normalization contract doesn't have yet, which is a contract change, not a
# pack change) -- never silently accepted as data. The five identity/
# classification fields every ``Action`` always carries (``verb``,
# ``operator``, ``developer``, ``action_class``, ``action_type``) are not
# listed here -- they are not something a pack "requires", they are what an
# action semantic entry itself declares.
NORMALIZED_ACTION_FIELDS = frozenset(
    {"amount_minor", "currency", "target", "cited_mandate_capsule_id", "equivalence_key", "model_id", "provider"}
)

HOLDS_INTEGRATION_VALUES = frozenset({"none", "stubbed", "built"})

# The dimensions a constraint's declared scope may name -- closed set, same
# "unregistered is a typo" reasoning as everywhere else in this file. These
# are the fields a numeric-aggregate check (today: caps) can genuinely be
# partitioned by, given the normalized Action/capsule fields this repo has.
KNOWN_SCOPE_DIMENSIONS = frozenset({"developer", "operator", "action_class", "target"})


@dataclass(frozen=True)
class Obligation:
    """The human-readable contract this pack encodes, mapped 1:1 to a check
    (every obligation maps 1:1 to the check that enforces it)."""

    id: str
    statement: str
    check: str


@dataclass(frozen=True)
class ActionSemantic:
    """One action type this pack governs and its required normalized fields.

    ``action_type`` here is a documentation-level convention name (registry-
    architecture ruling §2: "bare dotted names ... conventions, not owned
    artifacts", the OTel-semconv analogy) -- it is how this pack's own
    obligations/config reference this action family. It is NEVER written
    into a real capsule's ``action_type`` field, which is a base-spec field
    with its own closed vocabulary (``{"fyi", "decide"}``, §5.1) that a
    pack-specific name would violate -- ``loader.py`` refuses any pack that
    tries to name one of those two reserved values here, precisely so this
    distinction can't be missed. The normalized ``Action.verb`` (free text,
    no spec constraint) is where a pack-specific business-action label
    naturally lives on a real call.
    """

    action_type: str
    action_class: str
    required_fields: tuple[str, ...] = ()
    optional_fields: tuple[str, ...] = ()
    # Pack-facing display name for a normalized field, e.g. {"target":
    # "counterparty_ref"} -- documentation only, never a second field name:
    # the wire/engine contract is always the normalized field on the left.
    field_aliases: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ProposerStub:
    """A threshold proposer this pack declares. P1 only parses and digests
    this -- ``capsule thresholds propose`` (P2) is what actually runs it."""

    id: str
    fold_id: str
    strategy: str
    status: str = "planned"


@dataclass(frozen=True)
class FixtureScenario:
    id: str
    outcome: str  # allow | deny | escalate


@dataclass(frozen=True)
class PackFixtures:
    ledger: str | None
    scenarios: tuple[FixtureScenario, ...] = ()


@dataclass(frozen=True)
class PackDefinition:
    pack_id: str
    obligations: tuple[Obligation, ...]
    action_semantics: tuple[ActionSemantic, ...]
    constraints: tuple[WicketDefinition, ...]
    folds: tuple[FoldDefinition, ...]
    proposers: tuple[ProposerStub, ...] = ()
    holds_integration: str = "none"
    fixtures: PackFixtures | None = None
    bootstrap_path: str | None = None
    source_dir: Path | None = None
    # wicket_id -> declared scope dimensions, e.g. {"payments_safety.caps/1.0.0":
    # ("developer",)}. Required for every `caps` constraint (loader.py enforces
    # this); optional documentation for other check types.
    constraint_scopes: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def canonical_dict(self) -> dict:
        """The JCS-canonicalizable form of this pack -- drives
        ``definition_digest()``. Constraints/folds are cited by their own
        digest, never copied in whole -- see the module docstring."""
        out: dict[str, Any] = {
            "pack_id": self.pack_id,
            "obligations": [{"id": o.id, "statement": o.statement, "check": o.check} for o in self.obligations],
            "action_semantics": [
                {
                    "action_type": a.action_type,
                    "action_class": a.action_class,
                    "required_fields": list(a.required_fields),
                    **({"optional_fields": list(a.optional_fields)} if a.optional_fields else {}),
                    **({"field_aliases": dict(sorted(a.field_aliases.items()))} if a.field_aliases else {}),
                }
                for a in self.action_semantics
            ],
            "constraints": [
                {
                    "wicket_id": c.wicket_id,
                    "check": c.check,
                    "digest": c.definition_digest(),
                    **(
                        {"scope": list(self.constraint_scopes[c.wicket_id])}
                        if c.wicket_id in self.constraint_scopes
                        else {}
                    ),
                }
                for c in self.constraints
            ],
            "folds": [{"fold_id": f.fold_id, "digest": f.definition_digest()} for f in self.folds],
            "holds_integration": self.holds_integration,
        }
        if self.proposers:
            out["proposers"] = [
                {"id": p.id, "fold_id": p.fold_id, "strategy": p.strategy, "status": p.status} for p in self.proposers
            ]
        return out

    def definition_digest(self) -> str:
        """SHA-256 over the JCS bytes of the canonical pack definition -- the
        same digest a manifest's ``PackRef.digest`` cites."""
        try:
            return json_digest(self.canonical_dict())
        except FloatInDigestError as exc:
            raise PackDefinitionError(FLOAT_IN_PACK_DIGEST, str(exc)) from exc
        except UnsafeIntegerError as exc:
            raise PackDefinitionError(UNSAFE_INTEGER_IN_PACK_DIGEST, str(exc)) from exc

    def obligation_for_check(self, check: str) -> Obligation | None:
        for o in self.obligations:
            if o.check == check:
                return o
        return None

    def action_semantic_for(self, action_type: str) -> ActionSemantic | None:
        for a in self.action_semantics:
            if a.action_type == action_type:
                return a
        return None
