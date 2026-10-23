# SPDX-License-Identifier: Apache-2.0
"""P1 acceptance: the refusal set demonstrated on BOTH partner shapes, and
tau2-bench-retail-shaped traces expressing both format gaps.

Three committed, synthetic, neutrally-named fixture packs under
``tests/fixtures/packs/``:

- ``causal_remediation_shaped`` -- the shape where the agent CAN act
  directly (design §1: "agent authority | acts -- changes state directly").
- ``advisory_incident_shaped`` -- the shape where the agent CANNOT act at
  all, read-only advisory (design §1: "agent authority | cannot act").
- ``retail_synthetic_shaped`` -- hand-built, retail-domain-shaped (refund /
  exchange tool families), NOT a generated tau2-bench shift; see the
  fixture's own header comment for why (no real generated corpus existed
  yet).

A single-shape fixture set would not discharge this acceptance line --
the undecomposed-trust claim (``agent.caused_resolution``) must be shown
refusing under each shape independently.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
import yaml

from capsule_engine.packs.errors import PackDefinitionError
from capsule_engine.packs.loader import load_pack_dir

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "packs"

CAUSAL_DIR = FIXTURES_DIR / "causal_remediation_shaped"
ADVISORY_DIR = FIXTURES_DIR / "advisory_incident_shaped"
RETAIL_DIR = FIXTURES_DIR / "retail_synthetic_shaped"

REFUSED_OUTCOME_ID_BY_SHAPE = {
    "causal": ("outcome.agent_caused_human_decision", CAUSAL_DIR),
    "advisory": ("outcome.agent_caused_ticket_resolution", ADVISORY_DIR),
    "retail": ("outcome.agent_caused_customer_satisfaction", RETAIL_DIR),
}


@pytest.mark.parametrize("shape", sorted(REFUSED_OUTCOME_ID_BY_SHAPE))
def test_agent_caused_resolution_is_refused_under_every_shape(shape):
    """The load-bearing acceptance line: not a single-shape fixture set."""
    outcome_id, pack_dir = REFUSED_OUTCOME_ID_BY_SHAPE[shape]
    pack = load_pack_dir(pack_dir)
    outcome = pack.outcome_for_id(outcome_id)
    assert outcome is not None, f"{shape} fixture is missing its refused outcome {outcome_id!r}"
    assert outcome.effect_claim == "agent.caused_resolution"
    assert outcome.forward_verdict == "REFUSED"
    assert outcome.backward_verdict == "REFUSED"
    assert outcome.refusal_reason_code == "agent_caused_resolution_undecomposable"


@pytest.mark.parametrize("shape", sorted(REFUSED_OUTCOME_ID_BY_SHAPE))
def test_each_shape_also_carries_the_admissible_near_miss(shape):
    """The refusal alone would not prove the near-misses are actually
    reachable -- each shape must also carry at least one admissible claim
    (recommendation.acted_on or resolution.followed_action)."""
    _, pack_dir = REFUSED_OUTCOME_ID_BY_SHAPE[shape]
    pack = load_pack_dir(pack_dir)
    admissible = [o for o in pack.outcomes if o.effect_claim in ("recommendation.acted_on", "resolution.followed_action")]
    assert admissible, f"{shape} fixture has no admissible near-miss outcome"
    for o in admissible:
        assert o.refusal_reason_code is None


@pytest.mark.parametrize("shape,pack_dir", [("causal", CAUSAL_DIR), ("advisory", ADVISORY_DIR), ("retail", RETAIL_DIR)])
def test_red_before_green_the_loader_rejects_a_hand_relaxed_copy_of_each_refusal(shape, pack_dir):
    """RED-before-green, against the real committed fixture: load the real
    pack.yaml, relax the refused outcome's verdict pair to DETERMINISTIC
    (as if someone tried to quietly ship the causal claim as provable), and
    confirm the loader still catches it. Proves the enforcement isn't an
    artifact of how the schema tests above constructed their own minimal
    pack -- it holds against the real fixture content too."""
    outcome_id, _ = REFUSED_OUTCOME_ID_BY_SHAPE[shape]
    data = yaml.safe_load((pack_dir / "pack.yaml").read_text())
    mutated = False
    for entry in data["outcomes"]:
        if entry["id"] == outcome_id:
            entry["forward_verdict"] = "DETERMINISTIC"
            entry["backward_verdict"] = "DETERMINISTIC"
            mutated = True
    assert mutated, f"fixture setup bug: {outcome_id!r} not found in {pack_dir}"

    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        (td_path / "pack.yaml").write_text(yaml.dump(data))
        (td_path / "spend.yaml").write_text((pack_dir / "spend.yaml").read_text())
        with pytest.raises(PackDefinitionError) as exc:
            load_pack_dir(td_path)
        assert exc.value.reason == "effect_claim_not_refused"


def test_causal_shape_loads_end_to_end():
    pack = load_pack_dir(CAUSAL_DIR)
    assert pack.pack_id == "test_pub/causal-remediation-shaped/1.0.0"
    assert pack.scope_census is not None
    assert len(pack.outcomes) == 4


def test_advisory_shape_loads_end_to_end():
    pack = load_pack_dir(ADVISORY_DIR)
    assert pack.pack_id == "test_pub/advisory-incident-shaped/1.0.0"
    assert len(pack.outcomes) == 4


# --- tau2-bench-retail-shaped: both format gaps expressed --------------


def test_retail_shaped_pack_expresses_the_execute_path():
    """Gap coverage 1/2: a real WRITE-tool-shaped, deterministic-both-ways
    outcome -- retail's refund.issue action family."""
    pack = load_pack_dir(RETAIL_DIR)
    execute = pack.outcome_for_id("outcome.refund_confirmed")
    assert execute.forward_verdict == "DETERMINISTIC"
    assert execute.backward_verdict == "DETERMINISTIC"
    assert execute.effect_claim is None  # no advisory claim needed -- the agent acted itself


def test_retail_shaped_pack_expresses_the_recommend_only_path():
    """Gap coverage 2/2: a recommend-only outcome using the advisory near-
    miss, alongside the execute path above -- both paths in one pack, same
    as tau2-bench retail's mixed refund/exchange tool shape."""
    pack = load_pack_dir(RETAIL_DIR)
    recommend_only = pack.outcome_for_id("outcome.exchange_recommended_and_acted_on")
    assert recommend_only.effect_claim == "recommendation.acted_on"


def test_retail_shaped_pack_also_carries_the_offer_response_denominator():
    pack = load_pack_dir(RETAIL_DIR)
    responded = pack.outcome_for_id("outcome.customer_responded_to_exchange_offer")
    assert responded.forward_verdict == "UNAVAILABLE-STATE-REQUIRED"
    assert responded.backward_verdict == "WITH-INSTRUMENTATION"
