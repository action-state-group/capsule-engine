# SPDX-License-Identifier: Apache-2.0
"""The ``trace-record/1`` reader plus
the EU AI Act obligations pack, run end to end over TRACE v0.2 fixtures.

``requires_agentrust_trace`` skips only the tests that actually read or
verify a TRACE record -- neither the reader's own module nor this test
file's collection needs ``agentrust-trace`` (the ``trace`` extra) at import
time (local import, see ``trace_reader.py``). ``test_pack_loads`` and
``test_pack_outcomes_match_the_compiled_register_exactly`` exercise only
``load_pack_dir``/``load_register_file``/``EvidenceCompiler`` -- none of
which touch TRACE at all -- so they run (and the pack/register drift check
they perform stays live) even without the extra installed.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from capsule_engine.packs.eu_ai_act_trace_field_map import EU_AI_ACT_TRACE_FIELD_MAP
from capsule_engine.packs.loader import load_pack_dir
from capsule_engine.packs.trace_attainment import (
    STATUS_ESTABLISHED,
    STATUS_FAILED,
    STATUS_NOT_PRESENT,
    TraceFieldCheck,
    build_attainment_report,
    render_terminal,
)
from capsule_engine.packs.trace_reader import (
    READ_FAILED,
    READ_VERIFIED,
    SOURCE_TRACE,
    read_trace_record,
)
from capsule_engine.register.compiler import EvidenceCompiler
from capsule_engine.register.loader import load_register_file

requires_agentrust_trace = pytest.mark.skipif(
    importlib.util.find_spec("agentrust_trace") is None,
    reason="agentrust-trace (the trace extra) is not installed",
)

PACK_DIR = Path(__file__).parent.parent / "capsule_engine" / "packs" / "catalog" / "eu-ai-act"
FIXTURES_DIR = PACK_DIR / "fixtures" / "trace"


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES_DIR / name).read_text())


def _trusted_jwk() -> dict:
    return _load_fixture("trusted_issuer.jwk.json")


# --------------------------------------------------------------------------- pack loads + recompiles cleanly


def test_pack_loads():
    pack = load_pack_dir(PACK_DIR)
    assert pack.pack_id == "asg/eu-ai-act-obligations/0.1.1"
    # Re-pinned at asg/eu-ai-act-obligations/0.1.1: the four epistemic_type
    # values were lower-cased (no other field or structure change). 0.1.0
    # pinned to 4871978b789e3cc01776369fb9671c5008ebdc9cacf36dab21d70d518bc00265.
    assert pack.definition_digest() == "9d3197935b6b002ccd4682c7cfa2bacf01d9dbc2b3b04811e94dd50e6a2b9cfa"
    assert {o.id for o in pack.outcomes} == {"EU-53", "EU-14-3", "EU-75", "EU-04", "EU-50-1"}


def test_pack_outcomes_match_the_compiled_register_exactly():
    """pack.yaml's outcomes[] is a transcribed EvidenceCompiler candidate
    (register/compiler.py's own docstring) -- this test IS the drift check:
    if a human hand-edits pack.yaml without re-running the compiler, or
    edits register.yaml without re-transcribing, this fails."""
    pack = load_pack_dir(PACK_DIR)
    register = load_register_file(PACK_DIR / "register.yaml")
    compiler = EvidenceCompiler()

    pack_by_id = {o.id: o.canonical_dict() for o in pack.outcomes}
    compiled_by_id = {row.id: compiler.compile_requirement(row).canonical_dict() for row in register.rows}

    # EU-50-1 carries tier=must_have in pack.yaml, a human confirmation the
    # compiler's own default ("informational") does not make -- excluded
    # from the byte-for-byte comparison and checked separately below.
    assert pack_by_id["EU-50-1"]["tier"] == "must_have"
    del pack_by_id["EU-50-1"]["tier"]

    assert pack_by_id == compiled_by_id


# --------------------------------------------------------------------------- trace_reader.read_trace_record


@requires_agentrust_trace
def test_read_trace_record_verified():
    record = _load_fixture("good_execution.json")
    result = read_trace_record(record, _trusted_jwk())
    assert result.status == READ_VERIFIED
    assert result.record is not None
    assert result.record.model.model_id == record["model"]["model_id"]
    assert result.record.policy.enforcement_mode == record["policy"]["enforcement_mode"]


@requires_agentrust_trace
def test_read_trace_record_tampered_fails_closed():
    """The core dependency this whole reader stands on: a record whose bytes
    were altered after signing must NOT verify, and the failure must name a
    signature problem, not a schema coincidence."""
    tampered = _load_fixture("tampered_execution.json")
    result = read_trace_record(tampered, _trusted_jwk())
    assert result.status == READ_FAILED
    assert result.record is None
    assert "InvalidSignature" in result.detail


@requires_agentrust_trace
def test_read_trace_record_schema_invalid_also_fails_closed():
    """Schema violations are caught by the same verifier, distinct from a
    signature failure -- exercised here with a record missing a required
    top-level field entirely (no signature to even check)."""
    broken = {**_load_fixture("good_execution.json")}
    del broken["build_provenance"]
    result = read_trace_record(broken, _trusted_jwk())
    assert result.status == READ_FAILED
    assert result.record is None


# --------------------------------------------------------------------------- build_attainment_report: the happy path


@requires_agentrust_trace
def test_attainment_report_over_a_verified_record():
    pack = load_pack_dir(PACK_DIR)
    read = read_trace_record(_load_fixture("good_execution.json"), _trusted_jwk())
    rows = build_attainment_report(pack, read, EU_AI_ACT_TRACE_FIELD_MAP)
    by_id = {r.outcome_id: r for r in rows}

    assert by_id["EU-53"].status == STATUS_ESTABLISHED
    assert by_id["EU-14-3"].status == STATUS_ESTABLISHED  # enforcement_mode="enforce"
    # No TRACE-side mapping declared at all for these three -- honest
    # not_present, never inferred.
    assert by_id["EU-75"].status == STATUS_NOT_PRESENT
    assert by_id["EU-04"].status == STATUS_NOT_PRESENT
    assert by_id["EU-50-1"].status == STATUS_NOT_PRESENT
    assert all(r.source == SOURCE_TRACE for r in rows)
    assert len(rows) == len(pack.outcomes)


@requires_agentrust_trace
def test_attainment_report_rule_violation_is_failed_not_not_present():
    """A verified record whose declared field genuinely violates the rule
    (oversight bound but never evaluated) is FAILED -- a real negative
    finding -- never softened to not_present just because the record itself
    checked out."""
    pack = load_pack_dir(PACK_DIR)
    read = read_trace_record(_load_fixture("oversight_declared.json"), _trusted_jwk())
    assert read.status == READ_VERIFIED  # this record is honestly signed; only its content fails the rule
    rows = build_attainment_report(pack, read, EU_AI_ACT_TRACE_FIELD_MAP)
    by_id = {r.outcome_id: r for r in rows}

    assert by_id["EU-14-3"].status == STATUS_FAILED
    assert "declared" not in by_id["EU-14-3"].detail or "resolved value" in by_id["EU-14-3"].detail
    # EU-53's mapping is untouched by this fixture's change -- still established.
    assert by_id["EU-53"].status == STATUS_ESTABLISHED


@requires_agentrust_trace
def test_field_absent_on_a_verified_record_is_not_present_not_failed():
    """EU-75 has no mapping (asserted above); this test proves the OTHER
    absence path -- a mapped outcome whose field genuinely isn't on this
    particular verified record -- also renders not_present, by wiring a
    fresh mapping at a field this pack's fixtures never set."""
    pack = load_pack_dir(PACK_DIR)
    read = read_trace_record(_load_fixture("good_execution.json"), _trusted_jwk())
    field_map = {
        "EU-53": TraceFieldCheck(
            field_names=("transparency",),
            resolve=lambda record: (record.transparency,),
            rule=lambda values: values[0] is not None,
            description="transparency present",
        )
    }
    rows = build_attainment_report(pack, read, field_map)
    row = next(r for r in rows if r.outcome_id == "EU-53")
    assert row.status == STATUS_NOT_PRESENT
    assert row.status != STATUS_FAILED


# --------------------------------------------------------------------------- the negative test the task asks for by name


@requires_agentrust_trace
def test_tampered_record_reports_failed_never_not_present():
    """ACCEPTANCE: a tampered TRACE record's own verifier fails, and every
    outcome mapped to it reports `failed` -- not `not_present`. Reporting
    not_present here would say "we looked, cleanly, and TRACE has nothing to
    say" for a record that is actually untrustworthy, which is the specific
    dishonesty this whole reader exists to refuse.

    Both halves of the mutant: mapped outcomes escalate to FAILED (this is
    what changed); unmapped outcomes are untouched and stay NOT_PRESENT
    (this is what must NOT change -- an outcome nothing was ever going to
    check against TRACE is not made less-checked by TRACE being untrustworthy).
    """
    pack = load_pack_dir(PACK_DIR)
    read = read_trace_record(_load_fixture("tampered_execution.json"), _trusted_jwk())
    assert read.status == READ_FAILED

    rows = build_attainment_report(pack, read, EU_AI_ACT_TRACE_FIELD_MAP)
    by_id = {r.outcome_id: r for r in rows}

    # Mapped outcomes: FAILED, never NOT_PRESENT.
    assert by_id["EU-53"].status == STATUS_FAILED
    assert by_id["EU-14-3"].status == STATUS_FAILED
    for outcome_id in ("EU-53", "EU-14-3"):
        assert by_id[outcome_id].status != STATUS_NOT_PRESENT
        assert "verification" in by_id[outcome_id].detail or "InvalidSignature" in by_id[outcome_id].detail

    # Unmapped outcomes: unaffected by the failed read, still NOT_PRESENT.
    for outcome_id in ("EU-75", "EU-04", "EU-50-1"):
        assert by_id[outcome_id].status == STATUS_NOT_PRESENT


# --------------------------------------------------------------------------- render_terminal smoke test


@requires_agentrust_trace
def test_render_terminal_includes_every_row_and_its_source():
    pack = load_pack_dir(PACK_DIR)
    read = read_trace_record(_load_fixture("good_execution.json"), _trusted_jwk())
    rows = build_attainment_report(pack, read, EU_AI_ACT_TRACE_FIELD_MAP)
    text = render_terminal(rows)
    for row in rows:
        assert row.outcome_id in text
    assert text.count(f"source={SOURCE_TRACE}") == len(rows)
