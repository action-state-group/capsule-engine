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
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from agent_action_capsule.canonical import FloatInDigestError, UnsafeIntegerError, json_digest

from ..folds.definition import FoldDefinition
from ..guards.wickets.definition import WicketDefinition
from .errors import (
    FLOAT_IN_PACK_DIGEST,
    UNKNOWN_PROFILE_ID,
    UNSAFE_INTEGER_IN_PACK_DIGEST,
    PackDefinitionError,
)

__all__ = [
    "PACK_ID_RE",
    "NORMALIZED_ACTION_FIELDS",
    "HOLDS_INTEGRATION_VALUES",
    "KNOWN_SCOPE_DIMENSIONS",
    "MEASURABILITY_VALUES",
    "EVIDENCE_INSTRUMENT_KINDS",
    "TIER_VALUES",
    "MODE_VALUES",
    "PROFILE_ID_VALUES",
    "TOPOLOGY_INVARIANT_MODES",
    "Obligation",
    "ActionSemantic",
    "ProposerStub",
    "FixtureScenario",
    "PackFixtures",
    "WindowSpec",
    "EvidenceInstrument",
    "Outcome",
    "ScopeCensus",
    "CounterpartyBinding",
    "OutcomeOverride",
    "TopologyProfile",
    "ProfiledOutcomes",
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

# Whether an outcome's verdict is actually computed against this pack's
# fixtures/corpus, or declared honest-but-unmeasured because the corpus this
# pack ships against never emits the record the check would need (adversarial
# review finding, [pack-harden-tau2-oracle]: previously this was a hardcoded
# per-term "always inapplicable" lambda a future coder could point at ANY
# term -- including one with a real fail -- with nothing to catch it).
# "declared_not_measured" is not a permanent judgment about the STATEMENT;
# it is a factual claim about THIS pack's fixtures, and ``corpus_verify.py``
# is the oracle that makes the claim checkable rather than merely asserted.
MEASURABILITY_VALUES = frozenset({"measured", "declared_not_measured"})

# The closed set of evidence-instrument kinds ``corpus_verify.py`` knows how
# to resolve against a corpus. Deliberately narrow and mechanical -- both
# kinds ask "does any unit in the corpus carry this signal at all", never
# "is the signal correct", which is exactly the honest, cheap-to-verify shape
# of "this pack's corpus has -- or lacks -- the record type a term needs":
#   * structured_field: a named key on a unit/message dict, outside whatever
#     base schema the corpus's own reconstruction produces (e.g. a typed
#     severity/efficacy label, a stated-constraint field, a restriction-
#     reason-cited marker) -- present with a non-empty value anywhere means
#     the corpus DOES carry it.
#   * tool_call_name: a named tool/function call appearing anywhere in a
#     unit's tool-call trail.
# An unrecognized kind is a typo, same "unregistered is a typo" doctrine as
# every other closed set in this module -- never silently accepted as data.
EVIDENCE_INSTRUMENT_KINDS = frozenset({"structured_field", "tool_call_name"})

# Whether an outcome gates a session's job-success (backward-judge design
# §8.2). "must_have" terms are the ones §8.4's per-session rollup requires to
# hold for every session they apply to; "informational" terms are reported
# but never gate. No per-term target/ratio -- the gate is entirely at the
# session level, so this is a single closed-set field, not a threshold shape.
# "informational" is the default for a term with no ``tier`` declared, so
# every pack written before this field existed keeps its current (gate-free)
# behavior and digests identically.
TIER_VALUES = frozenset({"must_have", "informational"})

# The seven ways a ledger gets judged (standard-outcome-pack design §3) --
# every standard outcome is tagged with exactly one. "structural" (presence/
# absence over emitted fields, no model) is the default for an outcome with
# no ``mode`` declared, because it's what every pre-``mode`` outcome already
# was in practice (design §8.1: "the deterministic set is structural checks
# over emitted fields -- nothing else"), so a pack written before this field
# existed parses and digests identically to before.
MODE_VALUES = frozenset(
    {
        "structural",
        "value",
        "judged",
        "fold_rollup",
        "fold_counterparty",
        "fold_agent",
        "fold_cohort",
    }
)

# Relationship-topology profiles ([ldg-bp-topology-profiles], standard-
# outcome-pack design §6b): who the agent works WITH -- the same standard
# pack graded differently depending on whether the direct counterparty is an
# external customer, an internal employee, a mediated employee-on-behalf-of-
# a-downstream-customer, or another agent. P5 (autonomous, no direct
# counterparty at all) is DEFERRED per the design until a real autonomous
# case lands -- not in this closed set yet, so a pack cannot declare it as
# though it were already specified.
PROFILE_ID_VALUES = frozenset(
    {
        "p1_external_serve",
        "p2_internal_assist",
        "p3_mediated",
        "p4_agent_to_agent",
    }
)

# The judgment modes design §6b calls the "topology-invariant" trust floor --
# structural presence/absence checks, arithmetic over sealed numbers, and the
# per-session job-success rollup derived from them. None of these have a
# "who" the way a judged conduct row or a counterparty-trend fold does, so a
# topology profile is never allowed to override applicability/tier for an
# outcome in one of these modes -- loader.py enforces this mechanically
# rather than trusting a pack author's restraint (the same "unregistered is
# a typo" / "invariant is compile-time, not convention" doctrine every other
# closed-set check in this module already follows).
TOPOLOGY_INVARIANT_MODES = frozenset({"structural", "value", "fold_rollup"})


@dataclass(frozen=True)
class Obligation:
    """The human-readable contract this pack encodes, mapped 1:1 to a check
    (every obligation maps 1:1 to the check that enforces it).

    ``re_derivability_grade`` (design §2.3, compiler-and-setup-design
    2026-08-19) is optional and additive -- an undeclared grade is a
    legitimately different state from either closed-set value, not a typo,
    so existing packs (declared before this field existed) parse and digest
    identically to before. When declared it must be one of
    ``vocabulary.RE_DERIVABILITY_GRADES`` -- ``compiler.re_derivability.
    grade_for_check`` gives the seeded default for the checks this repo
    already ships.
    """

    id: str
    statement: str
    check: str
    re_derivability_grade: str | None = None


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
class WindowSpec:
    """Bounded-liveness window (design §3.1/§9.2): an outcome statement of
    the "eventually X" shape is only monitorable once it names how long
    "eventually" means. ``cure``/``grace`` are optional ISO-8601 duration
    strings (e.g. ``"P7D"``) -- present, even if null, because compile-time
    retention checking (window vs. WORM tier) is one of the non-retrofittable
    fields the design calls out by name."""

    duration: str
    cure: str | None = None
    grace: str | None = None


@dataclass(frozen=True)
class EvidenceInstrument:
    """What ``corpus_verify.py`` resolves against a corpus to check a
    ``measurability`` claim -- see ``EVIDENCE_INSTRUMENT_KINDS`` for the
    closed set of ``kind``s and what each one means. Exactly one of
    ``field``/``name`` is set, matching whichever ``kind`` this is."""

    kind: str
    field: str | None = None  # kind == "structured_field"
    name: str | None = None  # kind == "tool_call_name"

    def to_dict(self) -> dict:
        out: dict[str, Any] = {"kind": self.kind}
        if self.field is not None:
            out["field"] = self.field
        if self.name is not None:
            out["name"] = self.name
        return out


@dataclass(frozen=True)
class Outcome:
    """The sister table to ``Obligation`` (design §0: "one declaration ...
    compiled forward into a check ... compiled backward into a report").

    ``evidence_rule`` is a reference/expression naming which capsule
    pattern counts as confirming evidence (Outcome Compiler doc §4.1) --
    this module only requires it to be present and non-empty; validating it
    against a real observed field basis is the evidence-rule lint (Track A
    / B1), a later, separate task. A declared outcome with no confirming-
    evidence rule at all is a schema error here, full stop.

    ``forward_verdict``/``backward_verdict`` are the verdict PAIR (design
    §2.2) -- a schema shape decision, not an annotation: the judge is never
    in the enforcement path, so a statement's forward and backward
    mappability are independent facts, both always present.

    ``effect_claim``, when set, must be one of
    ``compiler.effect_model.EFFECT_CLAIMS``; ``loader.py`` enforces that a
    refused claim (``agent.caused_resolution``) can only be declared with
    the verdict pair and reason code ``compile_effect_claim`` computes for
    it -- REFUSED at compile time is mechanical, not a reviewer's judgment
    call. ``refusal_reason_code`` is required whenever either verdict is
    ``"REFUSED"``, for any reason (not only a refused effect claim -- e.g.
    an unbounded, un-windowed goal).

    ``declared_by``/``evidence_mapping_by`` are reserved now, semantics
    later (Outcome Compiler doc §11; DECISION 2026-08-18: default is
    vendor-led, customer accepts -- but the report-facing computed enum
    this cashes out to is design §7, still open). Free-form strings here on
    purpose -- do not read closed-vocabulary meaning into them yet.

    ``tier`` (backward-judge design §8.2) says whether this outcome gates a
    session's job-success (``"must_have"``) or is reported without gating
    (``"informational"``, the default) -- see ``TIER_VALUES``.

    ``mode`` (standard-outcome-pack design §3) says which of the seven ways
    this outcome is judged -- see ``MODE_VALUES``. Lets a report group by
    judgment mode and lets ``propose`` route grading.
    """

    id: str
    statement: str
    evidence_rule: str
    forward_verdict: str
    backward_verdict: str
    window: WindowSpec | None = None
    effect_claim: str | None = None
    refusal_reason_code: str | None = None
    re_derivability_grade: str | None = None
    declared_by: str | None = None
    evidence_mapping_by: str | None = None
    required_assurance_grade: str | None = None
    exposure_denominator_ref: str | None = None
    retention_check: str | None = None
    # measurability/evidence_instrument -- optional, additive
    # ([pack-harden-tau2-oracle]); default "measured" so a pack declared
    # before this field existed parses and DIGESTS identically to before
    # (canonical_dict below omits it whenever it's the default, same
    # convention every other optional Outcome field already follows).
    measurability: str = "measured"
    evidence_instrument: EvidenceInstrument | None = None
    # tier -- optional, additive ([ldg-bj-tier-field], backward-judge design
    # §8.2); default "informational" so a pack declared before this field
    # existed parses and DIGESTS identically to before (canonical_dict below
    # omits it whenever it's the default, same convention measurability
    # already follows).
    tier: str = "informational"
    # mode -- optional, additive ([ldg-bp-mode-tag], standard-outcome-pack
    # design §3); default "structural" so an outcome declared before this
    # field existed parses and DIGESTS identically to before (canonical_dict
    # below omits it whenever it's the default, same convention tier already
    # follows).
    mode: str = "structural"


@dataclass(frozen=True)
class ScopeCensus:
    """The T2 CLAIM a pack declares (design §4/§4b gap 3): "this pack covers
    ``n`` of the ``m`` outcomes/obligations in the document identified by
    ``document_digest``." This is the declaration; ``compiler.scope_census.
    build_scope_census_capsule`` seals the human sign-off ACT on it as a
    ledger record -- the pack.yaml field alone is not a recorded act."""

    document_digest: str
    n: int
    m: int
    review_by: str


@dataclass(frozen=True)
class CounterpartyBinding:
    """Which subject a profile's counterparty-scoped outcomes bind to
    (design §6b). ``direct`` is who the agent actually talks to; ``ultimate``
    is who the engagement is ultimately FOR -- equal for every profile except
    P3 mediated, where the direct counterparty (the employee) is a conduit to
    an ultimate one (their downstream customer) one hop further out. That
    equality-except-P3 shape is exactly what makes P3's binding "dual"
    without needing a separate field or profile-specific schema.

    The binding fixes which role a counterparty-scoped outcome reads by its
    own ``mode`` (``ProfiledOutcomes.subject_for``): a ``fold_counterparty``
    row (the differentiated counterparty-CHANGE value-props, family E) always
    binds to ``direct`` -- it is a trend over sessions with whoever the agent
    actually engaged; a ``fold_rollup`` row (job success) binds to
    ``ultimate`` -- job success is about whether the engagement's real end
    beneficiary was served, which is the downstream customer in P3, not the
    employee relaying to them.
    """

    direct: str
    ultimate: str

    def to_dict(self) -> dict:
        return {"direct": self.direct, "ultimate": self.ultimate}


@dataclass(frozen=True)
class OutcomeOverride:
    """One profile's ``{applies, tier}`` override for a single outcome_id
    (design §6b/§7). ``applies=False`` means the outcome is N/A under this
    profile (e.g. conduct/C-family under P4 agent-to-agent) -- it never
    silently changes a REFUSED/verdict-pair outcome, only whether it's in
    scope and, when in scope, which tier it gates at."""

    outcome_id: str
    applies: bool = True
    tier: str | None = None

    def to_dict(self) -> dict:
        out: dict[str, Any] = {"outcome_id": self.outcome_id}
        if not self.applies:
            out["applies"] = False
        if self.tier is not None:
            out["tier"] = self.tier
        return out


@dataclass(frozen=True)
class TopologyProfile:
    """A named, T1-confirmed declaration over the standard outcomes (design
    §6b/§7): a ``profile_id`` (one of ``PROFILE_ID_VALUES``) plus a
    ``counterparty_binding`` plus a set of per-outcome ``{applies, tier}``
    overrides. Not a forked pack -- the profile only ever *narrows or
    re-tiers* outcomes the pack already declares; ``loader.py`` refuses an
    override naming an outcome_id the pack doesn't have, or an outcome whose
    ``mode`` is in ``TOPOLOGY_INVARIANT_MODES``."""

    profile_id: str
    counterparty_binding: CounterpartyBinding
    overrides: tuple[OutcomeOverride, ...] = ()

    def override_for(self, outcome_id: str) -> OutcomeOverride | None:
        for o in self.overrides:
            if o.outcome_id == outcome_id:
                return o
        return None

    def canonical_dict(self) -> dict:
        return {
            "profile_id": self.profile_id,
            "counterparty_binding": self.counterparty_binding.to_dict(),
            "overrides": [o.to_dict() for o in sorted(self.overrides, key=lambda o: o.outcome_id)],
        }


@dataclass(frozen=True)
class ProfiledOutcomes:
    """The result of applying a ``TopologyProfile`` to a pack's outcomes
    (``PackDefinition.outcomes_for_profile``): ``outcomes`` carries every
    outcome that still applies under this profile (tier replaced where the
    profile overrides it, same relative order as the pack's own
    ``outcomes``), ``excluded`` names the ones this profile marks N/A, sorted
    for a stable report. Never mutates the pack's own ``Outcome`` objects --
    this is a view, recomputed from ``profile`` + the pack's outcomes every
    time, so it can never drift from either."""

    profile_id: str
    counterparty_binding: CounterpartyBinding
    outcomes: tuple[Outcome, ...]
    excluded: tuple[str, ...]

    def subject_for(self, outcome_id: str) -> str | None:
        """Which counterparty subject this outcome's verdict is ABOUT under
        this profile (``CounterpartyBinding``'s docstring) -- ``None`` for an
        outcome whose mode isn't counterparty-scoped at all (structural/
        value/judged/fold_agent/fold_cohort rows describe the agent's own
        conduct or trajectory, not one counterparty)."""
        for o in self.outcomes:
            if o.id == outcome_id:
                if o.mode == "fold_counterparty":
                    return self.counterparty_binding.direct
                if o.mode == "fold_rollup":
                    return self.counterparty_binding.ultimate
                return None
        return None


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
    # outcomes[]/scope_census -- the compiler's schema surface (design of
    # record, 2026-08-19). Both default to empty/None so a pack declared
    # before this field existed parses and DIGESTS identically to before
    # (canonical_dict below includes them only when non-empty, same
    # convention as ``proposers``).
    outcomes: tuple[Outcome, ...] = ()
    scope_census: ScopeCensus | None = None
    # wicket_id -> declared scope dimensions, e.g. {"payments_safety.caps/1.0.0":
    # ("developer",)}. Required for every `caps` constraint (loader.py enforces
    # this); optional documentation for other check types.
    constraint_scopes: dict[str, tuple[str, ...]] = field(default_factory=dict)
    # profiles[] -- relationship-topology profiles over this pack's own
    # outcomes ([ldg-bp-topology-profiles], design §6b/§7). Defaults to empty
    # so a pack declared before this field existed parses and DIGESTS
    # identically to before (canonical_dict below includes it only when
    # non-empty, same convention as ``proposers``/``outcomes``).
    profiles: tuple[TopologyProfile, ...] = ()

    def canonical_dict(self) -> dict:
        """The JCS-canonicalizable form of this pack -- drives
        ``definition_digest()``. Constraints/folds are cited by their own
        digest, never copied in whole -- see the module docstring."""
        out: dict[str, Any] = {
            "pack_id": self.pack_id,
            "obligations": [
                {
                    "id": o.id,
                    "statement": o.statement,
                    "check": o.check,
                    **({"re_derivability_grade": o.re_derivability_grade} if o.re_derivability_grade else {}),
                }
                for o in self.obligations
            ],
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
        if self.outcomes:
            out["outcomes"] = [
                {
                    "id": o.id,
                    "statement": o.statement,
                    "evidence_rule": o.evidence_rule,
                    "forward_verdict": o.forward_verdict,
                    "backward_verdict": o.backward_verdict,
                    **(
                        {"window": {"duration": o.window.duration, "cure": o.window.cure, "grace": o.window.grace}}
                        if o.window
                        else {}
                    ),
                    **({"effect_claim": o.effect_claim} if o.effect_claim else {}),
                    **({"refusal_reason_code": o.refusal_reason_code} if o.refusal_reason_code else {}),
                    **({"re_derivability_grade": o.re_derivability_grade} if o.re_derivability_grade else {}),
                    **({"declared_by": o.declared_by} if o.declared_by else {}),
                    **({"evidence_mapping_by": o.evidence_mapping_by} if o.evidence_mapping_by else {}),
                    **({"required_assurance_grade": o.required_assurance_grade} if o.required_assurance_grade else {}),
                    **({"exposure_denominator_ref": o.exposure_denominator_ref} if o.exposure_denominator_ref else {}),
                    **({"retention_check": o.retention_check} if o.retention_check else {}),
                    **({"measurability": o.measurability} if o.measurability != "measured" else {}),
                    **({"tier": o.tier} if o.tier != "informational" else {}),
                    **({"mode": o.mode} if o.mode != "structural" else {}),
                    **(
                        {"evidence_instrument": o.evidence_instrument.to_dict()}
                        if o.evidence_instrument is not None
                        else {}
                    ),
                }
                for o in self.outcomes
            ]
        if self.scope_census:
            out["scope_census"] = {
                "document_digest": self.scope_census.document_digest,
                "n": self.scope_census.n,
                "m": self.scope_census.m,
                "review_by": self.scope_census.review_by,
            }
        if self.profiles:
            out["profiles"] = [p.canonical_dict() for p in sorted(self.profiles, key=lambda p: p.profile_id)]
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

    def outcome_for_id(self, outcome_id: str) -> Outcome | None:
        for o in self.outcomes:
            if o.id == outcome_id:
                return o
        return None

    def action_semantic_for(self, action_type: str) -> ActionSemantic | None:
        for a in self.action_semantics:
            if a.action_type == action_type:
                return a
        return None

    def profile_for(self, profile_id: str) -> TopologyProfile | None:
        for p in self.profiles:
            if p.profile_id == profile_id:
                return p
        return None

    def outcomes_for_profile(self, profile_id: str) -> ProfiledOutcomes:
        """Apply a declared ``TopologyProfile`` to this pack's own outcomes
        (design §6b): every outcome whose override says ``applies=False`` is
        dropped into ``excluded``; every other outcome is kept, with its
        ``tier`` replaced by the override's when one is declared. Raises if
        ``profile_id`` isn't one this pack actually declares -- there is no
        silent "no profile selected" fallback, because a topology profile is
        a T1-confirmed fact about the engagement, not an optional filter."""
        profile = self.profile_for(profile_id)
        if profile is None:
            raise PackDefinitionError(
                UNKNOWN_PROFILE_ID,
                f"{profile_id!r} is not one of this pack's declared profiles: "
                f"{sorted(p.profile_id for p in self.profiles) or '<none>'}",
            )
        included: list[Outcome] = []
        excluded: list[str] = []
        for o in self.outcomes:
            override = profile.override_for(o.id)
            if override is not None and not override.applies:
                excluded.append(o.id)
                continue
            if override is not None and override.tier is not None:
                o = replace(o, tier=override.tier)
            included.append(o)
        return ProfiledOutcomes(
            profile_id=profile.profile_id,
            counterparty_binding=profile.counterparty_binding,
            outcomes=tuple(included),
            excluded=tuple(sorted(excluded)),
        )
