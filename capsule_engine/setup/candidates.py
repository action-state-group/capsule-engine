# SPDX-License-Identifier: Apache-2.0
"""Candidate outcome templates: what ``capsule setup propose`` grades
against an observed corpus (design §3.4). A candidate is NOT a guess mined
from nothing -- it is a small, named, structurally-typed statement (an
attainment claim, an offer/response claim, or a claim this format refuses
outright) whose *evidence rule* is fixed; what ``propose`` computes per
deployment is only the COVERAGE that evidence rule finds in the traces
actually observed, and, for the offer/response kind, whether the negative
case is instrumented at all. This is what makes the same three templates
produce three truthfully different answers across three deployments
(design §5) -- the templates don't change, the traces do.

Three kinds, one example of each, matching design §3.4's own worked
example almost exactly:

- ``attainment`` -- "an action of this class was confirmed by an external
  system." Fully decomposable: DETERMINISTIC/DETERMINISTIC once compiled,
  same as any plan/fold-backed declaration (``compiler/compile.py``).
- ``offer_response`` -- "a person was offered a choice and their response
  is on record." Coverage depends on the offer/response denominator
  primitive (design §4b gap 2) actually being instrumented; when the
  negative case (decline/defer) has never once been recorded, this
  downgrades to WITH-INSTRUMENTATION rather than silently reporting only
  the positive case.
- ``unbounded_goal`` / ``refused_effect`` -- REFUSED outright, corpus
  independent, using ``compiler.vocabulary``'s two seeded reason codes
  (``unbounded_goal_unmonitorable``, ``agent_caused_resolution_undecomposable``).
"""
from __future__ import annotations

from dataclasses import dataclass, field

__all__ = [
    "CandidateKind",
    "AttainmentCandidate",
    "OfferResponseCandidate",
    "RefusedCandidate",
    "Candidate",
    "DEFAULT_CANDIDATES",
]

CandidateKind = str  # "attainment" | "offer_response" | "refused"


@dataclass(frozen=True)
class AttainmentCandidate:
    """``action_class`` is deliberately the SAME identifier in both roles
    this candidate plays: the tag ``observe`` records on a dispatch's
    ``EVENT_DISPATCH`` detail (what ``propose`` groups traces by), and the
    literal ``Action.verb`` ``enforce`` checks against the compiled plan's
    ``allowed_actions`` (``compile_bridge.attainment_declaration_for``). A
    v0 simplification -- a real taxonomy where one class covers several
    verbs would need ``allowed_actions`` to carry more than one string,
    which ``compiler.compile.Declaration`` already supports; this template
    just doesn't exercise that yet."""

    outcome_id: str
    statement: str
    action_class: str
    kind: CandidateKind = field(default="attainment", init=False)


@dataclass(frozen=True)
class OfferResponseCandidate:
    outcome_id: str
    statement: str
    offer_namespace: str
    missing_instrument_label: str = "decline_event"
    kind: CandidateKind = field(default="offer_response", init=False)


@dataclass(frozen=True)
class RefusedCandidate:
    outcome_id: str
    statement: str
    reason_code: str  # one of compiler.vocabulary.REFUSAL_REASON_CODES
    effect_claim: str | None = None  # set only for agent_caused_resolution_undecomposable
    kind: CandidateKind = field(default="refused", init=False)


Candidate = AttainmentCandidate | OfferResponseCandidate | RefusedCandidate

_KIND_FIELDS: dict[str, tuple[str, ...]] = {
    "attainment": ("action_class",),
    "offer_response": ("offer_namespace", "missing_instrument_label"),
    "refused": ("reason_code", "effect_claim"),
}
_KIND_CLASSES: dict[str, type] = {
    "attainment": AttainmentCandidate,
    "offer_response": OfferResponseCandidate,
    "refused": RefusedCandidate,
}


def candidate_to_canonical_dict(c: Candidate) -> dict:
    """The candidate's own D-shape (design §2.1's ``D``): outcome_id,
    statement, kind, and kind-specific params -- stable and digestible
    regardless of which of the three template kinds this is, which is what
    lets ``declarations.DeclarationStore`` persist any candidate uniformly
    and ``propose --diff`` detect a planted change to any of them."""
    params = {name: getattr(c, name) for name in _KIND_FIELDS[c.kind]}
    return {"outcome_id": c.outcome_id, "statement": c.statement, "kind": c.kind, "params": params}


def candidate_from_canonical_dict(data: dict) -> Candidate:
    kind = data["kind"]
    cls = _KIND_CLASSES[kind]
    params = data.get("params", {})
    return cls(outcome_id=data["outcome_id"], statement=data["statement"], **params)

# The default catalog every ``capsule setup propose`` run grades unless
# ``--candidates`` overrides/extends it. Deliberately deployment-neutral
# names (no partner vocabulary anywhere in this repo, per the lane's
# boundary rule) -- these are the same three shapes design §3.4 worked
# through, renamed off any one deployment's own words.
DEFAULT_CANDIDATES: tuple[Candidate, ...] = (
    AttainmentCandidate(
        outcome_id="outcome.remediation_confirmed",
        statement="a remediation action was confirmed by an external system",
        action_class="remediation",
    ),
    OfferResponseCandidate(
        outcome_id="outcome.person_chose",
        statement="a person was offered a choice after being advised, and their response is on record",
        offer_namespace="advisory",
    ),
    RefusedCandidate(
        outcome_id="outcome.trust_increased",
        statement="the interaction increased the counterparty's trust in the system",
        reason_code="unbounded_goal_unmonitorable",
    ),
    RefusedCandidate(
        outcome_id="outcome.agent_resolved_case",
        statement="the agent's action caused the case to resolve",
        reason_code="agent_caused_resolution_undecomposable",
        effect_claim="agent.caused_resolution",
    ),
)
