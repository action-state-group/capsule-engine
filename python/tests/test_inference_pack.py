# SPDX-License-Identifier: Apache-2.0
"""The inference pack's v0 (capsule-engine#263): the selected peer's
advertised input and output rates against the operator's two caps
(``rate_cap``), a price that cannot be read refused, and the counterparty
list.

The shared vectors (``conformance-vectors/guard/v0/inference.json``) hold the
Python and Go guards to the same decisions; these tests pin the rules around
them: what a profile may set, and that the pack ships no bound."""
from __future__ import annotations

from pathlib import Path

import pytest

from capsule_engine.guards import Action
from capsule_engine.guards.checks import check_rate_cap
from capsule_engine.packs import install_pack, load_pack_dir
from capsule_engine.policy import PROFILE_FORMAT, PolicyManifestError, parse_profile
from capsule_engine.policy.errors import MALFORMED_PROFILE, PROFILE_UNKNOWN_PARAMETER
from capsule_engine.report.live_check import decide_steps

CATALOG = Path(__file__).parent.parent / "capsule_engine" / "packs" / "catalog"
INFERENCE = load_pack_dir(CATALOG / "inference")
EVERYDAY = load_pack_dir(CATALOG / "everyday")
CAPS = {"max_input_msat_per_million": 2_000_000, "max_output_msat_per_million": 8_000_000}


def _step(**kw) -> dict:
    step = {
        "operator": "node-a", "developer": "mesh-node@v1", "action_type": "decide", "taxonomy_version": "6",
        "verb": "buy", "action_class": "money.purchase", "action_id": "inference/t1",
        "timestamp": "2026-10-29T12:00:00Z", "target": "node-b",
        "input_msat_per_million": 1_000_000, "output_msat_per_million": 4_000_000,
    }
    return {k: v for k, v in {**step, **kw}.items() if v is not None}


def _profile(pack: str = "asg/inference", **parameters):
    return parse_profile({"format": PROFILE_FORMAT, "packs": [{"pack": pack, "parameters": parameters}]})


def _rate_cap(decision):
    return next(c for c in decision.constraints if c.id == "rate_cap")


def test_the_pack_ships_no_price_bound():
    (decision,) = decide_steps(INFERENCE, [_step(input_msat_per_million=2**53 - 1, output_msat_per_million=2**53 - 1)])
    assert decision.outcome == "allow"


@pytest.mark.parametrize(
    ("rates", "over"),
    [
        ({"input_msat_per_million": 2_000_001}, ["max_input_msat_per_million"]),
        ({"output_msat_per_million": 8_000_001}, ["max_output_msat_per_million"]),
        ({"input_msat_per_million": 3_000_000, "output_msat_per_million": 9_000_000},
         ["max_input_msat_per_million", "max_output_msat_per_million"]),
    ],
)
def test_a_rate_over_its_cap_is_refused_naming_the_cap(rates, over):
    (decision,) = decide_steps(INFERENCE, [_step(**rates)], profile=_profile(rate_cap=CAPS))
    assert decision.outcome == "deny"
    assert _rate_cap(decision).result == "fail"
    assert [o["cap"] for o in _rate_cap(decision).evidence["over"]] == over


def test_rates_at_their_caps_pass():
    (decision,) = decide_steps(
        INFERENCE, [_step(input_msat_per_million=2_000_000, output_msat_per_million=8_000_000)],
        profile=_profile(rate_cap=CAPS),
    )
    assert decision.outcome == "allow"
    assert _rate_cap(decision).evidence["over"] == []


@pytest.mark.parametrize("missing", ["input_msat_per_million", "output_msat_per_million"])
def test_a_rate_that_cannot_be_read_is_refused_naming_it(missing):
    (decision,) = decide_steps(INFERENCE, [_step(**{missing: None})], profile=_profile(rate_cap=CAPS))
    assert decision.outcome == "deny"
    assert decision.verdict == "not_evaluable"
    assert decision.not_evaluated_checks == ("rate_cap",)
    assert _rate_cap(decision).result == "n/a"
    assert _rate_cap(decision).evidence["missing_field"] == missing


def test_outside_its_classes_the_check_is_out_of_scope():
    action = Action(**{**_step(), "action_class": "communication.send"})
    out = check_rate_cap(action, max_input_msat_per_million=0, max_output_msat_per_million=0,
                         action_classes=["money.purchase"])
    assert out.constraint.result == "n/a"
    assert out.constraint.evidence["in_scope"] is False
    assert not out.fails_closed


def test_a_profile_may_set_one_cap_and_keep_the_other_default(tmp_path):
    installed = install_pack(INFERENCE, project_dir=tmp_path, profile=_profile(rate_cap={"max_input_msat_per_million": 5}))
    (wicket,) = (w for w in installed.resolved.configured_wickets(frozenset({"rate_cap"})))
    assert wicket.config["max_input_msat_per_million"] == 5
    assert wicket.config["max_output_msat_per_million"] == 2**53 - 1
    # The manifest still pins the wicket's own digest; only the profile moves.
    assert installed.manifest.wickets == install_pack(INFERENCE, project_dir=tmp_path / "plain").manifest.wickets


@pytest.mark.parametrize(
    "rate_cap",
    [{}, {"max_total_msat": 1}, {"max_input_msat_per_million": -1}, {"max_input_msat_per_million": True},
     {"max_input_msat_per_million": 2**53}, {"max_input_msat_per_million": "1"}],
)
def test_a_malformed_rate_cap_is_refused(rate_cap):
    with pytest.raises(PolicyManifestError) as err:
        _profile(rate_cap=rate_cap)
    assert err.value.reason == MALFORMED_PROFILE


def test_a_profile_cannot_set_rate_caps_for_a_pack_that_cites_no_rate_cap(tmp_path):
    with pytest.raises(PolicyManifestError) as err:
        install_pack(EVERYDAY, project_dir=tmp_path, profile=_profile("asg/everyday", rate_cap=CAPS))
    assert err.value.reason == PROFILE_UNKNOWN_PARAMETER
