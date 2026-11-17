# SPDX-License-Identifier: Apache-2.0
"""The countersign module through capsule-anchor's real ``recompute_statement``
(capsule-engine#294): the results must validate as anchor's CheckResult, use its
five words, and be named after the record kinds so ``profile_conformance`` counts
them as covered. Skipped when capsule-anchor is not installed (CI installs it)."""
from __future__ import annotations

import pytest

pytest.importorskip("capsule_anchor")

from capsule_anchor.countersign.bundle import BundleRefused, parse_bundle  # noqa: E402
from capsule_anchor.countersign.recompute import recompute_statement  # noqa: E402
from capsule_anchor.countersign.results import RESULTS  # noqa: E402
from test_countersign_modules import _disclosed  # noqa: E402

from capsule_engine.countersign import CountersignPackModule, DisclosureRefused  # noqa: E402

PROFILE = "asg/outcomes/1"


def _statement(raw, *, kinds=frozenset({"declared_count"}), parse=True):
    from capsule_anchor.countersign.bundle import Bundle, bundle_digest

    bundle = parse_bundle(raw, disclosable_kinds=kinds) if parse else Bundle(raw=raw, digest=bundle_digest(raw))
    return recompute_statement(bundle, policy_module=CountersignPackModule(), ledger_id="ledger", profile_id=PROFILE)


def _checks(statement):
    return {c.name: c for c in statement.checks}


def _raw(payload, **kw):
    raw = _disclosed(payload, **kw)
    raw.update({"bundle_version": "2", "bundle_kind": "evidence-bundle/v2", "root": "r", "completeness": {"payloads_mode": "none"}})
    return raw


def test_matching_disclosure_is_established_and_kinds_are_covered():
    checks = _checks(_statement(_raw({"declared_count": 4})))
    assert checks["declared_count"].result == "established"
    assert checks["method_freeze"].result == "established"
    # the two kinds this module owns are covered; the judged kind is not ours
    assert checks["profile conformance"].detail == "action_type(s) with no policy-module coverage: evaluation-report"
    assert all(c.result in RESULTS for c in checks.values())


def test_differing_count_is_failed_without_the_numbers():
    c = _checks(_statement(_raw({"declared_count": 3})))["declared_count"]
    assert c.result == "failed"
    # the count itself never leaves the countersigner (ruling (b)); detail says only that it differs
    assert c.detail == "the declared count differs from the certified sequence span"


def test_forged_disclosure_is_refused_by_the_engine():
    with pytest.raises(BundleRefused, match="digest"):
        _statement(_raw({"declared_count": 4}, tamper=True))


def test_forged_disclosure_is_refused_by_the_module_when_the_engine_is_bypassed():
    with pytest.raises(DisclosureRefused, match="digest"):
        _statement(_raw({"declared_count": 4}, tamper=True), parse=False)


def test_signed_bytes_and_detail_carry_no_count():
    # ruling (b): only the countersigner sees the declared count. The statement's
    # canonical bytes (signed and registered) and every check's detail omit it.
    statement = _statement(_raw({"declared_count": 777}))
    assert _checks(statement)["declared_count"].result == "failed"
    assert b"777" not in statement.canonical_bytes()
    assert "777" not in str(statement.model_dump())
