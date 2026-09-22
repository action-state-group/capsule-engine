# SPDX-License-Identifier: Apache-2.0
"""Which TRACE v0.2 record field(s) answer each of this pack's outcomes, and
the rule each resolved value must satisfy -- the human-authored bridge
between ``register.yaml``'s clause-anchored statements and TRACE's own field
names. Deliberately NOT part of ``pack.yaml``/``schema.EvidenceContract``:
this mapping is specific to ONE evidence source (TRACE) among however many a
pack outcome could someday be checked against, so it lives beside the pack
rather than inside the pack-schema surface every source would otherwise have
to share.

``resolve`` reads each field via a literal attribute chain on the verified
``TrustRecord`` -- never a dotted string walked with ``getattr`` -- so a typo
here is an ``AttributeError`` a test catches, not a silent ``None``.

Outcomes with no entry here (``EU-75``, ``EU-04``, ``EU-50-1``) are the
honest majority: TRACE v0.2 carries hardware/model/policy attestation, not
document-by-digest artifacts or session-level disclosure ordering, so
``trace_attainment.build_attainment_report`` reports them ``not_present``
without ever reaching this module.
"""
from __future__ import annotations

from .trace_attainment import TraceFieldCheck

EU_AI_ACT_TRACE_FIELD_MAP: dict[str, TraceFieldCheck] = {
    "EU-53": TraceFieldCheck(
        field_names=("model.version", "model.aibom_uri"),
        resolve=lambda record: (record.model.version, record.model.aibom_uri),
        rule=lambda values: all(v is not None for v in values),
        description="model.version and model.aibom_uri both present -- the epoch cites the version actually "
        "used and where its documentation lives",
    ),
    "EU-14-3": TraceFieldCheck(
        field_names=("policy.enforcement_mode",),
        resolve=lambda record: (record.policy.enforcement_mode,),
        # "declared" is agentrust-trace's own weakest value (models.py's
        # PolicyInfo.enforcement_mode docstring): the policy is named and
        # bound into the record, but nothing evaluated it. Any of the other
        # three values asserts SOME evaluation happened, which is the
        # honest floor for "oversight operated" -- see that docstring for
        # why "declared" must never be read as evidence a rule was checked.
        rule=lambda values: values[0] != "declared",
        description="policy.enforcement_mode names an evaluation that actually ran, not merely a bound-but-"
        "unevaluated policy",
    ),
}
