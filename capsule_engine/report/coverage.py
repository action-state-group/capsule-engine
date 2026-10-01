# SPDX-License-Identifier: Apache-2.0
"""Coverage report per requirement -- the Result v0 ``coverage_report``
section.

For each requirement of one Evidence Contract version, this module answers
three questions from the records actually in the ledger:

1. **What evidence exists** -- per ``evidence_requirements.required_sources``
   entry: how many records, how many contemporaneous vs backfilled (after
   ``chain.relation: "duplicates"`` collapse), how many distinct producers,
   and the record digests.
2. **What is missing** -- a ``gaps[]`` list: a required source with no
   record, a source whose records cannot clear ``minimum_assurance``, an
   independence demand met only by one producer, or a requirement that
   declares no sources at all.
3. **Which source would close it** -- each gap names the source and, when
   the caller's remedy catalog has an entry, the connector class that would
   capture it and the assurance mode that connector raises the source to.
   A gap with no catalog entry carries ``remedy: null`` and is counted in
   ``summary.gaps_without_remedy`` -- never silently dropped.

**Same-producer records are correlation, not corroboration.** Many records
from one producer -- a run of OpenTelemetry spans exported by one
deployment, an effect record plus the span that observed it -- show that one
party saw the same thing several times. They raise ``correlated_records``;
they never raise ``independent_producers``. A requirement whose
``independence`` field asks for corroboration is met only by evidence from
at least that many distinct producers.

**Who the producer is.** For each requirement, if every attributed record
carries a signer ``key_id`` (the local envelope field next to
``signature``), the producer is the key id and ``producer_basis`` is
``"key"``. Otherwise the producer is the capsule's self-asserted
``operator`` + ``developer`` strings and ``producer_basis`` is
``"asserted"``: anyone can write those strings, so an asserted producer is
NOT authenticated, and a renderer should say so. A key id binds records to
one key, but it does not show that two keys belong to two parties: AAC's
``kid`` is self-attested, and a producer can mint a second key. One basis is
used per requirement, so a producer that signs some records and not others
is never counted twice. A caller-supplied ``producer_of`` is always
``"asserted"``.

**Unattributed records count toward no producer.** A record with no
``key_id``, ``operator`` or ``developer`` is counted in
``unattributed_records`` and never raises ``independent_producers``. Evidence
that is entirely unattributed is ``INSUFFICIENT`` with an
``unattributed_only`` gap, even when no independence is asked for: nobody is
named as having produced it.

**Per-source sufficiency reuses** ``packs.backfill_coverage``'s
``evaluate_requirement_coverage`` unchanged (duplicate collapse and the
backfilled-record cap below ``committed``), one call per required source.

**Statuses use the bundle-assertion vocabulary** (``SATISFIED`` /
``INSUFFICIENT`` / ``NOT_FOUND`` / ``UNKNOWN``), and each requirement also
carries the contract-result ``sufficiency`` derived from it by the fixed
mapping in ``STATUS_TO_SUFFICIENCY``. Neither is a verdict: coverage says
whether evidence is there to evaluate, not what the evaluation found.

**Which record answers which source is injected.** ``source_of`` (record ->
source name or ``None``) is REQUIRED, the same explicit-injection convention
``backfill_coverage``'s ``matches`` uses; ``producer_of`` defaults to the
key-or-asserted rule above. No producer identity is written
into the report -- only counts and record digests. A source row's optional
``epistemic_type`` comes from a caller-supplied source catalog; nothing here
infers it from the records.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Hashable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from agent_action_capsule.canonical import FloatInDigestError, UnsafeIntegerError, json_digest

from ..packs.backfill_coverage import (
    STATUS_INSUFFICIENT,
    STATUS_NOT_FOUND,
    STATUS_SATISFIED,
    RequirementCoverageResult,
    _record_id,
    evaluate_requirement_coverage,
)
from ..packs.schema import EPISTEMIC_TYPE_VALUES
from .errors import INVALID_COVERAGE_REPORT, ResultError
from .result import Claim, DigestRef

__all__ = [
    "COVERAGE_REPORT_VERSION",
    "CONNECTOR_VALUES",
    "RAISES_TO_VALUES",
    "GAP_KIND_VALUES",
    "PRODUCER_BASIS_VALUES",
    "STATUS_UNKNOWN",
    "STATUS_TO_SUFFICIENCY",
    "Remedy",
    "SourceCoverage",
    "Independence",
    "Gap",
    "RequirementCoverage",
    "CoverageSummary",
    "CoverageReport",
    "default_producer_of",
    "required_producers",
    "build_coverage_report",
    "verify_coverage_report",
]

COVERAGE_REPORT_VERSION = "coverage-report/v0"

# Hook classes and the assurance mode each raises a source to.
CONNECTOR_VALUES = frozenset(
    {"otel", "mcp_proxy", "gateway", "git_ci", "system_of_record", "native_emission", "human_approval"}
)
RAISES_TO_VALUES = frozenset({"retrospectively_evidenced", "observed", "committed"})

GAP_MISSING_SOURCE = "missing_source"
GAP_ASSURANCE_BELOW_MINIMUM = "assurance_below_minimum"
GAP_CORRELATED_ONLY = "correlated_only"
GAP_UNATTRIBUTED_ONLY = "unattributed_only"
GAP_NO_SOURCES_DECLARED = "no_sources_declared"
GAP_KIND_VALUES = frozenset(
    {GAP_MISSING_SOURCE, GAP_ASSURANCE_BELOW_MINIMUM, GAP_CORRELATED_ONLY, GAP_UNATTRIBUTED_ONLY, GAP_NO_SOURCES_DECLARED}
)

PRODUCER_BASIS_VALUES = frozenset({"key", "asserted", "none"})

STATUS_UNKNOWN = "UNKNOWN"
_STATUS_VALUES = frozenset({STATUS_SATISFIED, STATUS_INSUFFICIENT, STATUS_NOT_FOUND, STATUS_UNKNOWN})

# Bundle-assertion status -> contract-result sufficiency (NOT_FOUND is a gap;
# INSUFFICIENT stays insufficient; UNKNOWN stays unknown).
STATUS_TO_SUFFICIENCY = {
    STATUS_SATISFIED: "SATISFIED",
    STATUS_NOT_FOUND: "GAP",
    STATUS_INSUFFICIENT: "INSUFFICIENT",
    STATUS_UNKNOWN: "UNKNOWN",
}

# Worst first: one missing source makes the whole requirement NOT_FOUND.
_STATUS_ORDER = (STATUS_NOT_FOUND, STATUS_INSUFFICIENT, STATUS_UNKNOWN, STATUS_SATISFIED)

_HEX64 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class Remedy:
    connector: str
    raises_to: str

    def __post_init__(self) -> None:
        if self.connector not in CONNECTOR_VALUES:
            raise ResultError(INVALID_COVERAGE_REPORT, f"remedy connector must be one of {sorted(CONNECTOR_VALUES)}, got {self.connector!r}")
        if self.raises_to not in RAISES_TO_VALUES:
            raise ResultError(INVALID_COVERAGE_REPORT, f"remedy raises_to must be one of {sorted(RAISES_TO_VALUES)}, got {self.raises_to!r}")

    def to_dict(self) -> dict:
        return {"connector": self.connector, "raises_to": self.raises_to}


@dataclass(frozen=True)
class SourceCoverage:
    source: str
    status: str
    record_count: int  # surviving, post-collapse
    contemporaneous_count: int
    backfilled_count: int
    duplicates_collapsed: int
    producer_count: int
    evidence: tuple[DigestRef, ...]
    epistemic_type: str | None = None  # declared by the caller's source catalog, never inferred

    def __post_init__(self) -> None:
        if self.epistemic_type is not None and self.epistemic_type not in EPISTEMIC_TYPE_VALUES:
            raise ResultError(
                INVALID_COVERAGE_REPORT,
                f"source {self.source!r} epistemic_type must be one of {sorted(EPISTEMIC_TYPE_VALUES)}, got {self.epistemic_type!r}",
            )

    def to_dict(self) -> dict:
        out: dict[str, Any] = {"source": self.source}
        if self.epistemic_type is not None:
            out["epistemic_type"] = self.epistemic_type
        return out | {
            "status": self.status,
            "record_count": self.record_count,
            "contemporaneous_count": self.contemporaneous_count,
            "backfilled_count": self.backfilled_count,
            "duplicates_collapsed": self.duplicates_collapsed,
            "producer_count": self.producer_count,
            "evidence": [e.to_dict() for e in self.evidence],
        }


@dataclass(frozen=True)
class Independence:
    required_producers: int
    independent_producers: int
    correlated_records: int  # records beyond the first from each producer
    unattributed_records: int  # records naming no producer; never counted toward one
    producer_basis: str  # "key" | "asserted" | "none" (no producer counted)
    met: bool

    def __post_init__(self) -> None:
        if self.producer_basis not in PRODUCER_BASIS_VALUES:
            raise ResultError(INVALID_COVERAGE_REPORT, f"producer_basis must be one of {sorted(PRODUCER_BASIS_VALUES)}, got {self.producer_basis!r}")

    def to_dict(self) -> dict:
        return {
            "required_producers": self.required_producers,
            "independent_producers": self.independent_producers,
            "correlated_records": self.correlated_records,
            "unattributed_records": self.unattributed_records,
            "producer_basis": self.producer_basis,
            "met": self.met,
        }


@dataclass(frozen=True)
class Gap:
    kind: str
    detail: str
    source: str | None = None
    remedy: Remedy | None = None

    def __post_init__(self) -> None:
        if self.kind not in GAP_KIND_VALUES:
            raise ResultError(INVALID_COVERAGE_REPORT, f"gap kind must be one of {sorted(GAP_KIND_VALUES)}, got {self.kind!r}")

    def to_dict(self) -> dict:
        out: dict[str, Any] = {"kind": self.kind}
        if self.source is not None:
            out["source"] = self.source
        out["detail"] = self.detail
        out["remedy"] = self.remedy.to_dict() if self.remedy is not None else None
        return out


@dataclass(frozen=True)
class RequirementCoverage:
    requirement_ref: str
    obligation_refs: tuple[str, ...]
    status: str
    claim_ids: tuple[str, ...]
    sources: tuple[SourceCoverage, ...]
    independence: Independence
    gaps: tuple[Gap, ...]

    @property
    def sufficiency(self) -> str:
        return STATUS_TO_SUFFICIENCY[self.status]

    def to_dict(self) -> dict:
        return {
            "requirement_ref": self.requirement_ref,
            "obligation_refs": list(self.obligation_refs),
            "status": self.status,
            "sufficiency": self.sufficiency,
            "claim_ids": list(self.claim_ids),
            "sources": [s.to_dict() for s in self.sources],
            "independence": self.independence.to_dict(),
            "gaps": [g.to_dict() for g in self.gaps],
        }


@dataclass(frozen=True)
class CoverageSummary:
    requirements: int
    satisfied: int
    with_gaps: int
    gaps: int
    gaps_without_remedy: int

    def to_dict(self) -> dict:
        return {
            "requirements": self.requirements,
            "satisfied": self.satisfied,
            "with_gaps": self.with_gaps,
            "gaps": self.gaps,
            "gaps_without_remedy": self.gaps_without_remedy,
        }


@dataclass(frozen=True)
class CoverageReport:
    contract_ref: str
    requirements: tuple[RequirementCoverage, ...]

    @property
    def summary(self) -> CoverageSummary:
        return _summarize(self.requirements)

    def to_dict(self) -> dict:
        return {
            "spec_version": COVERAGE_REPORT_VERSION,
            "contract_ref": self.contract_ref,
            "requirements": [r.to_dict() for r in self.requirements],
            "summary": self.summary.to_dict(),
        }


def _summarize(requirements: Sequence[RequirementCoverage]) -> CoverageSummary:
    all_gaps = [g for r in requirements for g in r.gaps]
    return CoverageSummary(
        requirements=len(requirements),
        satisfied=sum(1 for r in requirements if r.status == STATUS_SATISFIED),
        with_gaps=sum(1 for r in requirements if r.gaps),
        gaps=len(all_gaps),
        gaps_without_remedy=sum(1 for g in all_gaps if g.remedy is None),
    )


def _key_id(record: Mapping[str, Any]) -> str | None:
    key_id = record.get("key_id")
    return key_id if isinstance(key_id, str) and key_id else None


def _asserted(record: Mapping[str, Any]) -> tuple[Any, Any] | None:
    operator = record.get("operator")
    developer = record.get("developer")
    if operator is None and developer is None:
        return None
    return (operator, developer)


def default_producer_of(record: Mapping[str, Any]) -> Hashable | None:
    """One record's producer on its own: the signer ``key_id`` when present,
    else the asserted ``operator`` + ``developer`` pair, else ``None``
    (unattributed -- counts toward no producer). ``build_coverage_report``
    applies one basis per requirement (see the module docstring); this is the
    single-record view of the same rule."""
    key_id = _key_id(record)
    if key_id is not None:
        return ("key", key_id)
    asserted = _asserted(record)
    return None if asserted is None else ("asserted", asserted)


def _producer_rule(
    records: Sequence[Mapping[str, Any]],
    producer_of: Callable[[Mapping[str, Any]], Hashable | None] | None,
) -> tuple[Callable[[Mapping[str, Any]], Hashable | None], str]:
    """The producer function and its basis for one requirement's records."""
    if producer_of is not None:
        return producer_of, "asserted"
    attributed = [r for r in records if _key_id(r) is not None or _asserted(r) is not None]
    if attributed and all(_key_id(r) is not None for r in attributed):
        return _key_id, "key"

    def asserted_or_key(r: Mapping[str, Any]) -> Hashable | None:
        asserted = _asserted(r)
        if asserted is not None:
            return ("asserted", asserted)
        key_id = _key_id(r)  # a keyed record with no operator/developer stays its key's
        return None if key_id is None else ("key", key_id)

    return asserted_or_key, "asserted"


def _record_digest(record: Mapping[str, Any]) -> str:
    """The record's ``capsule_id`` when it is a 64-hex digest, else a
    content digest over the record -- always a valid ``DigestRef``."""
    capsule_id = record.get("capsule_id")
    if isinstance(capsule_id, str) and _HEX64.match(capsule_id):
        return capsule_id
    try:
        return json_digest(dict(record))
    except (FloatInDigestError, UnsafeIntegerError):
        return hashlib.sha256(json.dumps(dict(record), sort_keys=True, default=str).encode("utf-8")).hexdigest()


def required_producers(independence: str | None) -> int:
    """How many distinct producers a requirement's ``independence`` field
    asks for. Absent or ``"none"`` -> 1. A bare integer -> that integer
    (minimum 1). Any other declared value -> 2: the field exists to ask for
    corroboration, and corroboration needs a second party."""
    if independence is None:
        return 1
    value = independence.strip().lower()
    if value in ("", "none"):
        return 1
    if value.isdigit():
        return max(1, int(value))
    return 2


def _source_records(
    source: str,
    records: list[dict],
    *,
    source_of: Callable[[dict], str | None],
    minimum_assurance: frozenset[str],
) -> tuple[RequirementCoverageResult, list[dict]]:
    result = evaluate_requirement_coverage(
        records, matches=lambda r: source_of(r) == source, minimum_assurance=minimum_assurance
    )
    surviving_ids = set(result.matched_capsule_ids)
    surviving = [r for r in records if source_of(r) == source and _record_id(r) in surviving_ids]
    return result, surviving


def _requirement_coverage(
    requirement: Mapping[str, Any],
    records: list[dict],
    claims: Sequence[Claim],
    *,
    source_of: Callable[[dict], str | None],
    producer_of: Callable[[Mapping[str, Any]], Hashable | None] | None,
    remedies: Mapping[str, Remedy],
    corroboration_remedy: Remedy | None,
    source_catalog: Mapping[str, str],
) -> RequirementCoverage:
    req_id = requirement["id"]
    ev = requirement.get("evidence_requirements") or {}
    declared = list(dict.fromkeys(ev.get("required_sources") or ()))
    minimum_assurance = frozenset(ev.get("minimum_assurance") or ())
    need = required_producers(ev.get("independence"))
    claim_ids = tuple(c.id for c in claims if c.requirement_ref == req_id)
    obligation_refs = tuple(requirement.get("obligation_refs") or ())
    if requirement.get("clause_ref"):
        obligation_refs = obligation_refs + (requirement["clause_ref"],)

    if not declared:
        return RequirementCoverage(
            requirement_ref=req_id,
            obligation_refs=obligation_refs,
            status=STATUS_UNKNOWN,
            claim_ids=claim_ids,
            sources=(),
            independence=Independence(
                required_producers=need,
                independent_producers=0,
                correlated_records=0,
                unattributed_records=0,
                producer_basis="none",
                met=False,
            ),
            gaps=(
                Gap(
                    kind=GAP_NO_SOURCES_DECLARED,
                    detail="the requirement declares no required_sources, so coverage cannot be computed; name the sources in the contract",
                ),
            ),
        )

    per_source = [
        (source, *_source_records(source, records, source_of=source_of, minimum_assurance=minimum_assurance))
        for source in declared
    ]
    # One record can answer two sources; count it once.
    evidence: dict[str, dict] = {}
    for _, _, surviving in per_source:
        for record in surviving:
            evidence.setdefault(_record_id(record), record)
    producer, basis = _producer_rule(list(evidence.values()), producer_of)

    sources: list[SourceCoverage] = []
    gaps: list[Gap] = []
    for source, result, surviving in per_source:
        coverage = SourceCoverage(
            source=source,
            status=result.status,
            record_count=len(surviving),
            contemporaneous_count=result.contemporaneous_count,
            backfilled_count=result.backfilled_count,
            duplicates_collapsed=result.duplicates_collapsed_count,
            producer_count=len({p for r in surviving if (p := producer(r)) is not None}),
            evidence=tuple(DigestRef(digest=_record_digest(r)) for r in surviving),
            epistemic_type=source_catalog.get(source),
        )
        sources.append(coverage)
        if coverage.status == STATUS_NOT_FOUND:
            gaps.append(
                Gap(
                    kind=GAP_MISSING_SOURCE,
                    source=source,
                    detail=f"no record from source {source!r} is in the evaluated records",
                    remedy=remedies.get(source),
                )
            )
        elif coverage.status == STATUS_INSUFFICIENT:
            gaps.append(
                Gap(
                    kind=GAP_ASSURANCE_BELOW_MINIMUM,
                    source=source,
                    detail=(
                        f"{coverage.backfilled_count} backfilled record(s) from {source!r} cannot clear "
                        "minimum_assurance 'committed': source time is self-attested and no reference "
                        "corroborates it; capture this source live"
                    ),
                    remedy=remedies.get(source),
                )
            )

    # Correlation vs corroboration: count producers, never records; an
    # unattributed record counts toward no producer at all.
    per_producer: dict[Hashable, int] = {}
    unattributed = 0
    for record in evidence.values():
        key = producer(record)
        if key is None:
            unattributed += 1
            continue
        per_producer[key] = per_producer.get(key, 0) + 1
    independent = len(per_producer)
    correlated = sum(n - 1 for n in per_producer.values())
    met = independent >= need
    independence = Independence(
        required_producers=need,
        independent_producers=independent,
        correlated_records=correlated,
        unattributed_records=unattributed,
        producer_basis=basis if independent else "none",
        met=met,
    )
    if not met and evidence:
        if independent == 0:
            gaps.append(
                Gap(
                    kind=GAP_UNATTRIBUTED_ONLY,
                    detail=(
                        f"all {unattributed} record(s) name no producer (no key_id, operator or developer); "
                        "unattributed records count toward no producer, so nobody is named as having "
                        "produced this evidence; capture the source with producer attribution"
                    ),
                    remedy=corroboration_remedy,
                )
            )
        else:
            gaps.append(
                Gap(
                    kind=GAP_CORRELATED_ONLY,
                    detail=(
                        f"the requirement asks for {need} independent producer(s); its "
                        f"{len(evidence)} record(s) come from {independent} producer(s)"
                        + (f" plus {unattributed} unattributed record(s)" if unattributed else "")
                        + " -- records from one producer correlate, they do not corroborate; "
                        "add a source another party produces"
                    ),
                    remedy=corroboration_remedy,
                )
            )

    statuses = [s.status for s in sources]
    if not met and evidence:
        statuses.append(STATUS_INSUFFICIENT)
    status = next(s for s in _STATUS_ORDER if s in statuses) if statuses else STATUS_UNKNOWN
    return RequirementCoverage(
        requirement_ref=req_id,
        obligation_refs=obligation_refs,
        status=status,
        claim_ids=claim_ids,
        sources=tuple(sources),
        independence=independence,
        gaps=tuple(gaps),
    )


def build_coverage_report(
    contract: Mapping[str, Any],
    records: list[dict],
    *,
    source_of: Callable[[dict], str | None],
    claims: Sequence[Claim] = (),
    producer_of: Callable[[Mapping[str, Any]], Hashable | None] | None = None,
    remedies: Mapping[str, Remedy] | None = None,
    corroboration_remedy: Remedy | None = None,
    source_catalog: Mapping[str, str] | None = None,
) -> CoverageReport:
    """Compute the coverage report for every requirement of ``contract``
    over ``records`` (ledger order). ``claims`` are the Result's claims;
    each requirement lists the ids of those whose ``requirement_ref``
    matches it. ``remedies`` maps a source name to the connector that would
    capture it; a gap on a source with no entry gets ``remedy: null``.
    ``corroboration_remedy`` is the remedy named on a ``correlated_only``
    gap (the connector that would bring in a second producer), if any.
    ``source_catalog`` maps a source name to its declared epistemic type
    (one of ``packs.schema.EPISTEMIC_TYPE_VALUES``, compared
    case-insensitively); a source row carries
    ``epistemic_type`` only when the catalog names it."""
    contract_ref = f"{contract['id']}@{contract['version']}"
    for claim in claims:
        if claim.contract_ref != contract_ref:
            raise ResultError(
                INVALID_COVERAGE_REPORT,
                f"claim {claim.id!r} is for {claim.contract_ref!r}, not this report's contract {contract_ref!r}",
            )
    remedies = remedies or {}
    # Case-insensitive: the owning EvidenceBook list is lowercase, the
    # Evidence Contract and the Result spell it uppercase (an open spelling
    # question); either is accepted and the Result carries uppercase.
    source_catalog = {k: v.upper() if isinstance(v, str) else v for k, v in (source_catalog or {}).items()}
    for source, epistemic_type in source_catalog.items():
        if epistemic_type not in EPISTEMIC_TYPE_VALUES:
            raise ResultError(
                INVALID_COVERAGE_REPORT,
                f"source catalog types {source!r} as {epistemic_type!r}, not one of {sorted(EPISTEMIC_TYPE_VALUES)}",
            )
    requirements = tuple(
        _requirement_coverage(
            req,
            records,
            claims,
            source_of=source_of,
            producer_of=producer_of,
            remedies=remedies,
            corroboration_remedy=corroboration_remedy,
            source_catalog=source_catalog,
        )
        for req in contract["requirements"]
    )
    return CoverageReport(contract_ref=contract_ref, requirements=requirements)


def verify_coverage_report(doc: Mapping[str, Any], claims_by_id: Mapping[str, Mapping[str, Any]]) -> None:
    """Cross-element checks a schema cannot express: every ``claim_ids``
    entry names a claim with the same ``requirement_ref`` and
    ``contract_ref``; ``sufficiency`` matches ``status`` by the fixed
    mapping; each source's counts add up; independent producers plus
    correlated plus unattributed records equal the distinct records;
    ``producer_basis`` is ``"none"`` exactly when no producer was counted;
    ``independence.met`` agrees with the producer counts; and ``summary``
    recomputes from the requirement rows. Raises ``ResultError`` on the first
    violation.

    **What this cannot catch.** The report carries producer counts, never
    producer identities (by design: no identity goes into a disclosed
    document), and ``required_producers`` as the producer computed it. So a
    hand edit that raises ``independent_producers`` and lowers
    ``correlated_records`` by the same amount, or that lowers
    ``required_producers``, passes this check. Only a recompute from the
    records and the contract (``build_coverage_report``) detects it."""
    contract_ref = doc.get("contract_ref")
    rows = doc.get("requirements", [])
    gaps_total = 0
    gaps_without_remedy = 0
    satisfied = 0
    with_gaps = 0
    for row in rows:
        ref = row.get("requirement_ref")
        status = row.get("status")
        if status not in _STATUS_VALUES:
            raise ResultError(INVALID_COVERAGE_REPORT, f"requirement {ref!r} status {status!r} is not a coverage status")
        if row.get("sufficiency") != STATUS_TO_SUFFICIENCY[status]:
            raise ResultError(
                INVALID_COVERAGE_REPORT,
                f"requirement {ref!r} status {status!r} requires sufficiency {STATUS_TO_SUFFICIENCY[status]!r}, got {row.get('sufficiency')!r}",
            )
        for claim_id in row.get("claim_ids", []):
            claim = claims_by_id.get(claim_id)
            if claim is None:
                raise ResultError(INVALID_COVERAGE_REPORT, f"requirement {ref!r} names claim {claim_id!r}, which has no claim")
            if claim.get("requirement_ref") != ref or claim.get("contract_ref") != contract_ref:
                raise ResultError(
                    INVALID_COVERAGE_REPORT,
                    f"claim {claim_id!r} is for {claim.get('contract_ref')!r}/{claim.get('requirement_ref')!r}, not {contract_ref!r}/{ref!r}",
                )
        for src in row.get("sources", []):
            if src.get("contemporaneous_count", 0) + src.get("backfilled_count", 0) != src.get("record_count"):
                raise ResultError(
                    INVALID_COVERAGE_REPORT,
                    f"requirement {ref!r} source {src.get('source')!r}: contemporaneous + backfilled != record_count",
                )
            if len(src.get("evidence", [])) != src.get("record_count"):
                raise ResultError(
                    INVALID_COVERAGE_REPORT,
                    f"requirement {ref!r} source {src.get('source')!r}: evidence digests != record_count",
                )
            if (src.get("status") == STATUS_NOT_FOUND) != (src.get("record_count") == 0):
                raise ResultError(
                    INVALID_COVERAGE_REPORT,
                    f"requirement {ref!r} source {src.get('source')!r}: NOT_FOUND exactly when record_count is 0",
                )
        ind = row.get("independence", {})
        distinct = {e.get("digest") for src in row.get("sources", []) for e in src.get("evidence", [])}
        counted = ind.get("independent_producers", 0) + ind.get("correlated_records", 0) + ind.get("unattributed_records", 0)
        if counted != len(distinct):
            raise ResultError(
                INVALID_COVERAGE_REPORT,
                f"requirement {ref!r}: independent_producers + correlated_records + unattributed_records != {len(distinct)} distinct records",
            )
        if ind.get("producer_basis") not in PRODUCER_BASIS_VALUES or (
            (ind.get("producer_basis") == "none") != (ind.get("independent_producers", 0) == 0)
        ):
            raise ResultError(
                INVALID_COVERAGE_REPORT,
                f"requirement {ref!r}: producer_basis {ind.get('producer_basis')!r} disagrees with independent_producers",
            )
        if ind.get("met") !=(ind.get("independent_producers", 0) >= ind.get("required_producers", 1)):
            raise ResultError(INVALID_COVERAGE_REPORT, f"requirement {ref!r}: independence.met disagrees with its producer counts")
        if status == STATUS_SATISFIED and (row.get("gaps") or not ind.get("met")):
            raise ResultError(INVALID_COVERAGE_REPORT, f"requirement {ref!r} is SATISFIED but has gaps or unmet independence")
        gaps = row.get("gaps", [])
        gaps_total += len(gaps)
        gaps_without_remedy += sum(1 for g in gaps if g.get("remedy") is None)
        satisfied += status == STATUS_SATISFIED
        with_gaps += bool(gaps)
    expected = {
        "requirements": len(rows),
        "satisfied": satisfied,
        "with_gaps": with_gaps,
        "gaps": gaps_total,
        "gaps_without_remedy": gaps_without_remedy,
    }
    if doc.get("summary") != expected:
        raise ResultError(INVALID_COVERAGE_REPORT, f"summary {doc.get('summary')!r} does not recompute; expected {expected!r}")
