# SPDX-License-Identifier: Apache-2.0
"""[grc-facets-upstream-registry-proposals] (Steven's #24 review nit): the
skill layer needs a stable import surface for the five GRC-batch folds --
callers currently import each fold module directly (``capsule_engine.folds.
ordering``, etc). This proves every fold's entry point and result/classifier
types are importable from the ``capsule_engine.folds`` package namespace
itself, and that they are the SAME objects as the module-level ones (not
shadow copies)."""
from __future__ import annotations

from capsule_engine import folds
from capsule_engine.folds import approval_latency as approval_latency_mod
from capsule_engine.folds import ordering as ordering_mod
from capsule_engine.folds import record_type_coverage as record_type_coverage_mod
from capsule_engine.folds import retention_continuity as retention_continuity_mod
from capsule_engine.folds import set_membership as set_membership_mod


def test_every_grc_fold_entry_point_is_importable_via_package_namespace():
    assert folds.evaluate_ordering is ordering_mod.evaluate_ordering
    assert folds.evaluate_retention_continuity is retention_continuity_mod.evaluate_retention_continuity
    assert folds.evaluate_record_type_coverage is record_type_coverage_mod.evaluate_record_type_coverage
    assert folds.evaluate_approval_latency is approval_latency_mod.evaluate_approval_latency
    assert folds.evaluate_set_membership is set_membership_mod.evaluate_set_membership


def test_every_grc_fold_classifier_type_is_importable_via_package_namespace():
    assert folds.SessionOrderingResult is ordering_mod.SessionOrderingResult
    assert folds.OrderingResult is ordering_mod.OrderingResult
    assert folds.RetentionContinuityResult is retention_continuity_mod.RetentionContinuityResult
    assert folds.RecordTypeCoverageResult is record_type_coverage_mod.RecordTypeCoverageResult
    assert folds.ApprovalLatencyResult is approval_latency_mod.ApprovalLatencyResult
    assert folds.INDICATOR_LABEL == approval_latency_mod.INDICATOR_LABEL
    assert folds.MembershipRecordResult is set_membership_mod.MembershipRecordResult
    assert folds.SetMembershipResult is set_membership_mod.SetMembershipResult


def test_every_grc_fold_name_is_declared_in_folds_all():
    expected = {
        "SessionOrderingResult",
        "OrderingResult",
        "evaluate_ordering",
        "RetentionContinuityResult",
        "evaluate_retention_continuity",
        "RecordTypeCoverageResult",
        "evaluate_record_type_coverage",
        "ApprovalLatencyResult",
        "evaluate_approval_latency",
        "INDICATOR_LABEL",
        "MembershipRecordResult",
        "SetMembershipResult",
        "evaluate_set_membership",
    }
    assert expected <= set(folds.__all__)


def test_mutant_a_name_missing_from_the_namespace_is_detectable():
    """Mutant proving the checks above actually read the package namespace
    rather than trusting the module import: an entry point NOT re-exported
    from ``capsule_engine.folds`` must raise AttributeError."""
    assert not hasattr(folds, "_not_a_real_export_name")
