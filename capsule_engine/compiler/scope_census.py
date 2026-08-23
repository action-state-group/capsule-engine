# SPDX-License-Identifier: Apache-2.0
"""Scope census (T2): *"this pack covers N of the M outcomes/obligations in
document D."* Design §3.1/§4/§4b gap 3 -- ruled mandatory, not subject to
the doc-3 admission test at all, because it is the entire regulatory
question for obligations and the auditor's first question for outcomes.
Without it, a producer who submits 40 of 60 clauses gets a clean report on
40 and the compiler cannot see the 20 it was never shown.

Two objects, deliberately not one. ``packs/schema.py``'s ``ScopeCensus`` is
the pack-declared CLAIM (what a ``pack.yaml`` states). This module seals
the recorded ACT -- the T2 human sign-off on that claim, at a point in
time -- as a capsule, because "N of M" is worthless as an assertion no one
signed. ``review_by`` carries the census's own expiry: a census with no
freshness turns "is M still M" into an unanswerable question in year two,
exactly as inadmissible as never having had one. Re-census is a new T2
event, never an edit to the old one.
"""
from __future__ import annotations

from agent_action_capsule.contracts import is_hex64

from capsule_engine.guards.capsule import build_event_capsule
from capsule_engine.guards.signing import Signer

__all__ = ["EVENT_SCOPE_CENSUS", "build_scope_census_capsule"]

EVENT_SCOPE_CENSUS = "compiler.scope_census"


def build_scope_census_capsule(
    *,
    document_digest: str,
    n: int,
    m: int,
    review_by: str,
    operator: str,
    developer: str,
    signer: Signer,
    timestamp: str | None = None,
    action_id: str | None = None,
    chain_parent: str | None = None,
) -> dict:
    """Seal a T2 scope-census sign-off: ``n`` of ``m`` statements in the
    document identified by ``document_digest`` are covered, reviewable
    again no later than ``review_by`` (an ISO-8601 date/datetime string --
    validated as non-empty here; calendar semantics belong to whoever reads
    it, same as every other timestamp field this codebase passes through).
    ``chain_parent`` cites the prior census this one supersedes, if any --
    the lineage a re-census event is required to leave (module docstring).
    """
    if not is_hex64(document_digest):
        raise ValueError(f"document_digest must be a 64-hex SHA-256 digest; got {document_digest!r}")
    if not isinstance(n, int) or isinstance(n, bool) or n < 0:
        raise ValueError(f"n must be a non-negative integer; got {n!r}")
    if not isinstance(m, int) or isinstance(m, bool) or m < 1:
        raise ValueError(f"m must be a positive integer (a census over zero statements is not a census); got {m!r}")
    if n > m:
        raise ValueError(f"n ({n}) must not exceed m ({m}) -- covering more statements than the document has")
    if not review_by:
        raise ValueError("review_by is required -- a scope census with no freshness date is inadmissible in year two")

    detail = {"document_digest": document_digest, "n": n, "m": m, "review_by": review_by}
    return build_event_capsule(
        operator=operator,
        developer=developer,
        signer=signer,
        event=EVENT_SCOPE_CENSUS,
        detail=detail,
        timestamp=timestamp,
        action_id=action_id or f"compiler.scope_census/{document_digest}",
        chain_parent=chain_parent,
        chain_relation="follows" if chain_parent else None,
    )
