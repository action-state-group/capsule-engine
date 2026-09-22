# SPDX-License-Identifier: Apache-2.0
"""``trace-record/1``: read a foreign TRACE v0.2 Trust Record (agentrust-io/
trace-spec) as backward evidence for a pack's ``EvidenceContract`` outcomes.

**Why this exists** (`[ldg-obligations-pack-reads-trace]`): the format-agnostic
read side is a real capability only if a pack can consume TRACE evidence
without asking the producer to reshape it into an Agent Action Capsule first.
This module is the read path: it does not grade TRACE, does not claim
conformance, and computes nothing the record does not itself support.

**Depend on their verifier, never re-implement it.** Schema validation and
Ed25519 signature verification are both ``agentrust_trace.sign.verify_record``
(PyPI ``agentrust-trace``) -- the same standing rule
``capsule-emit-mesh``'s ``trace_citation.py`` already established for this
package. The import is local to ``read_trace_record`` so a deployment that
never reads a TRACE record does not need ``agentrust-trace`` installed at all
(mirrors ``trace_citation._check_record_signature``).

**The one invariant this module exists to hold**: a record that fails its own
verifier is not "no evidence" -- it is untrustworthy evidence, and reporting
it as ``not_present`` would say "we looked, cleanly, and found nothing" for a
case that is actually "we looked and the thing we found cannot be trusted".
``read_trace_record`` therefore returns a value, never partial data: either
the whole verified record, or a failure with no record attached at all, so a
caller physically cannot read a field off a record that didn't verify.

**Typed, not a bare dict, once verified.** ``TraceReadResult.record`` is an
``agentrust_trace.models.TrustRecord`` -- the package's own typed model, not
a re-declared shadow of it -- so a caller reads real, named fields
(``record.model.version``, ``record.policy.enforcement_mode``, ...) instead
of hoping a key is spelled right in an untyped dict. Building it via
``TrustRecord.model_validate`` after ``verify_record`` succeeds also runs
the model's OWN business-rule validators (e.g. ``_origin_cannot_claim_hardware``)
that the jsonschema-only check inside ``verify_record`` does not encode --
folded into the same fail-closed ``READ_FAILED`` path, since a record their
own model rejects is exactly as untrustworthy as one their signature check
rejects. Only ``raw`` (the wire JSON before any of this has run) stays an
untyped ``dict`` -- there is no typed shape to give data nobody has
validated yet, and ``agentrust_trace.sign.verify_record`` itself takes the
same untyped ``record: dict[str, Any]`` for the same reason.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agentrust_trace.models import TrustRecord

__all__ = [
    "SOURCE_TRACE",
    "READ_VERIFIED",
    "READ_FAILED",
    "TraceReadResult",
    "read_trace_record",
]

# The source label every finding derived from this reader carries, so a
# report never blurs which format an item came from (task brief item 1).
SOURCE_TRACE = "trace"

READ_VERIFIED = "verified"
READ_FAILED = "failed"


@dataclass(frozen=True)
class TraceReadResult:
    """The outcome of reading one TRACE record. ``record`` is the verified,
    typed ``TrustRecord`` when ``status == READ_VERIFIED``, and ``None``
    otherwise -- never a record a caller could mistake for a checked one."""

    status: str  # READ_VERIFIED | READ_FAILED
    detail: str
    record: TrustRecord | None = None


def read_trace_record(raw: dict[str, Any], trusted_key: Any) -> TraceReadResult:
    """Verify *raw* against the TRACE v0.2 schema and *trusted_key*'s
    signature via ``agentrust_trace.sign.verify_record`` -- schema first,
    signature second, exactly as that function itself orders them -- then
    parse it into a typed ``TrustRecord`` via ``TrustRecord.model_validate``,
    which runs the model's own additional business-rule validators.

    Never raises: every rejection (malformed record, schema violation,
    invalid signature, wrong/missing profile, or a model-level business-rule
    violation) becomes a ``READ_FAILED`` result carrying the exception's own
    message, the same "a result, never a propagated exception" discipline
    ``trace_citation.grade_tee_citation`` already uses for this package.

    ``max_age_seconds=None``: a Trust Record is commonly read long after it
    was minted (that is the point of citing/reading it instead of re-minting
    it) -- staleness is a separate, orthogonal policy this reader does not
    impose, the same reasoning ``trace_citation._check_record_signature``
    gives for the same parameter.
    """
    from agentrust_trace.models import TrustRecord
    from agentrust_trace.sign import verify_record

    try:
        verify_record(raw, trusted_key, max_age_seconds=None)
        record = TrustRecord.model_validate(raw)
    except Exception as exc:  # noqa: BLE001 - a result, not a propagated exception
        return TraceReadResult(READ_FAILED, f"{type(exc).__name__}: {exc}")
    return TraceReadResult(READ_VERIFIED, "record schema, signature, and model validation all verify", record=record)
