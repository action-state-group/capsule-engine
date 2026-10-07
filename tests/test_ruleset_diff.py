# SPDX-License-Identifier: Apache-2.0
"""``contract_diff`` re-aimed at RuleSets: what a policy change does to the
rules in force, per rule, as direction (tightened / loosened / changed /
neutral) and compatibility (breaking / non_breaking), from the same rules
and the same fail-closed default the Evidence Contract diff uses."""
from __future__ import annotations

from pathlib import Path

import pytest
from capsule_ledger.ledger import LedgerStore

from capsule_engine.guards import LocalSigner
from capsule_engine.packs import install_pack, load_pack_dir, record_pack_activation
from capsule_engine.packs.contract_diff import (
    BREAKING,
    NON_BREAKING,
    Change,
    ContractDiff,
    diff_contracts,
    diff_rulesets,
    render_diff,
    ruleset_from_activation,
)
from capsule_engine.policy import PROFILE_FORMAT, parse_profile

EVERYDAY_DIR = Path(__file__).parent.parent / "capsule_engine" / "packs" / "catalog" / "everyday"
EVERYDAY = "asg/everyday/0.1.0"
D1, D2 = "1" * 64, "2" * 64


def _ruleset(*, per_action=None, caps=None, pack_id=EVERYDAY, digest=D1, mode="enforce", **extra) -> dict[str, object]:
    caps_params = {}
    if per_action is not None:
        caps_params["per_action_minor"] = per_action
    if caps is not None:
        caps_params["caps_minor"] = caps
    out = {"packs": [{"pack_id": pack_id, "digest": digest, "mode": mode}], **extra}
    if caps_params:
        out["profile"] = {"format": PROFILE_FORMAT,
                          "packs": [{"pack": pack_id.rsplit("/", 1)[0], "parameters": {"caps": caps_params}}]}
    return out


def _results(diff: ContractDiff) -> dict[str, tuple[str, str]]:
    return {r["path"]: (r["direction"], r["compatibility"]) for r in diff.results()}


PURCHASE = "profile/packs[asg/everyday]/parameters/caps/per_action_minor/money.purchase"


def test_a_lower_limit_reads_tightened_and_breaking():
    diff = diff_rulesets(_ruleset(per_action={"money.purchase": 2_500}), _ruleset(per_action={"money.purchase": 1_500}))
    assert _results(diff) == {PURCHASE: ("tightened", BREAKING)}
    assert diff.breaking


def test_a_higher_limit_reads_loosened_and_non_breaking_but_is_still_reported():
    diff = diff_rulesets(_ruleset(per_action={"money.purchase": 2_500}), _ruleset(per_action={"money.purchase": 10_000}))
    assert _results(diff) == {PURCHASE: ("loosened", NON_BREAKING)}
    assert not diff.breaking
    (result,) = diff.results()
    assert result["reason"] == "per-action limit for money.purchase: 2500 -> 10000"


def test_each_rule_gets_its_own_direction():
    diff = diff_rulesets(
        _ruleset(per_action={"money.purchase": 2_500, "money.transfer": 2_500}, caps={"money.purchase": 10_000}),
        _ruleset(per_action={"money.purchase": 1_500, "money.transfer": 4_000}, caps={"money.purchase": 10_000}),
    )
    assert _results(diff) == {
        PURCHASE: ("tightened", BREAKING),
        "profile/packs[asg/everyday]/parameters/caps/per_action_minor/money.transfer": ("loosened", NON_BREAKING),
    }


def test_a_value_set_on_one_side_only_is_changed_and_breaking():
    # The other side falls back to the pack default, which a profile does not carry.
    diff = diff_rulesets(_ruleset(per_action={"money.purchase": 2_500, "money.transfer": 2_500}),
                         _ruleset(per_action={"money.purchase": 2_500}))
    assert _results(diff) == {
        "profile/packs[asg/everyday]/parameters/caps/per_action_minor/money.transfer": ("changed", BREAKING)}


def test_a_removed_profile_is_changed_per_class():
    diff = diff_rulesets(_ruleset(per_action={"money.purchase": 2_500}), _ruleset())
    assert _results(diff) == {PURCHASE: ("changed", BREAKING)}


def test_same_version_label_with_new_content_is_version_reused_and_refused():
    diff = diff_rulesets(_ruleset(digest=D1), _ruleset(digest=D2))
    kinds = {(c.path, c.kind) for c in diff.changes}
    assert ("packs[asg/everyday]/version", "version_reused") in kinds
    assert diff.breaking and diff.refused


def test_a_new_version_with_new_content_is_not_refused_but_its_direction_is_unknown_here():
    diff = diff_rulesets(_ruleset(digest=D1), _ruleset(pack_id="asg/everyday/0.2.0", digest=D2))
    assert _results(diff) == {
        "packs[asg/everyday]/digest": ("changed", BREAKING),
        "packs[asg/everyday]/version": ("neutral", NON_BREAKING),
    }
    assert not diff.refused


def test_a_new_version_label_on_the_same_content_is_neutral():
    diff = diff_rulesets(_ruleset(digest=D1), _ruleset(pack_id="asg/everyday/0.2.0", digest=D1))
    assert _results(diff) == {"packs[asg/everyday]/version": ("neutral", NON_BREAKING)}


def test_observe_to_enforce_is_tightened_and_back_is_loosened():
    up = diff_rulesets(_ruleset(mode="observe"), _ruleset(mode="enforce"))
    down = diff_rulesets(_ruleset(mode="enforce"), _ruleset(mode="observe"))
    assert _results(up) == {"packs[asg/everyday]/mode": ("tightened", BREAKING)}
    assert _results(down) == {"packs[asg/everyday]/mode": ("loosened", NON_BREAKING)}


def test_adding_a_pack_is_tightened_and_removing_one_is_loosened():
    one = _ruleset()
    two = _ruleset()
    two["packs"].append({"pack_id": "asg/payments-safety/1.0.0", "digest": D2, "mode": "enforce"})
    assert _results(diff_rulesets(one, two)) == {"packs[asg/payments-safety]": ("tightened", BREAKING)}
    assert _results(diff_rulesets(two, one)) == {"packs[asg/payments-safety]": ("loosened", NON_BREAKING)}


@pytest.mark.parametrize(
    ("a", "b", "path"),
    [
        # A field nothing has a rule for.
        (_ruleset(), _ruleset(note="household-a"), "note"),
        (_ruleset(), {**_ruleset(), "packs": [{**_ruleset()["packs"][0], "region": "eu"}]}, "packs[asg/everyday]/region"),
        # A profile parameter no rule ranks.
        (_ruleset(per_action={"money.purchase": 2_500}),
         {**_ruleset(per_action={"money.purchase": 2_500}),
          "profile": {"format": PROFILE_FORMAT, "packs": [{"pack": "asg/everyday", "parameters": {
              "caps": {"per_action_minor": {"money.purchase": 2_500}}, "dedupe": {"window_days": {"any": 7}}}}]}},
         "profile/packs[asg/everyday]/parameters/dedupe"),
        # A limit that is not an integer is never ranked.
        (_ruleset(per_action={"money.purchase": 2_500}), _ruleset(per_action={"money.purchase": True}), PURCHASE),
        (_ruleset(per_action={"money.purchase": 2_500}), _ruleset(per_action={"money.purchase": "1500"}), PURCHASE),
    ],
)
def test_anything_without_a_rule_is_changed_and_breaking(a, b, path):
    diff = diff_rulesets(a, b)
    assert _results(diff) == {path: ("changed", BREAKING)}


def test_the_diff_reads_two_real_activations(tmp_path):
    pack = load_pack_dir(EVERYDAY_DIR)
    signer = LocalSigner(key_id="ruleset-diff-key", secret=b"ruleset-diff-fixed-key")
    ledger = LedgerStore(tmp_path / "ledger")
    details = []
    for n, limit in enumerate((2_500, 1_500)):
        profile = parse_profile({"format": PROFILE_FORMAT, "packs": [
            {"pack": "asg/everyday", "parameters": {"caps": {"per_action_minor": {"money.purchase": limit}}}}]})
        installed = install_pack(pack, project_dir=tmp_path / "p", mode="enforce", profile=profile)
        capsule = record_pack_activation(installed, ledger=ledger, operator="household-a", developer="ops",
                                         signer=signer, timestamp=f"2026-10-06T09:0{n}:00Z")
        details.append(capsule["asg_payload"]["detail"])
    ledger.close()
    diff = diff_rulesets(ruleset_from_activation(details[0]), ruleset_from_activation(details[1]))
    assert _results(diff) == {PURCHASE: ("tightened", BREAKING)}


def test_a_pre_profile_activation_reads_as_a_ruleset_without_a_profile():
    detail = {"manifest_id": "m/1.0.0", "manifest_digest": D1, "folds": [], "wickets": [],
              "packs": [{"pack_id": EVERYDAY, "digest": D1, "mode": "observe"}]}
    assert ruleset_from_activation(detail) == {"packs": [{"pack_id": EVERYDAY, "digest": D1, "mode": "observe"}]}


def test_contract_diff_changes_carry_a_direction_too():
    a = {"id": "c", "version": "1.0.0", "requirements": [{"id": "r", "statement": "s"}]}
    b = {**a, "version": "1.1.0"}
    (result,) = diff_contracts(a, b).results()
    assert (result["path"], result["direction"], result["compatibility"]) == ("version", "neutral", NON_BREAKING)
    reused = diff_contracts(a, {**a, "requirements": [{"id": "r", "statement": "t"}]})
    assert ("version", "changed", BREAKING) in {(r["path"], r["direction"], r["compatibility"]) for r in reused.results()}
    assert reused.refused


# -- rendering ---------------------------------------------------------------


def test_render_formats_a_hand_built_diff_without_recomputing_it():
    # A diff no differ would produce from these pins: rendering must show
    # exactly what the object says.
    diff = ContractDiff(
        a={"ruleset_digest": "a" * 64},
        b={"ruleset_digest": "b" * 64},
        changes=[
            Change("x/limit", "loosened", "limit for x: 1 -> 2"),
            Change("y/pack", "version_reused", "one version label now names two different contents"),
            Change("z/limit", "tightened", "limit for z: 9 -> 3"),
        ],
    )
    assert render_diff(diff) == "\n".join([
        f"before: {'a' * 64}",
        f"after:  {'b' * 64}",
        "  loosened   non_breaking  x/limit: limit for x: 1 -> 2",
        "  changed    breaking      y/pack: one version label now names two different contents",
        "  tightened  breaking      z/limit: limit for z: 9 -> 3",
        "REFUSED: a version label names different content (version_reused)",
    ])


def test_render_of_an_empty_diff_says_identical():
    diff = ContractDiff(a={"ruleset_digest": "a" * 64}, b={"ruleset_digest": "a" * 64}, changes=[])
    assert render_diff(diff).splitlines()[-1] == "identical"

