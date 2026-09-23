# SPDX-License-Identifier: Apache-2.0
"""Dry-run report artifact: replay a ledger through the guard API in dry-run
mode and render a self-contained, fragment-carried HTML report.

See ``capsule_ledger.report.build.build_dry_run_report`` for the entry point and
``capsule_ledger.report.render.render_report_html`` for the artifact renderer.

``result.py`` is a separate, unrelated artifact despite living in the same
package: the public Evidence Result v0 emitter (``report`` -> ``result``
projection, [batch4-result-emission-from-engine]) -- see that module's
docstring. It shares no code with the dry-run report above; both simply
report on this engine's own decisions/folds.
"""
from .build import build_dry_run_report, build_dry_run_report_with_proposal
from .model import DryRunReport, GuardSection, ModelNote, ReportRow
from .render import encode_fragment, render_report_html
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
    "render_report_html",
    "encode_fragment",
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
]
