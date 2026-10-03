# SPDX-License-Identifier: Apache-2.0
"""eu-ai-act-deterministic pack: its outcomes are exactly the deterministic
obligations pack compiled from ``eu-ai-act/obligations-register.yaml`` (plus a
``tier`` overlay), and the measurability report over the tau2 corpus and a
synthetic internal-assist sample makes the MISSING INSTRUMENT result a checked
fact rather than an assertion in a comment."""
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
from capsule_engine.register import EvidenceCompiler, load_register_file

REPO = Path(__file__).parent.parent
CATALOG = REPO / "capsule_engine" / "packs" / "catalog"
PACK_DIR = CATALOG / "eu-ai-act-deterministic"
REGISTER = CATALOG / "eu-ai-act" / "obligations-register.yaml"
TAU2_CORPUS = (
    REPO / "capsule_engine" / "examples" / "data" / "tau2_airline" / "tau2_conversations_claude-3-7-sonnet_airline_4trials.jsonl"
)
SYNTHETIC_CORPUS = PACK_DIR / "fixtures" / "synthetic_internal_assist_sample.jsonl"

MUST_HAVE_ARTICLES = ("Article 5", "Article 50", "Article 26")


def _load_jsonl(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


@pytest.fixture(scope="module")
def pack():
    return load_pack_dir(PACK_DIR)


@pytest.fixture(scope="module")
def compiled():
    return EvidenceCompiler().compile_obligations(load_register_file(REGISTER))


def test_pack_loads(pack):
    assert pack.pack_id == "asg/eu-ai-act-deterministic/0.1.0"
    assert len(pack.outcomes) == 26
    assert len({o.id for o in pack.outcomes}) == 26


def test_outcomes_match_the_compiled_register_except_tier(pack, compiled):
    """The drift check: the register is the one source for these rows. Only
    ``tier`` may differ, and only as the human overlay below."""
    assert [o.id for o in pack.outcomes] == [c.id for c in compiled.compiled]
    for outcome, contract in zip(pack.outcomes, compiled.compiled, strict=True):
        got = outcome.canonical_dict()
        got.pop("tier", None)
        assert got == contract.canonical_dict(), outcome.id


def test_tier_must_have_is_exactly_the_art_5_50_26_rows(pack):
    for outcome in pack.outcomes:
        expected = "must_have" if outcome.clause.article in MUST_HAVE_ARTICLES else "informational"
        assert outcome.tier == expected, outcome.id


def test_no_judged_or_excluded_row_is_in_the_pack(pack, compiled):
    ids = {o.id for o in pack.outcomes}
    assert ids.isdisjoint({e.id for e in compiled.excluded})
    for outcome in pack.outcomes:
        assert outcome.mode != "judged", outcome.id
        assert outcome.forward_verdict == outcome.backward_verdict == "DETERMINISTIC", outcome.id
        assert not outcome.clause.contested, outcome.id


@pytest.mark.parametrize("corpus_path", [TAU2_CORPUS, SYNTHETIC_CORPUS], ids=["tau2", "synthetic"])
def test_declared_not_measured_claims_hold(pack, corpus_path):
    """The oracle: every declared_not_measured row's instrument genuinely
    does not resolve on the corpus (raises the moment one does)."""
    verify_declared_not_measured(pack, _load_jsonl(corpus_path))


@pytest.mark.parametrize(
    "corpus_path,entity_field", [(TAU2_CORPUS, "task_id"), (SYNTHETIC_CORPUS, "session_id")], ids=["tau2", "synthetic"]
)
def test_measurability_report_names_every_missing_instrument(pack, corpus_path, entity_field):
    """Over a plain transcript corpus, every row that names an instrument
    reports MISSING INSTRUMENT; the rows that name none have no declared gap
    and report as resolving."""
    rows = build_measurability_report(pack, _load_jsonl(corpus_path), entity_key=entity_key_field(entity_field))
    assert len(rows) == 26
    with_instrument = {o.id for o in pack.outcomes if o.evidence_instrument is not None}
    assert len(with_instrument) == 21
    assert {r.outcome_id for r in rows if r.status == STATUS_MISSING_INSTRUMENT} == with_instrument
    assert {r.outcome_id for r in rows if r.status == STATUS_RESOLVES} == {o.id for o in pack.outcomes} - with_instrument


def test_synthetic_sample_is_labelled_synthetic():
    units = _load_jsonl(SYNTHETIC_CORPUS)
    assert len(units) == 6
    assert all(u["session_id"].startswith("ia-") for u in units)
