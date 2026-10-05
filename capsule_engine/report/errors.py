# SPDX-License-Identifier: Apache-2.0
"""Named-reason errors for Result v0 construction (mirrors
``packs/errors.py``'s ``PackDefinitionError`` discipline: a stable reason
code for tests/tooling, a message that names the field and what was
expected).
"""
from __future__ import annotations

INVALID_HEX_DIGEST = "invalid_hex_digest"
INVALID_TIER = "invalid_result_tier"
INVALID_GRADE = "invalid_result_grade"
INVALID_SUFFICIENCY = "invalid_sufficiency"
INVALID_VERDICT = "invalid_verdict"
SUFFICIENCY_VERDICT_MISMATCH = "sufficiency_verdict_mismatch"
INVALID_DISCLOSED_STATUS = "invalid_disclosed_status"
INVALID_EVIDENCE_STATUS = "invalid_evidence_status"
DISCLOSURE_NOT_LEGAL_FOR_STATUS = "disclosure_not_legal_for_status"
DUPLICATE_CLAIM_ID = "duplicate_claim_id"
BUCKET_CLAIM_MISMATCH = "bucket_claim_mismatch"
COVERAGE_CLAIM_MISMATCH = "coverage_claim_mismatch"
SCHEMA_VALIDATION_FAILED = "schema_validation_failed"
INVALID_COVERAGE_REPORT = "invalid_coverage_report"


class ResultError(ValueError):
    """A Result v0 document, or one of its parts, fails to validate --
    "an untiered claim is a bug here, not a rendering problem".
    Always fail-closed at
    construction time, same discipline as ``PackDefinitionError``."""

    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        super().__init__(f"{reason}: {message}")
