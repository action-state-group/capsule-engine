# SPDX-License-Identifier: Apache-2.0
"""eu-ai-act pack ([ldg-eu-ai-act-pack]): loads clean, tiers only Art 5/50/26
rows must_have, and the measurability report over the tau2 corpus + an
Amplifier-shaped sample makes the honest MISSING INSTRUMENT claim on most
T4 rows a mechanically-checked fact rather than an assertion in a
docstring -- the acceptance check the task specified, run as a test so it
can't silently go stale.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from capsule_engine.packs.corpus_verify import verify_declared_not_measured
from capsule_engine.packs.loader import load_pack_dir
from capsule_engine.packs.measurability_report import (
    STATUS_MISSING_INSTRUMENT,
    STATUS_RESOLVES,
    build_measurability_report,
    entity_key_field,
)

PACK_DIR = Path(__file__).parent.parent / "capsule_engine" / "packs" / "catalog" / "eu-ai-act"
TAU2_CORPUS = (
    Path(__file__).parent.parent
    / "capsule_engine"
    / "examples"
    / "data"
    / "tau2_airline"
    / "tau2_conversations_claude-3-7-sonnet_airline_4trials.jsonl"
)
AMPLIFIER_CORPUS = PACK_DIR / "fixtures" / "amplifier_shaped_sample.jsonl"

MUST_HAVE_IDS = {
    "EU-05a", "EU-05b", "EU-05c", "EU-05d",
    "EU-50-1", "EU-50-2", "EU-50-3", "EU-50-4",
    "EU-26-1", "EU-26-2", "EU-26-4", "EU-26-5", "EU-26-6", "EU-26-7", "EU-26-11",
}
CONTESTED_IDS = {"EU-49", "EU-72", "EU-73", "EU-86"}
JUDGED_IDS = {"EU-05a", "EU-05b", "EU-26-4"}


def _load_jsonl(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def test_pack_loads_and_declares_37_outcomes():
    pack = load_pack_dir(PACK_DIR)
    assert pack.pack_id == "asg/eu-ai-act/1.0.0"
    assert len(pack.outcomes) == 37
    assert len({o.id for o in pack.outcomes}) == 37  # no duplicate ids


def test_tier_must_have_is_exactly_the_art_5_50_26_rows():
    pack = load_pack_dir(PACK_DIR)
    must_have = {o.id for o in pack.outcomes if o.tier == "must_have"}
    assert must_have == MUST_HAVE_IDS
    for outcome in pack.outcomes:
        if outcome.id in MUST_HAVE_IDS:
            assert outcome.clause.article in ("Article 5", "Article 50", "Article 26")


def test_contested_flag_is_exactly_the_four_t3_rows_the_report_names():
    pack = load_pack_dir(PACK_DIR)
    contested = {o.id for o in pack.outcomes if o.clause and o.clause.contested}
    assert contested == CONTESTED_IDS


def test_every_outcome_carries_a_structured_clause_with_effective_from():
    pack = load_pack_dir(PACK_DIR)
    for outcome in pack.outcomes:
        assert outcome.clause is not None, outcome.id
        assert outcome.clause.instrument == "Regulation (EU) 2024/1689"
        assert outcome.clause.effective_from is not None, outcome.id


def test_judged_rows_declare_no_evidence_instrument():
    """[measurability_report.py]'s own documented convention: mode=judged
    rows need no instrument, an LLM judge reads the sealed prose every
    corpus already carries -- this pack must not fabricate one just to
    force a resolves/missing_instrument verdict on a judged row."""
    pack = load_pack_dir(PACK_DIR)
    for outcome in pack.outcomes:
        if outcome.mode == "judged":
            assert outcome.id in JUDGED_IDS
            assert outcome.measurability == "measured"
            assert outcome.evidence_instrument is None


def test_declared_not_measured_claims_are_honest_on_the_tau2_corpus():
    """The oracle cross-check: every declared_not_measured outcome's
    evidence_instrument must genuinely NOT resolve on the real tau2 corpus
    -- proving the claim rather than trusting it (raises
    CorpusVerificationError the moment one resolves)."""
    pack = load_pack_dir(PACK_DIR)
    units = _load_jsonl(TAU2_CORPUS)
    verify_declared_not_measured(pack, units)  # no raise == every claim holds


def test_declared_not_measured_claims_are_honest_on_the_amplifier_sample():
    pack = load_pack_dir(PACK_DIR)
    units = _load_jsonl(AMPLIFIER_CORPUS)
    verify_declared_not_measured(pack, units)


@pytest.mark.parametrize("corpus_path,entity_field", [(TAU2_CORPUS, "task_id"), (AMPLIFIER_CORPUS, "session_id")])
def test_measurability_report_resolves_only_the_judged_rows(corpus_path, entity_field):
    """The task's own acceptance check, made mechanical: over the tau2
    corpus AND an Amplifier-shaped sample, only the JUDGED rows resolve --
    every structural/value row (most of them T4) reports MISSING
    INSTRUMENT naming its facet. That is the deliverable, not a defect."""
    pack = load_pack_dir(PACK_DIR)
    units = _load_jsonl(corpus_path)
    rows = build_measurability_report(pack, units, entity_key=entity_key_field(entity_field))
    assert len(rows) == 37
    resolved_ids = {r.outcome_id for r in rows if r.status == STATUS_RESOLVES}
    missing_ids = {r.outcome_id for r in rows if r.status == STATUS_MISSING_INSTRUMENT}
    assert resolved_ids == JUDGED_IDS
    assert missing_ids == {o.id for o in pack.outcomes} - JUDGED_IDS


def test_most_t4_rows_report_missing_instrument_on_the_tau2_corpus():
    """T4 (Annex III high-risk, effective 2027-12-02) is the tranche the
    task called out by name: 19 of its 20 rows (all but the one JUDGED
    row, EU-26-4) are expected to report MISSING INSTRUMENT."""
    pack = load_pack_dir(PACK_DIR)
    t4_ids = {o.id for o in pack.outcomes if o.clause.effective_from == "2027-12-02"}
    assert len(t4_ids) == 20
    units = _load_jsonl(TAU2_CORPUS)
    rows = build_measurability_report(pack, units, entity_key=entity_key_field("task_id"))
    t4_missing = {r.outcome_id for r in rows if r.outcome_id in t4_ids and r.status == STATUS_MISSING_INSTRUMENT}
    assert t4_missing == t4_ids - JUDGED_IDS
    assert len(t4_missing) == 19
