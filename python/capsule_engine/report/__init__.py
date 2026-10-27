# SPDX-License-Identifier: Apache-2.0
"""Dry-run report: replay a ledger through the guard API in dry-run mode and
return the report as data (``to_payload`` is its JSON form). The engine draws no
page; rendering belongs to capsule-viewer.

See ``capsule_engine.report.build.build_dry_run_report`` for the entry point.

``result.py`` is a separate, unrelated artifact despite living in the same
package: the public Evidence Result v0 emitter (``report`` -> ``result``
projection) -- see that module's
docstring. It shares no code with the dry-run report above; both simply
report on this engine's own decisions/folds. ``coverage.py`` builds the
Result's optional per-requirement ``coverage_report`` section.
"""
from .build import build_dry_run_report, build_dry_run_report_with_proposal
from .coverage import CoverageReport, Remedy, build_coverage_report
from .model import DryRunReport, GuardSection, ModelNote, ReportRow, to_payload
from .result import (
    Aggregate,
    AnalysisCarrier,
    Buckets,
    Claim,
    Coverage,
    DigestRef,
    DisclosureCarrier,
    EvidenceResult,
    ProofRef,
    StoryCarrier,
    View,
    build_result,
    validate_against_schema,
    verify_result,
)

__all__ = [
    "build_dry_run_report",
    "build_dry_run_report_with_proposal",
    "DryRunReport",
    "GuardSection",
    "ModelNote",
    "ReportRow",
    "to_payload",
    "Aggregate",
    "AnalysisCarrier",
    "Buckets",
    "Claim",
    "Coverage",
    "DigestRef",
    "DisclosureCarrier",
    "EvidenceResult",
    "ProofRef",
    "StoryCarrier",
    "View",
    "build_result",
    "validate_against_schema",
    "verify_result",
    "CoverageReport",
    "Remedy",
    "build_coverage_report",
]
