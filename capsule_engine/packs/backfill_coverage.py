# SPDX-License-Identifier: Apache-2.0
"""Backfill provenance mode: per-requirement evidence
sufficiency over a capsule stream that may mix contemporaneous and
backfilled records (AAC -05 ``draft-mih-scitt-agent-action-capsule-05``
§provenancemode, "Provenance mode and backfilled records").

Two rules from that section, mechanically enforced here rather than left to
a caller to remember (RULED 2026-09-22, decisions-log §6.4):

1. **Status cap.** A backfilled record's occurrence-time claim can never
   satisfy a requirement whose ``minimum_assurance`` includes
   ``"committed"`` -- log-witnessed time assurance -- unless a
   ``references[]`` entry cites, by digest, a corroborating witnessed
   timestamp (``citation_purpose: "corroborates_source_time"``). The cap is
   REDERIVED from that evidence, never trusted off a claimed
   ``provenance_mode.time_rung`` field, mirroring
   ``agent_action_capsule.verify``'s own check 9 discipline.
2. **Duplicates count once.** A record chained ``chain.relation:
   "duplicates"`` to a parent present in the same stream is excluded from
   independent counting; the contemporaneous parent's own status governs.

**Statuses use the schema's own vocabulary, not the fold verdict
vocabulary.** ``schemas/evidence-contract-v0.json``'s ``bundleAssertionStatus``
(``SATISFIED``/``INSUFFICIENT``/``NOT_FOUND``/...) is about **evidence
sufficiency and availability** -- the question this module answers.
``met``/``not_met``/``insufficient_evidence`` (``folds/ordering.py`` and
siblings) is the DIFFERENT, judged-OUTCOME axis (``evidence-contract-
internal-spec-v3.md`` §8) and is not reused here.

**Trusted-dict input, same as every fold in this package.** This module
re-derives only the ``provenance_mode``/duplicate facts it needs directly
off already-verified capsule dicts (the ``folds/engine.py`` assumption);
it does not re-run full structural verification via
``agent_action_capsule.verify_store``, which requires complete,
cryptographically valid capsules and is redundant with verification already
done upstream at ingest.

**Scope, held hard.** This is a coverage/sufficiency primitive for ONE
requirement's already-identified candidate evidence (the ``matches``
predicate) -- not a general Evidence Contract evaluator that resolves which
records answer which requirement (no such engine exists in this package
today; see the GRC folds -- ``ordering.py``, ``retention_continuity.py``,
``set_membership.py``, ``record_type_coverage.py`` -- for the same
narrow-primitive-over-monolith precedent).
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from agent_action_capsule.canonical import FloatInDigestError, UnsafeIntegerError, json_digest
from agent_action_capsule.contracts import TIME_RUNGS  # noqa: F401 -- re-exported for callers

__all__ = [
    "STATUS_SATISFIED",
    "STATUS_INSUFFICIENT",
    "STATUS_NOT_FOUND",
    "MODE_CONTEMPORANEOUS",
    "MODE_BACKFILLED",
    "RequirementCoverageResult",
    "evaluate_requirement_coverage",
]

# schemas/evidence-contract-v0.json $defs.bundleAssertionStatus (the three
# values a coverage/sufficiency check can produce; SATISFIED/GAP/UNKNOWN's
# other bundle-level siblings -- NOT_COMMITTED, WITHHELD, CONTRADICTED,
# NOT_APPLICABLE, UNKNOWN -- are a different check's business, not this one's).
STATUS_SATISFIED = "SATISFIED"
STATUS_INSUFFICIENT = "INSUFFICIENT"
STATUS_NOT_FOUND = "NOT_FOUND"

MODE_CONTEMPORANEOUS = "contemporaneous"
MODE_BACKFILLED = "backfilled"

# AAC -05 §provenancemode; REGISTRY.md §11 -- the one citation_purpose that
# can raise a backfilled record's time_rung above self_attested.
_CORROBORATES_SOURCE_TIME = "corroborates_source_time"


def _record_id(record: Mapping[str, Any]) -> str:
    """A real capsule's own ``capsule_id`` when present, else a content
    digest over the record -- mirrors ``folds/engine.py``'s
    ``_record_identity`` exactly, so a synthetic/fixture record with no
    ``capsule_id`` still gets a stable, deterministic identity rather than
    ``None``."""
    capsule_id = record.get("capsule_id")
    if isinstance(capsule_id, str) and capsule_id:
        return capsule_id
    try:
        return json_digest(dict(record))
    except (FloatInDigestError, UnsafeIntegerError):
        return hashlib.sha256(json.dumps(dict(record), sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _provenance_mode(record: Mapping[str, Any]) -> str:
    pm = record.get("provenance_mode")
    if isinstance(pm, Mapping) and pm.get("mode") in (MODE_CONTEMPORANEOUS, MODE_BACKFILLED):
        return pm["mode"]
    return MODE_CONTEMPORANEOUS  # absent block => contemporaneous (AAC -05 §provenancemode)


def _has_witnessed_corroboration(record: Mapping[str, Any]) -> bool:
    refs = record.get("references")
    if not isinstance(refs, list):
        return False
    for r in refs:
        if (
            isinstance(r, Mapping)
            and r.get("citation_purpose") == _CORROBORATES_SOURCE_TIME
            and isinstance(r.get("type"), str) and r.get("type")
            and isinstance(r.get("digest_alg"), str) and r.get("digest_alg")
            and isinstance(r.get("digest"), str) and r.get("digest")
        ):
            return True
    return False


def _time_rung(record: Mapping[str, Any]) -> str:
    """``"witnessed"`` for a contemporaneous record (``sealed_at`` IS the
    witnessable time, AAC -05 §provenancemode "Time semantics, normative");
    for a backfilled record, ``"witnessed"`` only with a well-formed
    corroborating reference, else ``"self_attested"`` -- never trusts a
    claimed ``provenance_mode.time_rung`` by itself."""
    if _provenance_mode(record) != MODE_BACKFILLED:
        return "witnessed"
    return "witnessed" if _has_witnessed_corroboration(record) else "self_attested"


def _duplicate_parent_id(record: Mapping[str, Any]) -> str | None:
    chain = record.get("chain")
    if isinstance(chain, Mapping) and chain.get("relation") == "duplicates":
        parent = chain.get("parent_capsule_id")
        if isinstance(parent, str) and parent:
            return parent
    return None


@dataclass(frozen=True)
class RequirementCoverageResult:
    status: str  # STATUS_SATISFIED | STATUS_INSUFFICIENT | STATUS_NOT_FOUND
    detail: str
    considered_count: int
    matched_count: int  # candidates before duplicate collapse
    duplicates_collapsed_count: int
    contemporaneous_count: int  # surviving, post-collapse
    backfilled_count: int  # surviving, post-collapse
    matched_capsule_ids: tuple[str, ...]


def evaluate_requirement_coverage(
    records: list[dict],
    *,
    matches: Callable[[dict], bool],
    minimum_assurance: frozenset[str] = frozenset(),
) -> RequirementCoverageResult:
    """Walk ``records`` (ledger order, never re-sorted -- same discipline
    ``folds/engine.py`` documents), select the ``matches`` predicate's
    candidates, collapse ``chain.relation: "duplicates"`` pairs to their
    contemporaneous parent, then report sufficiency.

    ``minimum_assurance`` containing ``"committed"`` applies the status cap:
    a requirement needing Committed-or-higher assurance is
    ``STATUS_INSUFFICIENT`` when every surviving candidate is a backfilled
    record capped at ``self_attested`` -- evidence exists, it just cannot
    clear the bar, which is a different, more specific fact than
    ``STATUS_NOT_FOUND`` (no evidence at all).

    ``matches`` is REQUIRED, no default, the same explicit-injection
    convention ``folds/ordering.py``'s ``before``/``after`` and
    ``packs/measurability_report.py``'s ``entity_key`` use: this module has
    no built-in notion of which records answer which requirement.
    """
    candidates = [r for r in records if matches(r)]
    by_id = {rid: r for r in records if (rid := _record_id(r)) is not None}

    collapsed = 0
    surviving: list[dict] = []
    for record in candidates:
        parent_id = _duplicate_parent_id(record)
        if parent_id is not None and parent_id in by_id:
            collapsed += 1
            continue
        surviving.append(record)

    contemporaneous = [r for r in surviving if _provenance_mode(r) == MODE_CONTEMPORANEOUS]
    backfilled = [r for r in surviving if _provenance_mode(r) == MODE_BACKFILLED]

    if not surviving:
        return RequirementCoverageResult(
            status=STATUS_NOT_FOUND,
            detail="no evidence record satisfies this requirement's matcher",
            considered_count=len(records),
            matched_count=len(candidates),
            duplicates_collapsed_count=collapsed,
            contemporaneous_count=0,
            backfilled_count=0,
            matched_capsule_ids=(),
        )

    if "committed" in minimum_assurance:
        committed_eligible = [r for r in surviving if _time_rung(r) == "witnessed"]
        if not committed_eligible:
            return RequirementCoverageResult(
                status=STATUS_INSUFFICIENT,
                detail=(
                    f"{len(backfilled)} backfilled record(s) satisfy this requirement's matcher, "
                    "but a Committed-or-higher requirement needs log-witnessed time assurance; a "
                    "backfilled record's occurrence-time claim is capped at self_attested unless a "
                    "references[] entry cites a corroborating witnessed timestamp by digest "
                    "(citation_purpose=corroborates_source_time) -- none is present here "
                    "(draft-mih-scitt-agent-action-capsule-05 §provenancemode, 'Status cap')"
                ),
                considered_count=len(records),
                matched_count=len(candidates),
                duplicates_collapsed_count=collapsed,
                contemporaneous_count=len(contemporaneous),
                backfilled_count=len(backfilled),
                matched_capsule_ids=tuple(_record_id(r) for r in surviving),
            )

    return RequirementCoverageResult(
        status=STATUS_SATISFIED,
        detail=f"{len(contemporaneous)} contemporaneous + {len(backfilled)} backfilled record(s) satisfy this requirement",
        considered_count=len(records),
        matched_count=len(candidates),
        duplicates_collapsed_count=collapsed,
        contemporaneous_count=len(contemporaneous),
        backfilled_count=len(backfilled),
        matched_capsule_ids=tuple(_record_id(r) for r in surviving),
    )
