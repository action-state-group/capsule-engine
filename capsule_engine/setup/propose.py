# SPDX-License-Identifier: Apache-2.0
"""``capsule setup propose`` (design §3.4/§6b): the compiler run backwards
from observed traces. Grades ``candidates.DEFAULT_CANDIDATES`` (or a
caller-supplied catalog) against the emit-layer corpus ``observe`` wrote,
and drafts one ``ProposedOutcome`` per candidate that has ANY evidence in
this corpus -- coverage always as **N of M, never a bare percentage**
(design §3.4), WITH-INSTRUMENTATION items always name the missing
instrument, and refusals render exactly as prominently as successes.

**``propose`` writes a file, not a terminal scroll** (design §3.2): the
terminal render here is the preview; ``proposals.yaml``
(``write_proposals_yaml``) is the diffable, committable, reviewable
artifact ``confirm`` actually acts on.

**The drift check** (design §3.2's terraform analogy, acceptance line):
re-running ``propose`` and diffing its candidates against what
``declarations.DeclarationStore`` has on record for the SAME outcome_ids is
the whole mechanism -- ``diff_against_stored`` below, not a second, bespoke
diff tool.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml
from capsule_ledger.ledger.api import LedgerAPI, ScanQuery

from ..compiler.effect_model import compile_effect_claim
from ..compiler.offer_response import EVENT_OFFER, EVENT_RESPONSE
from ..compiler.vocabulary import REFUSAL_REASON_CODES, display_string
from .candidates import (
    DEFAULT_CANDIDATES,
    AttainmentCandidate,
    Candidate,
    OfferResponseCandidate,
    RefusedCandidate,
    candidate_to_canonical_dict,
)
from .declarations import DeclarationStore, StoredCandidate, candidate_digest
from .observe import EVENT_CONFIRMATION, EVENT_DISPATCH
from .scan import detail as _detail
from .scan import parent as _parent
from .scan import scan_event as _scan

__all__ = [
    "ProposedOutcome",
    "ProposalSet",
    "DriftEntry",
    "propose_from_ledger",
    "persist_proposals",
    "render_terminal",
    "write_proposals_yaml",
    "load_proposals_yaml",
    "diff_against_stored",
]


@dataclass(frozen=True)
class ProposedOutcome:
    outcome_id: str
    statement: str
    forward_verdict: str
    backward_verdict: str
    coverage_n: int | None
    coverage_m: int | None
    rationale: str
    missing_instrument: str | None = None
    refusal_reason_code: str | None = None
    candidate: Candidate | None = field(repr=False, compare=False, default=None)

    @property
    def is_refused(self) -> bool:
        return "REFUSED" in (self.forward_verdict, self.backward_verdict)

    @property
    def needs_instrumentation(self) -> bool:
        return self.backward_verdict == "WITH-INSTRUMENTATION"

    def status_glyph(self) -> str:
        if self.is_refused:
            return "✗"  # ✗
        if self.needs_instrumentation:
            return "⚠"  # ⚠
        return "✓"  # ✓

    def coverage_fraction(self) -> str | None:
        if self.coverage_n is None or self.coverage_m is None:
            return None
        pct = round(100 * self.coverage_n / self.coverage_m) if self.coverage_m else 0
        return f"{self.coverage_n} of {self.coverage_m} ({pct}%)"


@dataclass(frozen=True)
class ProposalSet:
    proposals: tuple[ProposedOutcome, ...]
    records_observed: int


def _attainment_coverage(ledger: LedgerAPI, action_class: str) -> tuple[int, int]:
    dispatches = [r for r in _scan(ledger, EVENT_DISPATCH) if _detail(r).get("action_class") == action_class]
    dispatch_ids = {r.capsule["capsule_id"] for r in dispatches}
    confirmed = {
        _parent(r)
        for r in _scan(ledger, EVENT_CONFIRMATION)
        if _parent(r) in dispatch_ids and _detail(r).get("status") == "confirmed"
    }
    return len(confirmed), len(dispatches)


def _offer_response_coverage(ledger: LedgerAPI, namespace: str) -> tuple[int, int, bool]:
    prefix = f"{namespace}/"
    offers = [r for r in _scan(ledger, EVENT_OFFER) if _detail(r).get("offer_id", "").startswith(prefix)]
    offer_ids = {r.capsule["capsule_id"] for r in offers}
    responses = _scan(ledger, EVENT_RESPONSE)
    instrumented = any(_detail(r).get("response_class") in ("declined", "deferred") for r in responses)
    accepted = sum(1 for r in responses if _parent(r) in offer_ids and _detail(r).get("response_class") == "accepted")
    return accepted, len(offers), instrumented


def _propose_attainment(ledger: LedgerAPI, c: AttainmentCandidate) -> ProposedOutcome | None:
    n, m = _attainment_coverage(ledger, c.action_class)
    if m == 0:
        return None
    return ProposedOutcome(
        outcome_id=c.outcome_id,
        statement=c.statement,
        forward_verdict="DETERMINISTIC",
        backward_verdict="DETERMINISTIC",
        coverage_n=n,
        coverage_m=m,
        rationale=f"evidence rule: a {c.action_class!r} dispatch chained to a confirmation with status=confirmed",
        candidate=c,
    )


def _propose_offer_response(ledger: LedgerAPI, c: OfferResponseCandidate) -> ProposedOutcome | None:
    n, m, instrumented = _offer_response_coverage(ledger, c.offer_namespace)
    if m == 0:
        return None
    if instrumented:
        return ProposedOutcome(
            outcome_id=c.outcome_id,
            statement=c.statement,
            forward_verdict="UNAVAILABLE-STATE-REQUIRED",
            backward_verdict="DETERMINISTIC",
            coverage_n=n,
            coverage_m=m,
            rationale="evidence rule: offer chained to response, response_class recorded including declines/defers",
            candidate=c,
        )
    return ProposedOutcome(
        outcome_id=c.outcome_id,
        statement=c.statement,
        forward_verdict="UNAVAILABLE-STATE-REQUIRED",
        backward_verdict="WITH-INSTRUMENTATION",
        coverage_n=n,
        coverage_m=m,
        rationale=(
            f"MISSING INSTRUMENT: no {c.missing_instrument_label.replace('_', ' ')} is recorded today "
            "(accepts are recorded; declines/defers are not)"
        ),
        missing_instrument=c.missing_instrument_label,
        candidate=c,
    )


def _propose_refused(c: RefusedCandidate) -> ProposedOutcome:
    if c.reason_code not in REFUSAL_REASON_CODES:
        raise ValueError(f"reason_code must be one of {sorted(REFUSAL_REASON_CODES)}; got {c.reason_code!r}")
    if c.effect_claim is not None:
        # Structural refusal already computed by the compiler's own effect
        # model (design §4b gap 1) -- never re-derived by hand here.
        compiled = compile_effect_claim(c.effect_claim)
        reason_code = compiled.refusal_reason_code
    else:
        reason_code = c.reason_code
    return ProposedOutcome(
        outcome_id=c.outcome_id,
        statement=c.statement,
        forward_verdict="REFUSED",
        backward_verdict="REFUSED",
        coverage_n=None,
        coverage_m=None,
        rationale=f"REFUSED -- {display_string('refusal_reason_code', reason_code)}",
        refusal_reason_code=reason_code,
        candidate=c,
    )


def propose_from_ledger(ledger: LedgerAPI, candidates: tuple[Candidate, ...] = DEFAULT_CANDIDATES) -> ProposalSet:
    proposals: list[ProposedOutcome] = []
    records_observed = sum(1 for _ in ledger.scan(ScanQuery(action_type="fyi")))
    for c in candidates:
        if isinstance(c, AttainmentCandidate):
            outcome = _propose_attainment(ledger, c)
        elif isinstance(c, OfferResponseCandidate):
            outcome = _propose_offer_response(ledger, c)
        elif isinstance(c, RefusedCandidate):
            outcome = _propose_refused(c)
        else:  # pragma: no cover - closed set, defensive
            raise TypeError(f"unknown candidate type {type(c)!r}")
        if outcome is not None:
            proposals.append(outcome)
    return ProposalSet(proposals=tuple(proposals), records_observed=records_observed)


def persist_proposals(proposal_set: ProposalSet, store: DeclarationStore) -> None:
    """Write every proposed candidate into ``store`` at ``proposed`` state.

    An outcome_id already ``accepted``/``refused`` is a FROZEN T1/T4 record
    (design §3.5: "confirm produces recorded acts, never config edits") and
    is never overwritten here, even when today's candidate would compile to
    different bytes -- that mismatch is exactly what a mutated/drifted
    candidate looks like, and ``diff_against_stored`` must still be able to
    see the OLD frozen digest to report it. Silently re-freezing on every
    propose run would erase the drift it exists to catch."""
    for p in proposal_set.proposals:
        if store.exists(p.outcome_id) and store.load(p.outcome_id).acceptance_state != "proposed":
            continue
        store.save(
            p.candidate,
            acceptance_state="proposed",
            forward_verdict=p.forward_verdict,
            backward_verdict=p.backward_verdict,
            refusal_reason_code=p.refusal_reason_code,
            missing_instrument=p.missing_instrument,
        )


def render_terminal(proposal_set: ProposalSet) -> str:
    lines = [f"observed {proposal_set.records_observed} emit-layer record(s)."]
    lines.append("")
    for p in proposal_set.proposals:
        lines.append(f"  {p.status_glyph()} {p.outcome_id}")
        lines.append(f"      backward {p.backward_verdict} · forward {p.forward_verdict}")
        fraction = p.coverage_fraction()
        if fraction is not None:
            lines.append(f"      provable on {fraction}")
        lines.append(f"      {p.rationale}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _proposal_to_dict(p: ProposedOutcome) -> dict:
    d = {
        "outcome_id": p.outcome_id,
        "statement": p.statement,
        "forward_verdict": p.forward_verdict,
        "backward_verdict": p.backward_verdict,
        "rationale": p.rationale,
    }
    if p.coverage_n is not None:
        d["coverage_n"] = p.coverage_n
        d["coverage_m"] = p.coverage_m
    if p.missing_instrument is not None:
        d["missing_instrument"] = p.missing_instrument
    if p.refusal_reason_code is not None:
        d["refusal_reason_code"] = p.refusal_reason_code
    d["declaration"] = candidate_to_canonical_dict(p.candidate)
    return d


def write_proposals_yaml(path: str | Path, proposal_set: ProposalSet) -> None:
    data = {
        "records_observed": proposal_set.records_observed,
        "proposals": [_proposal_to_dict(p) for p in proposal_set.proposals],
    }
    Path(path).write_text(yaml.safe_dump(data, sort_keys=False))


def load_proposals_yaml(path: str | Path) -> dict:
    return yaml.safe_load(Path(path).read_text())


@dataclass(frozen=True)
class DriftEntry:
    outcome_id: str
    stored: StoredCandidate
    current_candidate: Candidate
    drifted: bool
    stored_digest: str
    current_digest: str


def diff_against_stored(
    proposal_set: ProposalSet, store: DeclarationStore
) -> list[DriftEntry]:
    """The drift check (design §3.2 acceptance line): for every proposed
    outcome that ALSO already has a stored candidate (``propose``'s own
    earlier write, or an accepted one), recompute D's digest from the
    candidate ``propose`` would draft today and compare it against what is
    on record. A mismatch is drift -- planted by hand-editing a candidate
    template (e.g. widening ``allowed_actions``/``action_class``) without
    going back through ``confirm``, which is exactly the failure mode this
    check exists to catch before it reaches an accepted declaration."""
    entries: list[DriftEntry] = []
    for p in proposal_set.proposals:
        if not store.exists(p.outcome_id):
            continue
        stored = store.load(p.outcome_id)
        current_digest = candidate_digest(p.candidate)
        entries.append(
            DriftEntry(
                outcome_id=p.outcome_id,
                stored=stored,
                current_candidate=p.candidate,
                drifted=stored.d_digest != current_digest,
                stored_digest=stored.d_digest,
                current_digest=current_digest,
            )
        )
    return entries
