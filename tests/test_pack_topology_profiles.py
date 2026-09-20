# SPDX-License-Identifier: Apache-2.0
"""``[ldg-bp-topology-profiles]``: relationship-topology profiles over a
pack's own outcomes (standard-outcome-pack design §6b/§7). A profile is a
thin, additive declaration -- ``profile_id`` + a counterparty binding + a
handful of per-outcome ``{applies, tier}`` overrides -- never a forked pack.
Two properties matter most and get their own tests: (1) the agent-integrity
core (structural/value/fold_rollup outcomes) is topology-INVARIANT by
construction, mechanically enforced at load time, not merely by convention;
(2) the same standard pack under two different profiles selects different
applicability/tier and a different counterparty for the fold_counterparty
family, while the integrity core stays identical.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from capsule_engine.packs.errors import PackDefinitionError
from capsule_engine.packs.loader import load_pack_dir

STANDARD_VENDOR_DIR = Path(__file__).parent.parent / "capsule_engine" / "packs" / "catalog" / "standard-vendor"

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


def _profile(**overrides) -> dict:
    base = {
        "profile_id": "p1_external_serve",
        "counterparty_binding": {"direct": "customer"},
    }
    base.update(overrides)
    return base


# --- digest preservation for existing packs -------------------------------


def test_zero_profile_pack_digests_identically_to_before_the_profiles_field_existed(tmp_path):
    pack_dir = _write_pack(tmp_path, {"outcomes": [_outcome(mode="judged")]})
    pack = load_pack_dir(pack_dir)
    assert pack.profiles == ()
    assert "profiles" not in pack.canonical_dict()


def test_the_real_standard_vendor_pack_declares_all_four_shipped_profiles():
    pack = load_pack_dir(STANDARD_VENDOR_DIR)
    assert {p.profile_id for p in pack.profiles} == {
        "p1_external_serve",
        "p2_internal_assist",
        "p3_mediated",
        "p4_agent_to_agent",
    }
    # P5 autonomous is deferred (design §6b) -- not a declarable profile yet.
    assert "p5_autonomous" not in {p.profile_id for p in pack.profiles}


# --- basic parsing / validation -------------------------------------------


def test_unknown_profile_id_is_rejected(tmp_path):
    pack_dir = _write_pack(
        tmp_path, {"outcomes": [_outcome(mode="judged")], "profiles": [_profile(profile_id="p5_autonomous")]}
    )
    with pytest.raises(PackDefinitionError) as exc:
        load_pack_dir(pack_dir)
    assert exc.value.reason == "invalid_profile_id"


def test_duplicate_profile_id_is_rejected(tmp_path):
    pack_dir = _write_pack(tmp_path, {"outcomes": [_outcome(mode="judged")], "profiles": [_profile(), _profile()]})
    with pytest.raises(PackDefinitionError) as exc:
        load_pack_dir(pack_dir)
    assert exc.value.reason == "duplicate_profile_id"


def test_a_clean_profile_loads_and_defaults_ultimate_to_direct(tmp_path):
    pack_dir = _write_pack(tmp_path, {"outcomes": [_outcome(mode="judged")], "profiles": [_profile()]})
    pack = load_pack_dir(pack_dir)
    profile = pack.profile_for("p1_external_serve")
    assert profile.counterparty_binding.direct == "customer"
    assert profile.counterparty_binding.ultimate == "customer"


def test_a_dual_binding_keeps_direct_and_ultimate_distinct(tmp_path):
    pack_dir = _write_pack(
        tmp_path,
        {
            "outcomes": [_outcome(mode="judged")],
            "profiles": [_profile(profile_id="p3_mediated", counterparty_binding={"direct": "employee", "ultimate": "downstream"})],
        },
    )
    pack = load_pack_dir(pack_dir)
    binding = pack.profile_for("p3_mediated").counterparty_binding
    assert (binding.direct, binding.ultimate) == ("employee", "downstream")


def test_override_naming_an_unknown_outcome_id_is_rejected(tmp_path):
    pack_dir = _write_pack(
        tmp_path,
        {
            "outcomes": [_outcome(mode="judged")],
            "profiles": [_profile(overrides=[{"outcome_id": "does_not_exist", "applies": False}])],
        },
    )
    with pytest.raises(PackDefinitionError) as exc:
        load_pack_dir(pack_dir)
    assert exc.value.reason == "unknown_outcome_in_profile_override"


def test_duplicate_override_outcome_id_within_one_profile_is_rejected(tmp_path):
    pack_dir = _write_pack(
        tmp_path,
        {
            "outcomes": [_outcome(mode="judged")],
            "profiles": [
                _profile(
                    overrides=[
                        {"outcome_id": "outcome.remediation_confirmed", "tier": "must_have"},
                        {"outcome_id": "outcome.remediation_confirmed", "applies": False},
                    ]
                )
            ],
        },
    )
    with pytest.raises(PackDefinitionError) as exc:
        load_pack_dir(pack_dir)
    assert exc.value.reason == "duplicate_profile_override_outcome_id"


def test_an_invalid_tier_in_an_override_is_rejected(tmp_path):
    pack_dir = _write_pack(
        tmp_path,
        {
            "outcomes": [_outcome(mode="judged")],
            "profiles": [_profile(overrides=[{"outcome_id": "outcome.remediation_confirmed", "tier": "vibes"}])],
        },
    )
    with pytest.raises(PackDefinitionError) as exc:
        load_pack_dir(pack_dir)
    assert exc.value.reason == "invalid_tier"


# --- the topology-invariant enforcement (RED/GREEN, QUEUE_PROTOCOL §7) ----


@pytest.mark.parametrize("mode", ["structural", "value", "fold_rollup"])
def test_overriding_a_topology_invariant_mode_is_rejected(tmp_path, mode):
    """RED: structural/value/fold_rollup outcomes (S1-S4/F1 in the real
    standard-vendor pack) are the invariant trust floor -- a profile may not
    touch them, mechanically enforced, not by convention. `value` stays a
    real, checked mode here even though the shipped pack has no row using
    it as of the V1/V2 ruling (2026-09-01, design doc [rev8]): both moved to
    `judged`/MODEL-ASSISTED, labeled honestly rather than sitting at
    WITH-INSTRUMENTATION -- see `_INTEGRITY_CORE_IDS` below, which no
    longer includes V1."""
    pack_dir = _write_pack(
        tmp_path,
        {
            "outcomes": [_outcome(mode=mode, tier="must_have")],
            "profiles": [_profile(overrides=[{"outcome_id": "outcome.remediation_confirmed", "applies": False}])],
        },
    )
    with pytest.raises(PackDefinitionError) as exc:
        load_pack_dir(pack_dir)
    assert exc.value.reason == "topology_invariant_override"


@pytest.mark.parametrize("mode", ["judged", "fold_counterparty", "fold_agent", "fold_cohort"])
def test_overriding_a_non_invariant_mode_loads_clean(tmp_path, mode):
    """GREEN near-miss: every mode NOT in TOPOLOGY_INVARIANT_MODES is a
    legitimate override target -- the check discriminates, it isn't just a
    blanket refusal."""
    pack_dir = _write_pack(
        tmp_path,
        {
            "outcomes": [_outcome(mode=mode)],
            "profiles": [_profile(overrides=[{"outcome_id": "outcome.remediation_confirmed", "applies": False}])],
        },
    )
    pack = load_pack_dir(pack_dir)
    assert pack.profile_for("p1_external_serve").overrides[0].applies is False


# --- outcomes_for_profile / subject_for -----------------------------------


def _pack_with_two_outcomes(tmp_path):
    return _write_pack(
        tmp_path,
        {
            "outcomes": [
                _outcome(id="J1", statement="conduct", mode="judged", tier="must_have"),
                _outcome(id="C1", statement="growth", mode="fold_counterparty"),
                _outcome(id="F1", statement="job success", mode="fold_rollup", tier="must_have"),
            ],
            "profiles": [
                _profile(
                    profile_id="p2_internal_assist",
                    counterparty_binding={"direct": "employee"},
                    overrides=[{"outcome_id": "J1", "tier": "informational"}],
                ),
                _profile(
                    profile_id="p4_agent_to_agent",
                    counterparty_binding={"direct": "agent", "ultimate": "principal"},
                    overrides=[
                        {"outcome_id": "J1", "applies": False},
                        {"outcome_id": "C1", "applies": False},
                    ],
                ),
            ],
        },
    )


def test_outcomes_for_profile_replaces_tier_and_keeps_the_outcome(tmp_path):
    pack = load_pack_dir(_pack_with_two_outcomes(tmp_path))
    profiled = pack.outcomes_for_profile("p2_internal_assist")
    assert profiled.excluded == ()
    j1 = next(o for o in profiled.outcomes if o.id == "J1")
    assert j1.tier == "informational"
    # the pack's own EvidenceContract is untouched -- outcomes_for_profile is a view.
    assert pack.outcome_for_id("J1").tier == "must_have"


def test_outcomes_for_profile_excludes_na_outcomes(tmp_path):
    pack = load_pack_dir(_pack_with_two_outcomes(tmp_path))
    profiled = pack.outcomes_for_profile("p4_agent_to_agent")
    assert profiled.excluded == ("C1", "J1")
    assert {o.id for o in profiled.outcomes} == {"F1"}


def test_outcomes_for_profile_rejects_an_unknown_profile_id(tmp_path):
    pack = load_pack_dir(_pack_with_two_outcomes(tmp_path))
    with pytest.raises(PackDefinitionError) as exc:
        pack.outcomes_for_profile("p1_external_serve")
    assert exc.value.reason == "unknown_profile_id"


def test_subject_for_reads_fold_counterparty_as_direct_and_fold_rollup_as_ultimate(tmp_path):
    pack_dir = _write_pack(
        tmp_path,
        {
            "outcomes": [
                _outcome(id="C1", statement="growth", mode="fold_counterparty"),
                _outcome(id="F1", statement="job success", mode="fold_rollup", tier="must_have"),
                _outcome(id="J1", statement="conduct", mode="judged"),
            ],
            "profiles": [_profile(profile_id="p3_mediated", counterparty_binding={"direct": "employee", "ultimate": "downstream"})],
        },
    )
    pack = load_pack_dir(pack_dir)
    profiled = pack.outcomes_for_profile("p3_mediated")
    assert profiled.subject_for("C1") == "employee"
    assert profiled.subject_for("F1") == "downstream"
    assert profiled.subject_for("J1") is None  # not counterparty-scoped


# --- canonical_dict / digest additivity for a real profiles[] block -------


def test_a_populated_profiles_block_renders_in_the_digest_and_is_sorted(tmp_path):
    pack_dir = _write_pack(
        tmp_path,
        {
            "outcomes": [_outcome(mode="judged")],
            "profiles": [_profile(profile_id="p2_internal_assist"), _profile(profile_id="p1_external_serve")],
        },
    )
    pack = load_pack_dir(pack_dir)
    canonical = pack.canonical_dict()
    assert [p["profile_id"] for p in canonical["profiles"]] == ["p1_external_serve", "p2_internal_assist"]


# --- the acceptance scenario, run over the REAL standard-vendor pack ------
# (inbox [ldg-bp-topology-profiles]: "the same standard pack under P2 vs P3
# selects different applicability/tier and different C-family counterparty,
# with the integrity core identical")

# V1 removed 2026-09-01 (design doc [rev8], the V1/V2 ruling): V1's mode
# moved from `value` (topology-invariant, mechanically un-overridable) to
# `judged` (not invariant) -- it still happens to be must_have/unexcluded
# across every shipped profile today, but that's no longer a MECHANICALLY
# GUARANTEED property the way it is for S1-S4/F1 (a future profile COULD
# now legally override V1, where it couldn't before). This tuple checks the
# guarantee, not an incidental current state, so V1 no longer belongs here.
_INTEGRITY_CORE_IDS = ("S1", "S2", "S3", "S4", "F1")
_CONDUCT_IDS = ("J1", "J2", "J3", "J5", "J6")  # J4 has no declared tier either way
_COUNTERPARTY_CHANGE_IDS = ("C1", "C2", "C3", "C4", "C5", "C6")


def test_p1_conduct_is_must_have_and_c_family_binds_to_the_customer():
    pack = load_pack_dir(STANDARD_VENDOR_DIR)
    profiled = pack.outcomes_for_profile("p1_external_serve")
    by_id = {o.id: o for o in profiled.outcomes}
    for oid in _CONDUCT_IDS:
        assert by_id[oid].tier == "must_have", oid
    for oid in _COUNTERPARTY_CHANGE_IDS:
        assert profiled.subject_for(oid) == "customer", oid


def test_p2_vs_p3_select_different_tier_from_p1_but_share_conduct_tier_with_each_other():
    """P1 vs {P2, P3}: applicability/tier genuinely differs -- conduct (J)
    is must_have serving an external customer, informational once the
    direct counterparty is an internal employee."""
    pack = load_pack_dir(STANDARD_VENDOR_DIR)
    p1 = pack.outcomes_for_profile("p1_external_serve")
    p2 = pack.outcomes_for_profile("p2_internal_assist")
    p3 = pack.outcomes_for_profile("p3_mediated")
    p1_by_id = {o.id: o for o in p1.outcomes}
    p2_by_id = {o.id: o for o in p2.outcomes}
    p3_by_id = {o.id: o for o in p3.outcomes}
    for oid in _CONDUCT_IDS:
        assert p1_by_id[oid].tier == "must_have", oid
        assert p2_by_id[oid].tier == "informational", oid
        assert p3_by_id[oid].tier == "informational", oid


def test_p2_vs_p3_bind_the_c_family_to_a_different_ultimate_but_the_same_direct_counterparty():
    """P2 vs P3: the C-family (fold_counterparty, design §5's differentiated
    value-props) binds to 'direct' in both -- the employee -- but P3's
    job-success rollup (fold_rollup) binds to a DIFFERENT ultimate
    counterparty (the downstream customer) than P2's (where direct==ultimate,
    no mediation). This is the dual binding design §6b calls for."""
    pack = load_pack_dir(STANDARD_VENDOR_DIR)
    p2 = pack.outcomes_for_profile("p2_internal_assist")
    p3 = pack.outcomes_for_profile("p3_mediated")
    for oid in _COUNTERPARTY_CHANGE_IDS:
        assert p2.subject_for(oid) == "employee", oid
        assert p3.subject_for(oid) == "employee", oid
    assert p2.subject_for("F1") == "employee"
    assert p3.subject_for("F1") == "downstream"
    assert p2.subject_for("F1") != p3.subject_for("F1")


def test_p4_marks_conduct_and_c_family_na_but_keeps_the_integrity_core():
    pack = load_pack_dir(STANDARD_VENDOR_DIR)
    p4 = pack.outcomes_for_profile("p4_agent_to_agent")
    assert set(p4.excluded) == set(_CONDUCT_IDS) | {"J4"} | set(_COUNTERPARTY_CHANGE_IDS)
    remaining_ids = {o.id for o in p4.outcomes}
    for oid in _INTEGRITY_CORE_IDS:
        assert oid in remaining_ids, oid


def test_the_integrity_core_is_must_have_and_unexcluded_identically_across_every_profile():
    """The design's own invariant claim, checked mechanically across all
    four shipped profiles at once -- not just pairwise."""
    pack = load_pack_dir(STANDARD_VENDOR_DIR)
    for profile_id in ("p1_external_serve", "p2_internal_assist", "p3_mediated", "p4_agent_to_agent"):
        profiled = pack.outcomes_for_profile(profile_id)
        assert not (set(profiled.excluded) & set(_INTEGRITY_CORE_IDS)), profile_id
        by_id = {o.id: o for o in profiled.outcomes}
        for oid in _INTEGRITY_CORE_IDS:
            assert by_id[oid].tier == "must_have", (profile_id, oid)


# --- V1/V2 ruling (2026-09-01, design doc [rev8]) --------------------------
# "the wall moves to the label, not the capability": a value-integrity row
# may run LLM-Assisted, pinned and labeled MODEL-ASSISTED, rather than
# sitting at WITH-INSTRUMENTATION forever for lack of a typed sealed field.


def test_v1_and_v2_are_judged_model_assisted_not_with_instrumentation():
    pack = load_pack_dir(STANDARD_VENDOR_DIR)
    v1 = pack.outcome_for_id("V1")
    v2 = pack.outcome_for_id("V2")
    for outcome in (v1, v2):
        assert outcome.mode == "judged", outcome.id
        assert outcome.backward_verdict == "MODEL-ASSISTED", outcome.id
        assert outcome.forward_verdict == "UNAVAILABLE-MODEL-REQUIRED", outcome.id
        assert outcome.measurability == "measured", outcome.id  # not declared_not_measured
        assert outcome.evidence_instrument is None, outcome.id  # no structured field to bind to anymore
        # honesty check: the row must say MODEL-ASSISTED in its own evidence_rule,
        # never claim DETERMINISTIC -- the label is the whole point of the ruling.
        assert "MODEL-ASSISTED" in outcome.evidence_rule
        assert "DETERMINISTIC" not in outcome.evidence_rule.replace("never DETERMINISTIC", "")


def test_v1_tier_is_unchanged_must_have_v2_tier_is_unchanged_informational():
    """The ruling changes mode/verdict/measurability, deliberately NOT tier
    -- pin both original tiers so a future edit can't silently drift them."""
    pack = load_pack_dir(STANDARD_VENDOR_DIR)
    assert pack.outcome_for_id("V1").tier == "must_have"
    assert pack.outcome_for_id("V2").tier == "informational"  # V2 declares no tier -> schema default


def test_v1_is_no_longer_topology_invariant_a_profile_could_now_override_it():
    """The real (not hypothetical) consequence of moving V1 off `value`: it
    is no longer mechanically protected from a profile override the way
    S1-S4/F1 still are. This is a positive assertion the new state permits
    what the old one refused, not just an absence of a crash."""
    pack_dir_data = {
        "pack_id": "asg/test-v1-override/1.0.0",
        "obligations": [{"id": "o1", "statement": "no dup", "check": "dedupe"}],
        "action_semantics": [{"action_type": "t.a", "action_class": "info.query", "required_fields": ["target"]}],
        "constraints": [{"wicket_id": "t.dedupe/1.0.0", "check": "dedupe", "config": {}}],
        "folds": [{"file": "spend.yaml"}],
        "outcomes": [_outcome(id="V1", mode="judged", tier="must_have")],
        "profiles": [_profile(overrides=[{"outcome_id": "V1", "applies": False}])],
    }
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        (tmp_path / "pack.yaml").write_text(yaml.dump(pack_dir_data))
        (tmp_path / "spend.yaml").write_text(
            "fold_id: test.spend/1.0.0\nreads:\n  - path: developer\n    erasure_class: commitment-ok\n"
            "key: developer\nreduce:\n  reducer: count\nemit: n\n"
        )
        pack = load_pack_dir(tmp_path)  # must NOT raise topology_invariant_override -- judged mode permits the override
    assert pack.profile_for("p1_external_serve").overrides[0].applies is False
