# SPDX-License-Identifier: Apache-2.0
"""Adapter: a ``deal reconcile`` report plus per-action layer observations ->
one Result v0 per layer, never one combined aggregate.

A run that checks each consequential action at several layers (was the
check invoked, was the action described correctly, was the returned
disposition honored, ...) reports each layer as its own metric. This module
builds one ``EvidenceResult`` per layer with ``build_result``, so every
layer's coverage and buckets are counted off that layer's own claims, and
wraps them in a ``layer-tally/v0`` document that carries what the Result v0
schema has no field for: the tool version under test, the period, and the
reconcile pass's ``coverage.cannot_see`` list. Nothing here adds the layers
together.

The action population is the reconcile report's consequential set
(``recorded``, ``unrecorded`` and ``failed_attempts`` rows). The first
layer, the invocation layer, is read off that report and nothing else:

- a ``recorded`` row is ``met`` (a sealed deal step accounts for it);
- an ``unrecorded`` row is ``not_met`` (no deal step accounts for it);
- a ``failed_attempts`` row is ``not_evaluable`` with sufficiency
  ``UNKNOWN``: reconcile lists it for review because a failed attempt can
  still have had an effect, so whether a check was owed is not settled;
- ``not_consequential`` is that layer's ``excluded_not_applicable`` count.

Every later layer names a ``gate`` layer. An action reaches it only where
its gate claim is ``met``; where the gate is ``not_met`` or excluded, the
action is excluded as NOT_APPLICABLE for this layer (spec section 3: it
never becomes a claim, it is only counted). Where the gate is
``not_evaluable``, whether the layer applied is itself unsettled, so the
action is ``not_evaluable`` here too. An action that reaches a layer with
no observation is ``not_evaluable`` with sufficiency ``UNKNOWN``: no
observed check is never a ``met``.

Grades: a layer whose population is a party's own report is configured
``self_reported``, and every claim on it is ``self-attested`` -- the count
is a lower bound on what happened, however tamper-evident the record. The
invocation layer is always self-reported: reconcile reads the agent host's
own records on the agent's own machine, so it must be configured that way,
and every invocation claim cites the digest of the executions file
reconcile read (``executions_sha256``) as its first evidence. On other
layers, observations carry the grade the caller read off the sealed record;
on a self-reported layer, any grade but ``self-attested`` is refused.
A ``judged`` layer must name its judge pin, which every claim of that layer
carries as its first evidence digest; a ``recomputed`` layer must not.
"""
from __future__ import annotations

import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import NamedTuple, NotRequired, TypedDict

from agent_action_capsule.canonical import json_digest

from .errors import COVERAGE_NOT_COMPUTED, INVALID_LAYER_TALLY, ResultError
from .result import (
    AnalysisCarrier,
    Claim,
    Coverage,
    DigestRef,
    DisclosureCarrier,
    EvidenceResult,
    build_result,
    validate_against_schema,
    verify_result,
)

__all__ = [
    "TALLY_VERSION",
    "CoverageCounts",
    "ReconcileCounts",
    "LayerEntry",
    "LayerTallyDoc",
    "ActionLayer",
    "LayerSpec",
    "LayerObservation",
    "ReconcileRow",
    "ReconcileReport",
    "LayerAggregate",
    "LayerTally",
    "build_layer_tally",
    "verify_layer_tally",
    "render_layer_tally",
]

TALLY_VERSION = "layer-tally/v0"

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_MATCHED_BY = frozenset({"act", "disclosure", "check"})
_RECONCILE_COUNTS = ("records_read", "outside_period", "not_consequential", "consequential")
# The Analysis carrier status for an observation the record did not settle,
# using the coverage report's fixed mapping (GAP <-> NOT_FOUND).
_UNSETTLED_STATUS = {"INSUFFICIENT": "INSUFFICIENT", "GAP": "NOT_FOUND", "UNKNOWN": "UNKNOWN"}


class CoverageCounts(TypedDict):
    evaluated_population: int
    excluded_not_applicable: int
    unknown_count: int


class ReconcileCounts(TypedDict):
    records_read: int
    outside_period: int
    not_consequential: int
    consequential: int


class ReconcileSource(ReconcileCounts):
    kind: str
    failed_attempts: int
    # SHA-256 of the executions file the reconcile pass read.
    executions_sha256: str
    # The reconcile report's own ``coverage`` block, carried verbatim:
    # ``cannot_see`` is checked non-empty, the rest is the pass's wording.
    coverage: Mapping[str, object]


class TallyTool(TypedDict):
    name: str
    version: str


TallyPeriod = TypedDict("TallyPeriod", {"from": str, "to": str})


class LayerEntry(TypedDict):
    layer: str
    tier: str
    gate: NotRequired[str]
    judge_pin: NotRequired[str]
    self_reported: bool
    coverage: CoverageCounts
    # A Result v0 document, as ``EvidenceResult.to_dict`` returns it.
    result: dict | None


class LayerTallyDoc(TypedDict):
    tally_version: str
    generated_at: str
    tool: TallyTool
    period: TallyPeriod
    source: ReconcileSource
    layers: list[LayerEntry]


def _tally_error(message: str) -> ResultError:
    return ResultError(INVALID_LAYER_TALLY, message)


def _check_rfc3339(name: str, value: str) -> None:
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise _tally_error(f"{name} must be an RFC 3339 time, got {value!r}") from exc


class ActionLayer(NamedTuple):
    action_id: str
    layer_id: str


@dataclass(frozen=True)
class LayerSpec:
    """One layer, as the caller configures it. ``gate`` is the id of an
    earlier layer; ``None`` only for the invocation layer."""

    layer_id: str
    requirement_ref: str
    tier: str
    gate: str | None = None
    judge_pin: str | None = None
    self_reported: bool = False

    def __post_init__(self) -> None:
        if not self.layer_id or not self.requirement_ref:
            raise _tally_error("layer_id and requirement_ref must be non-empty")
        if self.tier == "judged":
            if self.judge_pin is None:
                raise _tally_error(f"layer {self.layer_id!r} is judged and must name its judge pin")
            DigestRef(digest=self.judge_pin)
        elif self.tier == "recomputed":
            if self.judge_pin is not None:
                raise _tally_error(f"layer {self.layer_id!r} is recomputed and carries no judge pin")
        else:
            raise _tally_error(f"layer {self.layer_id!r} tier must be recomputed|judged, got {self.tier!r}")


@dataclass(frozen=True)
class LayerObservation:
    """What the caller observed for one action at one layer, read off the
    sealed record: its grade, whether the record settled the question
    (``sufficiency``) and, if so, the verdict. ``Claim`` enforces the
    sufficiency/verdict rule when the claim is built."""

    action_id: str
    layer_id: str
    grade: str
    sufficiency: str
    verdict: str
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class ReconcileRow:
    id: str
    digest: str
    matched_by: str | None
    capsule_id: str | None


def _row(raw: Mapping[str, object], where: str, *, recorded: bool) -> ReconcileRow:
    row_id = raw.get("id")
    if not isinstance(row_id, str) or not row_id:
        raise _tally_error(f"{where}: every row needs a non-empty string id")
    matched_by = raw.get("matched_by")
    capsule_id = raw.get("capsule_id")
    if recorded:
        if matched_by not in _MATCHED_BY:
            raise _tally_error(f"{where} row {row_id!r}: matched_by must be one of {sorted(_MATCHED_BY)}, got {matched_by!r}")
        if not isinstance(capsule_id, str):
            raise _tally_error(f"{where} row {row_id!r}: a recorded row names the capsule_id that accounts for it")
    elif matched_by is not None:
        raise _tally_error(f"{where} row {row_id!r}: only a recorded row carries matched_by")
    return ReconcileRow(
        id=row_id,
        digest=json_digest(dict(raw)),
        matched_by=matched_by if recorded else None,
        capsule_id=capsule_id if recorded else None,
    )


def _rows(doc: Mapping[str, object], key: str, *, recorded: bool = False) -> tuple[ReconcileRow, ...]:
    raw = doc.get(key)
    if not isinstance(raw, list):
        raise _tally_error(f"reconcile report: {key} must be a list")
    return tuple(_row(r, key, recorded=recorded) for r in raw)


@dataclass(frozen=True)
class ReconcileReport:
    """The fields of one ``deal reconcile`` JSON output this adapter reads.
    ``coverage`` is kept verbatim so ``cannot_see`` reaches the tally."""

    period_from: str
    period_to: str
    counts: ReconcileCounts
    recorded: tuple[ReconcileRow, ...]
    unrecorded: tuple[ReconcileRow, ...]
    failed_attempts: tuple[ReconcileRow, ...]
    coverage: Mapping[str, object]

    @classmethod
    def from_json(cls, doc: Mapping[str, object]) -> ReconcileReport:
        period = doc.get("period")
        if not isinstance(period, dict) or not isinstance(period.get("from"), str) or not isinstance(period.get("to"), str):
            raise _tally_error("reconcile report: period must carry from and to")
        _check_rfc3339("period.from", period["from"])
        _check_rfc3339("period.to", period["to"])
        for key in _RECONCILE_COUNTS:
            value = doc.get(key)
            if type(value) is not int or value < 0:
                raise _tally_error(f"reconcile report: {key} must be a non-negative integer, got {value!r}")
        counts = ReconcileCounts(
            records_read=doc["records_read"],
            outside_period=doc["outside_period"],
            not_consequential=doc["not_consequential"],
            consequential=doc["consequential"],
        )
        coverage = doc.get("coverage")
        if not isinstance(coverage, dict):
            raise _tally_error("reconcile report: coverage is missing")
        cannot_see = coverage.get("cannot_see")
        if not isinstance(cannot_see, list) or not cannot_see or not all(isinstance(s, str) and s for s in cannot_see):
            raise _tally_error("reconcile report: coverage.cannot_see must be a non-empty list of strings; it is never dropped")
        report = cls(
            period_from=period["from"],
            period_to=period["to"],
            counts=counts,
            recorded=_rows(doc, "recorded", recorded=True),
            unrecorded=_rows(doc, "unrecorded"),
            failed_attempts=_rows(doc, "failed_attempts"),
            coverage=dict(coverage),
        )
        report._check_counts()
        return report

    def _check_counts(self) -> None:
        # The reconcile pass's own arithmetic: consequential = recorded +
        # unrecorded; every in-period record is consequential, failed, or not.
        consequential = len(self.recorded) + len(self.unrecorded)
        if self.counts["consequential"] != consequential:
            raise _tally_error(
                f"reconcile report: consequential is {self.counts['consequential']} but recorded + unrecorded is {consequential}"
            )
        expected_read = consequential + len(self.failed_attempts) + self.counts["not_consequential"]
        if self.counts["records_read"] != expected_read:
            raise _tally_error(
                f"reconcile report: records_read is {self.counts['records_read']} but its parts add to {expected_read}"
            )
        ids = [r.id for r in self.recorded + self.unrecorded + self.failed_attempts]
        if len(set(ids)) != len(ids):
            raise _tally_error("reconcile report: an execution record id appears in more than one row")

    @property
    def action_ids(self) -> tuple[str, ...]:
        return tuple(r.id for r in self.recorded + self.unrecorded + self.failed_attempts)


@dataclass(frozen=True)
class LayerAggregate:
    """One layer's metric. ``result`` is ``None`` only when no action was
    evaluated at this layer: Result v0 requires at least one claim, so a
    zero denominator is stated in ``coverage`` instead of hidden."""

    spec: LayerSpec
    coverage: Coverage
    result: EvidenceResult | None

    def to_dict(self) -> LayerEntry:
        out = LayerEntry(
            layer=self.spec.layer_id,
            tier=self.spec.tier,
            self_reported=self.spec.self_reported,
            coverage=CoverageCounts(
                evaluated_population=self.coverage.evaluated_population,
                excluded_not_applicable=self.coverage.excluded_not_applicable,
                unknown_count=self.coverage.unknown_count,
            ),
            result=None if self.result is None else self.result.to_dict(),
        )
        if self.spec.gate is not None:
            out["gate"] = self.spec.gate
        if self.spec.judge_pin is not None:
            out["judge_pin"] = self.spec.judge_pin
        return out


@dataclass(frozen=True)
class LayerTally:
    generated_at: str
    tool_name: str
    tool_version: str
    executions_sha256: str
    reconcile: ReconcileReport
    layers: tuple[LayerAggregate, ...]

    def to_dict(self) -> LayerTallyDoc:
        source = ReconcileSource(
            kind="deal-reconcile",
            **self.reconcile.counts,
            failed_attempts=len(self.reconcile.failed_attempts),
            executions_sha256=self.executions_sha256,
            coverage=dict(self.reconcile.coverage),
        )
        return LayerTallyDoc(
            tally_version=TALLY_VERSION,
            generated_at=self.generated_at,
            tool=TallyTool(name=self.tool_name, version=self.tool_version),
            period=TallyPeriod({"from": self.reconcile.period_from, "to": self.reconcile.period_to}),
            source=source,
            layers=[layer.to_dict() for layer in self.layers],
        )


def _invocation_claims(
    report: ReconcileReport, spec: LayerSpec, contract_ref: str, executions_sha256: str
) -> dict[str, Claim]:
    claims: dict[str, Claim] = {}

    def claim(row: ReconcileRow, sufficiency: str, verdict: str, presentation_status: str) -> Claim:
        # The executions file first: it is what the count is a lower bound
        # over. The row digest covers the whole row, capsule_id included;
        # the capsule_id is also cited on its own when it is itself a digest.
        digests = [executions_sha256, row.digest]
        if row.capsule_id is not None and _HEX64.fullmatch(row.capsule_id):
            digests.append(row.capsule_id)
        evidence = tuple(DigestRef(digest=d) for d in digests)
        if sufficiency == "SATISFIED":
            presentation: DisclosureCarrier | AnalysisCarrier = DisclosureCarrier(status=presentation_status, evidence=evidence)
        else:
            presentation = AnalysisCarrier(
                status=presentation_status,
                summary="a failed attempt, listed for review: it can still have had an effect, so whether a check was owed is not settled",
            )
        return Claim(
            id=f"{spec.layer_id}:{row.id}",
            contract_ref=contract_ref,
            requirement_ref=spec.requirement_ref,
            tier=spec.tier,
            grade="self-attested",
            sufficiency=sufficiency,
            verdict=verdict,
            evidence=evidence,
            proofs=(),
            presentation=presentation,
        )

    for row in report.recorded:
        claims[row.id] = claim(row, "SATISFIED", "met", "SATISFIED")
    for row in report.unrecorded:
        claims[row.id] = claim(row, "SATISFIED", "not_met", "SATISFIED")
    for row in report.failed_attempts:
        claims[row.id] = claim(row, "UNKNOWN", "not_evaluable", "UNKNOWN")
    return claims


def _unknown_claim(spec: LayerSpec, action_id: str, contract_ref: str, summary: str) -> Claim:
    evidence = (DigestRef(digest=spec.judge_pin),) if spec.judge_pin is not None else ()
    return Claim(
        id=f"{spec.layer_id}:{action_id}",
        contract_ref=contract_ref,
        requirement_ref=spec.requirement_ref,
        tier=spec.tier,
        grade="self-attested",
        sufficiency="UNKNOWN",
        verdict="not_evaluable",
        evidence=evidence,
        proofs=(),
        presentation=AnalysisCarrier(status="UNKNOWN", summary=summary),
    )


def _observed_claim(spec: LayerSpec, obs: LayerObservation, contract_ref: str) -> Claim:
    digests = ((spec.judge_pin,) if spec.judge_pin is not None else ()) + obs.evidence
    evidence = tuple(DigestRef(digest=d) for d in digests)
    if obs.sufficiency == "SATISFIED":
        presentation: DisclosureCarrier | AnalysisCarrier = DisclosureCarrier(status="SATISFIED", evidence=evidence)
    else:
        presentation = AnalysisCarrier(
            status=_UNSETTLED_STATUS.get(obs.sufficiency, "UNKNOWN"),
            summary=f"the layer applied and the record did not settle it (sufficiency {obs.sufficiency})",
        )
    return Claim(
        id=f"{spec.layer_id}:{obs.action_id}",
        contract_ref=contract_ref,
        requirement_ref=spec.requirement_ref,
        tier=spec.tier,
        grade=obs.grade,
        sufficiency=obs.sufficiency,
        verdict=obs.verdict,
        evidence=evidence,
        proofs=(),
        presentation=presentation,
    )


def _layer_aggregate(
    spec: LayerSpec, claims: Sequence[Claim], excluded: int, generated_at: str
) -> LayerAggregate:
    if not claims:
        return LayerAggregate(spec=spec, coverage=Coverage(0, excluded, 0), result=None)
    result = build_result(list(claims), generated_at=generated_at, excluded_not_applicable=excluded)
    return LayerAggregate(spec=spec, coverage=result.aggregate.coverage, result=result)


def build_layer_tally(
    reconcile: Mapping[str, object],
    invocation: LayerSpec,
    layers: Sequence[LayerSpec],
    observations: Sequence[LayerObservation],
    *,
    contract_ref: str,
    generated_at: str,
    tool_name: str,
    tool_version: str,
    executions_sha256: str,
    not_applicable: Collection[ActionLayer] = (),
) -> LayerTally:
    """Build one Result per layer: ``invocation`` first, read off the
    ``deal reconcile`` JSON ``reconcile``; then each of ``layers`` from
    ``observations``. ``not_applicable`` names (action, layer) pairs the
    caller saw the layer not apply to (for example, no disposition was
    returned), each counted as excluded, never as a claim.
    ``executions_sha256`` is the SHA-256 of the executions file the
    reconcile pass read; reconcile's JSON does not carry it."""
    _check_rfc3339("generated_at", generated_at)
    if not tool_name or not tool_version:
        raise _tally_error("tool_name and tool_version must be non-empty: the version under test is part of the artifact")
    if invocation.gate is not None or invocation.tier != "recomputed" or not invocation.self_reported:
        raise _tally_error("the invocation layer is recomputed from the reconcile report, self-reported, and has no gate")
    DigestRef(digest=executions_sha256)
    report = ReconcileReport.from_json(reconcile)
    actions = report.action_ids

    known = {invocation.layer_id}
    specs = {spec.layer_id: spec for spec in layers}
    for spec in layers:
        if spec.layer_id in known:
            raise _tally_error(f"layer {spec.layer_id!r} appears more than once")
        if spec.gate not in known:
            raise _tally_error(f"layer {spec.layer_id!r} must be gated on an earlier layer, got {spec.gate!r}")
        known.add(spec.layer_id)

    action_set = set(actions)
    by_pair: dict[ActionLayer, LayerObservation] = {}
    for obs in observations:
        pair = ActionLayer(obs.action_id, obs.layer_id)
        if obs.layer_id == invocation.layer_id:
            raise _tally_error("the invocation layer is read from the reconcile report, never from an observation")
        if obs.layer_id not in known or obs.action_id not in action_set:
            raise _tally_error(f"observation for {pair} names no configured layer or no consequential action")
        if pair in by_pair:
            raise _tally_error(f"two observations for {pair}")
        if specs[obs.layer_id].self_reported and obs.grade != "self-attested":
            raise _tally_error(f"{pair} is on a self-reported layer and must be self-attested, got {obs.grade!r}")
        by_pair[pair] = obs
    skipped = {ActionLayer(*p) for p in not_applicable}
    for pair in skipped:
        if pair in by_pair:
            raise _tally_error(f"{pair} is both observed and declared not applicable")
        if pair.layer_id not in known or pair.layer_id == invocation.layer_id or pair.action_id not in action_set:
            raise _tally_error(f"not_applicable entry {pair} names no gated layer or no consequential action")

    # Per action, the verdict each layer reached, or None when excluded.
    outcome: dict[str, dict[str, str | None]] = {a: {} for a in actions}
    invocation_claims = _invocation_claims(report, invocation, contract_ref, executions_sha256)
    for action_id, claim in invocation_claims.items():
        outcome[action_id][invocation.layer_id] = claim.verdict
    aggregates = [
        _layer_aggregate(
            invocation, list(invocation_claims.values()), report.counts["not_consequential"], generated_at
        )
    ]

    for spec in layers:
        claims: list[Claim] = []
        excluded = 0
        for action_id in actions:
            pair = ActionLayer(action_id, spec.layer_id)
            gate = outcome[action_id][spec.gate]
            obs = by_pair.get(pair)
            if gate is None or gate == "not_met" or pair in skipped:
                if obs is not None:
                    raise _tally_error(f"{pair} is observed, but its gate layer {spec.gate!r} did not reach it")
                outcome[action_id][spec.layer_id] = None
                excluded += 1
                continue
            if gate == "not_evaluable":
                if obs is not None:
                    raise _tally_error(f"{pair} is observed, but whether its gate layer {spec.gate!r} applied is unsettled")
                claim = _unknown_claim(
                    spec, action_id, contract_ref, f"whether this layer applied is unsettled: layer {spec.gate} did not settle"
                )
            elif obs is None:
                claim = _unknown_claim(spec, action_id, contract_ref, "no observation was recorded for this action at this layer")
            else:
                claim = _observed_claim(spec, obs, contract_ref)
            outcome[action_id][spec.layer_id] = claim.verdict
            claims.append(claim)
        aggregates.append(_layer_aggregate(spec, claims, excluded, generated_at))

    return LayerTally(
        generated_at=generated_at,
        tool_name=tool_name,
        tool_version=tool_version,
        executions_sha256=executions_sha256,
        reconcile=report,
        layers=tuple(aggregates),
    )


def _recount(claims: Sequence[Mapping[str, object]], excluded: int) -> CoverageCounts:
    return CoverageCounts(
        evaluated_population=len(claims),
        excluded_not_applicable=excluded,
        unknown_count=sum(1 for c in claims if c.get("sufficiency") == "UNKNOWN"),
    )


def verify_layer_tally(doc: Mapping[str, object]) -> None:
    """Re-check a ``layer-tally/v0`` document: each layer's Result passes
    the schema and ``verify_result``, and each layer's coverage equals the
    count of its own claims -- a hand-set ``evaluated_population`` or
    ``unknown_count`` is refused, wherever it was edited. Raises
    ``ResultError`` on the first violation."""
    if doc.get("tally_version") != TALLY_VERSION:
        raise _tally_error(f"tally_version must be {TALLY_VERSION!r}")
    generated_at = doc.get("generated_at")
    if not isinstance(generated_at, str):
        raise _tally_error("generated_at is missing")
    _check_rfc3339("generated_at", generated_at)
    tool = doc.get("tool")
    if not isinstance(tool, dict) or not tool.get("name") or not tool.get("version"):
        raise _tally_error("tool.name and tool.version are required")
    source = doc.get("source")
    coverage = source.get("coverage") if isinstance(source, dict) else None
    cannot_see = coverage.get("cannot_see") if isinstance(coverage, dict) else None
    if not isinstance(cannot_see, list) or not cannot_see:
        raise _tally_error("source.coverage.cannot_see must be carried, non-empty")
    executions = source.get("executions_sha256") if isinstance(source, dict) else None
    if not isinstance(executions, str) or not _HEX64.fullmatch(executions):
        raise _tally_error("source.executions_sha256 must be a SHA-256 hex digest")
    layers = doc.get("layers")
    if not isinstance(layers, list) or not layers:
        raise _tally_error("layers must be a non-empty list")
    seen: set[str] = set()
    for entry in layers:
        layer_id = entry.get("layer")
        if layer_id in seen:
            raise _tally_error(f"layer {layer_id!r} appears more than once")
        seen.add(layer_id)
        stated = entry.get("coverage")
        result = entry.get("result")
        if result is None:
            if not isinstance(stated, dict) or stated.get("evaluated_population") != 0 or stated.get("unknown_count") != 0:
                raise ResultError(COVERAGE_NOT_COMPUTED, f"layer {layer_id!r} has no result, so its stated population must be 0")
            continue
        validate_against_schema(result)
        verify_result(result)
        if result["generated_at"] != generated_at:
            raise _tally_error(f"layer {layer_id!r} result generated_at differs from the tally's")
        claims = result["claims"]
        # excluded_not_applicable is the one count a caller supplies (spec
        # section 3); the schema check above already made it an integer >= 0.
        recount = _recount(claims, result["aggregate"]["coverage"]["excluded_not_applicable"])
        if result["aggregate"]["coverage"] != recount or stated != recount:
            raise ResultError(
                COVERAGE_NOT_COMPUTED,
                f"layer {layer_id!r} coverage does not recount from its claims: stated {stated}, aggregate "
                f"{result['aggregate']['coverage']}, recounted {recount}",
            )
        tier = entry.get("tier")
        pin = entry.get("judge_pin")
        if tier == "judged" and not pin:
            raise _tally_error(f"layer {layer_id!r} is judged and names no judge pin")
        if tier == "recomputed" and pin is not None:
            raise _tally_error(f"layer {layer_id!r} is recomputed and carries a judge pin")
        self_reported = entry.get("self_reported")
        if not isinstance(self_reported, bool):
            raise _tally_error(f"layer {layer_id!r} must state self_reported")
        # The invocation layer is the one with no gate: it is a lower bound
        # over the executions file, which every one of its claims cites first.
        if "gate" not in entry and (not self_reported or any(
            not c["evidence"] or c["evidence"][0]["digest"] != executions for c in claims
        )):
            raise _tally_error(f"invocation layer {layer_id!r} must be self-reported and cite the executions file first")
        for claim in claims:
            if self_reported and claim["grade"] != "self-attested":
                raise _tally_error(f"claim {claim['id']!r} is on a self-reported layer and graded {claim['grade']!r}")
            if claim["tier"] != tier:
                raise _tally_error(f"claim {claim['id']!r} tier {claim['tier']!r} differs from layer tier {tier!r}")
            if pin is not None and (not claim["evidence"] or claim["evidence"][0]["digest"] != pin):
                raise _tally_error(f"claim {claim['id']!r} does not carry the layer's judge pin")


def render_layer_tally(doc: LayerTallyDoc) -> list[str]:
    """Plain lines, one per layer, each with its own denominator, the date
    and the tool version, then every ``cannot_see`` line. A layer with no
    evaluated action says so; it is never shown as a pass."""
    tool = doc["tool"]
    stamp = f"{doc['generated_at']} · {tool['name']} {tool['version']}"
    lines = []
    for entry in doc["layers"]:
        cov = entry["coverage"]
        excluded = f"{cov['excluded_not_applicable']} excluded as not applicable"
        if entry["result"] is None:
            lines.append(f"{entry['layer']}: 0 evaluated (denominator 0), no result · {excluded} · {entry['tier']} · {stamp}")
            continue
        buckets = entry["result"]["aggregate"]["buckets"]
        lines.append(
            f"{entry['layer']}: {len(buckets['met'])} met of {cov['evaluated_population']} evaluated · "
            f"{len(buckets['not_met'])} not met · {len(buckets['not_evaluable'])} not evaluable "
            f"({cov['unknown_count']} unknown) · {excluded} · {entry['tier']} · {stamp}"
        )
    lines.append("cannot see:")
    lines.extend(f"- {gap}" for gap in doc["source"]["coverage"]["cannot_see"])
    return lines
