# SPDX-License-Identifier: Apache-2.0
"""outcomes[] -- the sister table to obligations[] (compiler-and-setup
design of record 2026-08-19; supersedes [ldg-outcome-declaration-schema]).

Digest preservation for existing zero-outcome packs, required-field
validation, and the mechanical enforcement that ``agent.caused_resolution``
can only be declared as a refusal -- never silently accepted as a provable
claim. Every negative case here is exercised twice: once as the failure
(RED) and once as the corrected near-miss that loads clean (GREEN), per
QUEUE_PROTOCOL §7 ("a refusal test that never rejected anything proves
nothing").
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from capsule_engine.packs.errors import PackDefinitionError
from capsule_engine.packs.loader import load_pack_dir

PAYMENTS_SAFETY_DIR = Path(__file__).parent.parent / "capsule_engine" / "packs" / "catalog" / "payments-safety"

BASE_PACK = {
    "pack_id": "test_pub/test-pack/1.0.0",
    "obligations": [{"id": "o1", "statement": "no dup", "check": "dedupe"}],
    "action_semantics": [
        {"action_type": "payment.dispatch", "action_class": "money.transfer", "required_fields": ["amount_minor"]}
    ],
    "constraints": [{"wicket_id": "test.dedupe/1.0.0", "check": "dedupe", "config": {}}],
    "folds": [{"file": "spend.yaml"}],
}

MINIMAL_FOLD_YAML = """
fold_id: test.spend/1.0.0
reads:
  - path: developer
    erasure_class: commitment-ok
key: developer
reduce:
  reducer: count
emit: n
"""

_DOC_DIGEST = "a" * 64


def _write_pack(tmp_path: Path, overrides: dict | None = None) -> Path:
    data = {**BASE_PACK, **(overrides or {})}
    (tmp_path / "pack.yaml").write_text(yaml.dump(data))
    (tmp_path / "spend.yaml").write_text(MINIMAL_FOLD_YAML)
    return tmp_path


def _outcome(**overrides) -> dict:
    base = {
        "id": "outcome.remediation_confirmed",
        "statement": "The flagged condition was remediated.",
        "evidence_rule": "fulfill capsule chained to intent, effect_attestation=counterparty_confirmed",
        "forward_verdict": "DETERMINISTIC",
        "backward_verdict": "DETERMINISTIC",
    }
    base.update(overrides)
    return base


# --- digest preservation for existing packs ---------------------------


def test_zero_outcome_pack_digests_identically_to_before_the_outcomes_field_existed(tmp_path):
    """Additive-schema guarantee: a pack with no outcomes[] key at all must
    produce the same canonical_dict/digest as one written before this
    field existed. Verified against a hand-computed canonical dict that
    omits "outcomes"/"scope_census" entirely -- if this ever changes, every
    already-sealed pack pin in the fleet silently breaks."""
    pack_dir = _write_pack(tmp_path)
    pack = load_pack_dir(pack_dir)
    canonical = pack.canonical_dict()
    assert "outcomes" not in canonical
    assert "scope_census" not in canonical


def test_the_real_payments_safety_pack_digest_is_unchanged():
    """The committed payments-safety catalog pack (no outcomes[]) must keep
    its exact digest through this change -- a real regression check, not a
    synthetic one."""
    pack = load_pack_dir(PAYMENTS_SAFETY_DIR)
    assert pack.outcomes == ()
    assert pack.scope_census is None
    assert "outcomes" not in pack.canonical_dict()


# --- required fields --------------------------------------------------


def test_missing_evidence_rule_is_a_schema_error(tmp_path):
    pack_dir = _write_pack(tmp_path, {"outcomes": [_outcome(evidence_rule="")]})
    with pytest.raises(PackDefinitionError) as exc:
        load_pack_dir(pack_dir)
    assert exc.value.reason == "missing_evidence_rule"


def test_an_outcome_with_an_evidence_rule_loads_clean(tmp_path):
    pack_dir = _write_pack(tmp_path, {"outcomes": [_outcome()]})
    pack = load_pack_dir(pack_dir)
    assert pack.outcomes[0].evidence_rule


def test_duplicate_outcome_id_is_rejected(tmp_path):
    pack_dir = _write_pack(tmp_path, {"outcomes": [_outcome(), _outcome()]})
    with pytest.raises(PackDefinitionError) as exc:
        load_pack_dir(pack_dir)
    assert exc.value.reason == "duplicate_outcome_id"


def test_invalid_forward_verdict_is_rejected(tmp_path):
    pack_dir = _write_pack(tmp_path, {"outcomes": [_outcome(forward_verdict="MODEL-ASSISTED")]})
    with pytest.raises(PackDefinitionError) as exc:
        load_pack_dir(pack_dir)
    assert exc.value.reason == "invalid_verdict"


def test_valid_forward_verdict_loads_clean(tmp_path):
    pack_dir = _write_pack(tmp_path, {"outcomes": [_outcome(forward_verdict="DETERMINISTIC")]})
    pack = load_pack_dir(pack_dir)
    assert pack.outcomes[0].forward_verdict == "DETERMINISTIC"


# --- the load-bearing case: agent.caused_resolution -----------------------


def test_agent_caused_resolution_with_a_non_refused_verdict_is_rejected(tmp_path):
    """The RED case: someone tries to ship the undecomposable causal claim
    as though it were provable."""
    pack_dir = _write_pack(
        tmp_path,
        {
            "outcomes": [
                _outcome(
                    id="outcome.bad_causal_claim",
                    effect_claim="agent.caused_resolution",
                    forward_verdict="DETERMINISTIC",
                    backward_verdict="DETERMINISTIC",
                )
            ]
        },
    )
    with pytest.raises(PackDefinitionError) as exc:
        load_pack_dir(pack_dir)
    assert exc.value.reason == "effect_claim_not_refused"


def test_agent_caused_resolution_declared_as_refused_loads_clean(tmp_path):
    """The GREEN case, same claim: declared exactly as compile_effect_claim
    says (REFUSED/REFUSED, with the seeded reason code) -- and the reason
    code is auto-filled when omitted."""
    pack_dir = _write_pack(
        tmp_path,
        {
            "outcomes": [
                _outcome(
                    id="outcome.bad_causal_claim",
                    evidence_rule="n/a -- undecomposable, refused by design",
                    effect_claim="agent.caused_resolution",
                    forward_verdict="REFUSED",
                    backward_verdict="REFUSED",
                )
            ]
        },
    )
    pack = load_pack_dir(pack_dir)
    outcome = pack.outcome_for_id("outcome.bad_causal_claim")
    assert outcome.refusal_reason_code == "agent_caused_resolution_undecomposable"


@pytest.mark.parametrize("claim", ["recommendation.acted_on", "resolution.followed_action"])
def test_admissible_effect_claims_load_clean(tmp_path, claim):
    pack_dir = _write_pack(
        tmp_path,
        {
            "outcomes": [
                _outcome(
                    id="outcome.near_miss",
                    effect_claim=claim,
                    forward_verdict="DETERMINISTIC",
                    backward_verdict="DETERMINISTIC",
                )
            ]
        },
    )
    pack = load_pack_dir(pack_dir)
    assert pack.outcome_for_id("outcome.near_miss").effect_claim == claim


def test_unknown_effect_claim_is_rejected(tmp_path):
    pack_dir = _write_pack(tmp_path, {"outcomes": [_outcome(effect_claim="agent.definitely_caused_it")]})
    with pytest.raises(PackDefinitionError) as exc:
        load_pack_dir(pack_dir)
    assert exc.value.reason == "unknown_effect_claim"


# --- REFUSED requires a reason code ----------------------------------


def test_a_refused_verdict_with_no_reason_code_is_rejected(tmp_path):
    pack_dir = _write_pack(
        tmp_path,
        {
            "outcomes": [
                _outcome(
                    id="outcome.unbounded",
                    evidence_rule="n/a -- unbounded goal, refused by design",
                    forward_verdict="REFUSED",
                    backward_verdict="REFUSED",
                )
            ]
        },
    )
    with pytest.raises(PackDefinitionError) as exc:
        load_pack_dir(pack_dir)
    assert exc.value.reason == "missing_refusal_reason"


def test_a_refused_verdict_with_an_explicit_reason_code_loads_clean(tmp_path):
    pack_dir = _write_pack(
        tmp_path,
        {
            "outcomes": [
                _outcome(
                    id="outcome.unbounded",
                    evidence_rule="n/a -- unbounded goal, refused by design",
                    statement="Customer trust increases over time.",
                    forward_verdict="REFUSED",
                    backward_verdict="REFUSED",
                    refusal_reason_code="unbounded_goal_unmonitorable",
                )
            ]
        },
    )
    pack = load_pack_dir(pack_dir)
    assert pack.outcome_for_id("outcome.unbounded").refusal_reason_code == "unbounded_goal_unmonitorable"


# --- scope census -------------------------------------------------------


def test_scope_census_n_greater_than_m_is_rejected(tmp_path):
    pack_dir = _write_pack(tmp_path, {"scope_census": {"document_digest": _DOC_DIGEST, "n": 90, "m": 10, "review_by": "2027-01-01"}})
    with pytest.raises(PackDefinitionError) as exc:
        load_pack_dir(pack_dir)
    assert exc.value.reason == "invalid_scope_census"


def test_scope_census_with_valid_n_and_m_loads_clean(tmp_path):
    pack_dir = _write_pack(tmp_path, {"scope_census": {"document_digest": _DOC_DIGEST, "n": 23, "m": 88, "review_by": "2027-01-01"}})
    pack = load_pack_dir(pack_dir)
    assert pack.scope_census.n == 23
    assert pack.scope_census.m == 88


# --- re-derivability grade on obligations ------------------------------


def test_invalid_re_derivability_grade_on_an_obligation_is_rejected(tmp_path):
    data = {**BASE_PACK}
    data["obligations"] = [{"id": "o1", "statement": "no dup", "check": "dedupe", "re_derivability_grade": "made_up_grade"}]
    (tmp_path / "pack.yaml").write_text(yaml.dump(data))
    (tmp_path / "spend.yaml").write_text(MINIMAL_FOLD_YAML)
    with pytest.raises(PackDefinitionError) as exc:
        load_pack_dir(tmp_path)
    assert exc.value.reason == "invalid_re_derivability_grade"


def test_valid_re_derivability_grade_on_an_obligation_loads_clean(tmp_path):
    data = {**BASE_PACK}
    data["obligations"] = [{"id": "o1", "statement": "no dup", "check": "dedupe", "re_derivability_grade": "ledger_state_dependent"}]
    (tmp_path / "pack.yaml").write_text(yaml.dump(data))
    (tmp_path / "spend.yaml").write_text(MINIMAL_FOLD_YAML)
    pack = load_pack_dir(tmp_path)
    assert pack.obligations[0].re_derivability_grade == "ledger_state_dependent"


# --- measurability / evidence_instrument ([pack-harden-tau2-oracle]) ------
#
# Closes the adversarial-review finding (adv-tau2-demo.md Area 1/4) that a
# term's "declared, not measured on this corpus" status was a hardcoded
# Python lambda a future coder could point at ANY term -- including one with
# a real fail -- with nothing in the schema/loader to catch it. Measurability
# is now closed-set DATA, and a declared_not_measured claim MUST carry an
# evidence_instrument -- corpus_verify.py is the runtime oracle that checks
# the claim against a real corpus (see tests/test_corpus_verify.py).


def test_zero_outcome_pack_digest_is_unaffected_by_measurability_default(tmp_path):
    """A pack with no outcomes[] key still digests identically -- the new
    fields don't touch the additive-schema guarantee already proven above."""
    pack_dir = _write_pack(tmp_path)
    pack = load_pack_dir(pack_dir)
    assert "outcomes" not in pack.canonical_dict()


def test_default_measurability_is_measured_and_omitted_from_the_digest(tmp_path):
    """An outcome that doesn't mention measurability at all -- the ordinary
    case for every pre-existing outcome -- parses as 'measured' and the
    digest renders identically to before this field existed (no
    'measurability' key at all), so no already-sealed pack pin moves."""
    pack_dir = _write_pack(tmp_path, {"outcomes": [_outcome()]})
    pack = load_pack_dir(pack_dir)
    assert pack.outcomes[0].measurability == "measured"
    assert pack.outcomes[0].evidence_instrument is None
    assert "measurability" not in pack.canonical_dict()["outcomes"][0]
    assert "evidence_instrument" not in pack.canonical_dict()["outcomes"][0]


def test_invalid_measurability_value_is_rejected(tmp_path):
    pack_dir = _write_pack(tmp_path, {"outcomes": [_outcome(measurability="sort_of_measured")]})
    with pytest.raises(PackDefinitionError) as exc:
        load_pack_dir(pack_dir)
    assert exc.value.reason == "invalid_measurability"


def test_declared_not_measured_without_an_evidence_instrument_is_rejected(tmp_path):
    """The RED case this task exists to close: a term declares itself
    unmeasurable but names no checkable signal -- exactly the unverifiable
    shape the old hardcoded ``always_false`` lambda had."""
    pack_dir = _write_pack(
        tmp_path,
        {"outcomes": [_outcome(measurability="declared_not_measured", forward_verdict="UNAVAILABLE-STATE-REQUIRED")]},
    )
    with pytest.raises(PackDefinitionError) as exc:
        load_pack_dir(pack_dir)
    assert exc.value.reason == "missing_evidence_instrument"


def test_declared_not_measured_with_an_evidence_instrument_loads_clean(tmp_path):
    """The GREEN near-miss: same claim, now naming a checkable instrument."""
    pack_dir = _write_pack(
        tmp_path,
        {
            "outcomes": [
                _outcome(
                    measurability="declared_not_measured",
                    forward_verdict="UNAVAILABLE-STATE-REQUIRED",
                    evidence_instrument={"kind": "structured_field", "field": "restriction_reason_cited"},
                )
            ]
        },
    )
    pack = load_pack_dir(pack_dir)
    outcome = pack.outcomes[0]
    assert outcome.measurability == "declared_not_measured"
    assert outcome.evidence_instrument.kind == "structured_field"
    assert outcome.evidence_instrument.field == "restriction_reason_cited"
    rendered = pack.canonical_dict()["outcomes"][0]
    assert rendered["measurability"] == "declared_not_measured"
    assert rendered["evidence_instrument"] == {"kind": "structured_field", "field": "restriction_reason_cited"}


def test_unknown_evidence_instrument_kind_is_rejected(tmp_path):
    pack_dir = _write_pack(
        tmp_path,
        {
            "outcomes": [
                _outcome(
                    measurability="declared_not_measured",
                    forward_verdict="UNAVAILABLE-STATE-REQUIRED",
                    evidence_instrument={"kind": "vibes", "field": "x"},
                )
            ]
        },
    )
    with pytest.raises(PackDefinitionError) as exc:
        load_pack_dir(pack_dir)
    assert exc.value.reason == "invalid_evidence_instrument"


def test_structured_field_instrument_without_a_field_name_is_rejected(tmp_path):
    pack_dir = _write_pack(
        tmp_path,
        {
            "outcomes": [
                _outcome(
                    measurability="declared_not_measured",
                    forward_verdict="UNAVAILABLE-STATE-REQUIRED",
                    evidence_instrument={"kind": "structured_field"},
                )
            ]
        },
    )
    with pytest.raises(PackDefinitionError) as exc:
        load_pack_dir(pack_dir)
    assert exc.value.reason == "invalid_evidence_instrument"


def test_tool_call_name_instrument_loads_clean(tmp_path):
    pack_dir = _write_pack(
        tmp_path,
        {
            "outcomes": [
                _outcome(
                    measurability="declared_not_measured",
                    forward_verdict="UNAVAILABLE-STATE-REQUIRED",
                    evidence_instrument={"kind": "tool_call_name", "name": "issue_refund"},
                )
            ]
        },
    )
    pack = load_pack_dir(pack_dir)
    assert pack.outcomes[0].evidence_instrument.name == "issue_refund"


def test_measured_outcome_may_still_declare_an_evidence_instrument(tmp_path):
    """evidence_instrument is optional documentation on a 'measured' outcome
    (only REQUIRED when declared_not_measured) -- a pack author may still
    name the real signal a measured check reads, for corpus_verify.py or a
    future consumer to cross-reference."""
    pack_dir = _write_pack(
        tmp_path,
        {"outcomes": [_outcome(evidence_instrument={"kind": "tool_call_name", "name": "offer_alternative"})]},
    )
    pack = load_pack_dir(pack_dir)
    assert pack.outcomes[0].measurability == "measured"
    assert pack.outcomes[0].evidence_instrument.name == "offer_alternative"


def test_the_real_airline_engagement_pack_loads_clean_with_expected_measurability_split():
    """The real, committed catalog pack this task templatizes -- not a
    synthetic fixture. 3 measured (A4, A6, A7), 5 declared_not_measured
    (A1, A2, A3a, A3b, A5). ``[remove-keyword-scorers]`` moved A3b from
    measured (a keyword regex reported as a finding) to declared_not_measured
    (honest pending-judge state); ``[ldg-bj-91-a1-to-llm-judge]`` (review
    bounce B2) did the identical move for A1 -- it no longer matches
    ``record_grounding_bench.judge_run.airline_terms``'s split over the tau2
    airline corpus for either row."""
    pack_dir = Path(__file__).parent.parent / "capsule_engine" / "packs" / "catalog" / "airline-engagement"
    pack = load_pack_dir(pack_dir)
    by_id = {o.id: o for o in pack.outcomes}
    assert set(by_id) == {"A1", "A2", "A3a", "A3b", "A4", "A5", "A6", "A7"}
    measured = {oid for oid, o in by_id.items() if o.measurability == "measured"}
    declared_not_measured = {oid for oid, o in by_id.items() if o.measurability == "declared_not_measured"}
    assert measured == {"A4", "A6", "A7"}
    assert declared_not_measured == {"A1", "A2", "A3a", "A3b", "A5"}
    for oid in declared_not_measured:
        assert by_id[oid].evidence_instrument is not None


# --- tier ([ldg-bj-tier-field], backward-judge design §8.2) ---------------
#
# Whether an outcome gates a session's job-success (must_have) or is
# reported without gating (informational, the default). Additive, closed-
# set, no per-term target/ratio -- the gate is entirely at the session-level
# rollup a later task builds (§8.4).


def test_default_tier_is_informational_and_omitted_from_the_digest(tmp_path):
    """An outcome that doesn't mention tier at all -- the ordinary case for
    every pre-existing outcome -- parses as 'informational' and the digest
    renders identically to before this field existed (no 'tier' key at
    all), so no already-sealed pack pin moves."""
    pack_dir = _write_pack(tmp_path, {"outcomes": [_outcome()]})
    pack = load_pack_dir(pack_dir)
    assert pack.outcomes[0].tier == "informational"
    assert "tier" not in pack.canonical_dict()["outcomes"][0]


def test_invalid_tier_value_is_rejected(tmp_path):
    pack_dir = _write_pack(tmp_path, {"outcomes": [_outcome(tier="critical")]})
    with pytest.raises(PackDefinitionError) as exc:
        load_pack_dir(pack_dir)
    assert exc.value.reason == "invalid_tier"


def test_must_have_tier_loads_clean_and_renders_in_the_digest(tmp_path):
    pack_dir = _write_pack(tmp_path, {"outcomes": [_outcome(tier="must_have")]})
    pack = load_pack_dir(pack_dir)
    assert pack.outcomes[0].tier == "must_have"
    assert pack.canonical_dict()["outcomes"][0]["tier"] == "must_have"


# --- mode ([ldg-bp-mode-tag], standard-outcome-pack design §3) ------------
#
# Which of the seven ways an outcome is judged (structural/value/judged/
# fold_rollup/fold_counterparty/fold_agent/fold_cohort). Additive, closed-
# set, same optional-with-default backward-compat pattern as tier (#93) --
# lets a report group by judgment mode and lets propose route grading.


def test_default_mode_is_structural_and_omitted_from_the_digest(tmp_path):
    """An outcome that doesn't mention mode at all -- the ordinary case for
    every pre-existing outcome -- parses as 'structural' and the digest
    renders identically to before this field existed (no 'mode' key at
    all), so no already-sealed pack pin moves."""
    pack_dir = _write_pack(tmp_path, {"outcomes": [_outcome()]})
    pack = load_pack_dir(pack_dir)
    assert pack.outcomes[0].mode == "structural"
    assert "mode" not in pack.canonical_dict()["outcomes"][0]


def test_invalid_mode_value_is_rejected(tmp_path):
    pack_dir = _write_pack(tmp_path, {"outcomes": [_outcome(mode="vibes")]})
    with pytest.raises(PackDefinitionError) as exc:
        load_pack_dir(pack_dir)
    assert exc.value.reason == "invalid_mode"


def test_judged_mode_loads_clean_and_renders_in_the_digest(tmp_path):
    pack_dir = _write_pack(tmp_path, {"outcomes": [_outcome(mode="judged")]})
    pack = load_pack_dir(pack_dir)
    assert pack.outcomes[0].mode == "judged"
    assert pack.canonical_dict()["outcomes"][0]["mode"] == "judged"


@pytest.mark.parametrize(
    "mode",
    ["structural", "value", "judged", "fold_rollup", "fold_counterparty", "fold_agent", "fold_cohort"],
)
def test_every_closed_set_mode_loads_clean(tmp_path, mode):
    pack_dir = _write_pack(tmp_path, {"outcomes": [_outcome(mode=mode)]})
    pack = load_pack_dir(pack_dir)
    assert pack.outcomes[0].mode == mode
