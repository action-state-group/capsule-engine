# SPDX-License-Identifier: Apache-2.0
"""An operator-set limit binds at the caps check (``policy/limits.py``).

The limit lives in a policy profile that a signed activation record binds;
the pack and its ``caps`` wicket keep their bytes and digests. The caps check
applies the operator's value where one is in force and the wicket's default
otherwise, and its sealed evidence names which (``limit_sources``) together
with the profile digest, so a reader holding the decision and the activation
record can tell an operator-set limit from a pack-default one without
re-running the check. A purchase over a limit is held for the account holder
(money.purchase names that approver role), never run. A lower limit takes
effect at its activation; a higher one waits out a 12-hour cooling-off, during which the evidence names the
pending raise. Neither the action's own timestamp nor a record appended
without the node's key can bring a raise forward."""
from __future__ import annotations

import copy
import hashlib
from pathlib import Path

import pytest
from agent_action_capsule import compute_capsule_id
from agent_action_capsule.canonical import json_digest
from capsule_ledger.ledger import LedgerStore

from capsule_engine.events import build_event_capsule
from capsule_engine.guards import Action, GuardDecision, GuardEngine, LocalSigner
from capsule_engine.guards.capsule import DENY, ESCALATE
from capsule_engine.guards.checks import check_caps
from capsule_engine.guards.checks.caps import LimitSource
from capsule_engine.packs import build_engine, install_pack, load_pack_dir, record_pack_activation
from capsule_engine.packs.contract_diff import diff_rulesets, ruleset_from_activation
from capsule_engine.policy import PROFILE_FORMAT, PolicyProfile, parse_profile
from capsule_engine.policy.activation import EVENT_MANIFEST_ACTIVATED, find_latest_activation
from capsule_engine.policy.errors import (
    ACTIVATION_OUT_OF_ORDER,
    ACTIVATION_UNVERIFIED,
    PROFILE_DIGEST_DRIFT,
    PROFILE_UNBOUND,
)
from capsule_engine.policy.limits import activation_records, read_caps_limits

REPO = Path(__file__).parent.parent
EVERYDAY_DIR = REPO / "capsule_engine" / "packs" / "catalog" / "everyday"
CAPS_V5 = REPO / "capsule_engine" / "guards" / "wickets" / "catalog_defs" / "caps.v5.yaml"
SIGNER = LocalSigner(key_id="operator-limits-test-key", secret=b"operator-limits-test-fixed-key")
# Same key id, different secret: a record made without this node's key.
OTHER_KEY = LocalSigner(key_id="operator-limits-test-key", secret=b"not-this-node-secret")
OPERATOR = "household-limits-fixture"

# The everyday pack (0.3.5) and its caps wicket as released: an operator
# limit must not move either.
EVERYDAY_PACK_DIGEST = "0400d22c4464e91728bac2d00ca3bf8551ec98cdd20ef18d803b5b4432d4e8f4"
CAPS_V5_WICKET_DIGEST = "2807e174dc7c817917621f90a53f3fa54992b76fe3ec28e8567f814b9e72a741"
CAPS_V5_FILE_SHA256 = "3f7ef8850e11d4a893ccaa7f85e1c9b2dde358980c946104dbc970f6989ad206"

# caps/5.0.0 defaults money.purchase to a 25.00 per-action limit and a
# 100.00 rolling-window limit.
PACK_PER_ACTION_DEFAULT = 2_500
PACK_WINDOW_DEFAULT = 10_000


def _profile(per_action: int | None = None, window: int | None = None) -> PolicyProfile:
    caps = {}
    if per_action is not None:
        caps["per_action_minor"] = {"money.purchase": per_action}
    if window is not None:
        caps["caps_minor"] = {"money.purchase": window}
    return parse_profile({"format": PROFILE_FORMAT, "packs": [{"pack": "asg/everyday", "parameters": {"caps": caps}}]})


def _purchase(name: str, amount_minor: int, timestamp: str) -> Action:
    return Action(
        verb="make_purchase",
        operator=OPERATOR,
        developer="household-assistant@v1",
        action_class="money.purchase",
        amount_minor=amount_minor,
        currency="USD",
        target=f"shop/{name}",
        rail="card",
        recurrence="one_time",
        action_id=f"make_purchase/limits-{name}",
        timestamp=timestamp,
    )


class _Household:
    """One project and one ledger; each ``activate`` installs the pack with a
    profile (or none) and records the activation at ``timestamp``."""

    def __init__(self, tmp_path: Path) -> None:
        self.pack = load_pack_dir(EVERYDAY_DIR)
        self.project = tmp_path / "project"
        self.ledger = LedgerStore(tmp_path / "ledger")
        self.installed = None

    def install(self, profile: PolicyProfile | None, mode: str = "enforce") -> None:
        self.installed = install_pack(self.pack, project_dir=self.project, mode=mode, profile=profile)

    def activate(self, profile: PolicyProfile | None, timestamp: str) -> None:
        self.install(profile)
        record_pack_activation(
            self.installed, ledger=self.ledger, operator=OPERATOR, developer="ops", signer=SIGNER, timestamp=timestamp
        )

    def engine(self, now: str):
        return build_engine(self.installed, ledger=self.ledger, signer_provider=lambda: SIGNER, clock=lambda: now)

    def check(self, name: str, amount_minor: int, timestamp: str, *, now: str | None = None):
        """``now`` is the engine's clock; by default the action is checked at
        the moment it is stamped."""
        decision = self.engine(now or timestamp).check(_purchase(name, amount_minor, timestamp), dry_run=False)
        caps = [c for c in decision.constraints if c.id == "caps"]
        return decision, (caps[0] if caps else None)

    def close(self) -> None:
        self.ledger.close()


@pytest.fixture
def household(tmp_path):
    h = _Household(tmp_path)
    yield h
    h.close()


def _sealed_caps_evidence_digest(decision: GuardDecision) -> str:
    (caps,) = [c for c in decision.capsule["constraints"] if c["id"] == "caps"]
    return caps["evidence_digest"]


# everyday asks before a first purchase from a merchant
# (counterparty_seen_before), and every shop here is new to a fresh ledger, so
# a purchase the limit allows still fails that check alone.
NEW_MERCHANT = "counterparty_seen_before"


def _failed(decision: GuardDecision) -> list[str]:
    return [c.id for c in decision.constraints if c.result == "fail"]


def _per_action(caps) -> LimitSource:
    return caps.evidence["limit_sources"]["per_action"]


# -- the acceptance case -------------------------------------------------------


def test_an_operator_set_5_dollar_limit_holds_a_6_dollar_purchase_for_approval(household):
    five = _profile(500)
    household.activate(five, "2026-10-08T09:00:00Z")

    decision, caps = household.check("six-dollars", 600, "2026-10-08T09:01:00Z")

    assert decision.outcome == ESCALATE
    assert caps.result == "fail"
    assert caps.evidence["tripped"] == [{"limit": "per_action", "threshold_minor": 500, "observed_minor": 600}]
    assert caps.evidence["per_action_cap_minor"] == 500
    assert caps.evidence["limit_sources"] == {
        "per_action": {"limit_source": "operator_profile", "profile_digest": five.profile_digest()},
        "window": {"limit_source": "definition_default"},
    }

    # The pack and its caps wicket are the released ones, byte for byte.
    assert household.installed.manifest.packs[0].digest == EVERYDAY_PACK_DIGEST
    assert household.pack.definition_digest() == EVERYDAY_PACK_DIGEST
    (caps_ref,) = [w for w in household.installed.manifest.wickets if w.wicket_id == "caps/5.0.0"]
    assert caps_ref.digest == CAPS_V5_WICKET_DIGEST
    assert hashlib.sha256(CAPS_V5.read_bytes()).hexdigest() == CAPS_V5_FILE_SHA256

    # Sealed: the decision capsule's capsule_id covers the caps evidence's
    # digest, and the profile digest the evidence names is the one the signed
    # activation binds, whose recorded values set 5.00 -- checkable from the
    # two records and the evidence alone.
    capsule = decision.capsule
    assert compute_capsule_id(capsule) == capsule["capsule_id"]
    assert json_digest(caps.evidence) == _sealed_caps_evidence_digest(decision)
    named = _per_action(caps)["profile_digest"]
    detail = activation_records(household.ledger)[-1].detail
    assert named == detail["profile"]["profile_digest"]
    assert parse_profile(detail["profile"]["values"]).profile_digest() == named
    assert detail["profile"]["values"]["packs"][0]["parameters"]["caps"]["per_action_minor"] == {"money.purchase": 500}
    assert capsule["asg_payload"]["manifest_digest"] == detail["manifest_digest"]

    # Evidence rewritten to claim the pack default no longer matches the seal.
    forged = copy.deepcopy(caps.evidence)
    forged["limit_sources"]["per_action"] = {"limit_source": "definition_default"}
    assert json_digest(forged) != _sealed_caps_evidence_digest(decision)


def test_with_no_profile_the_pack_default_applies_and_the_evidence_says_so(household):
    household.activate(None, "2026-10-08T09:00:00Z")

    held, caps = household.check("twenty-six-dollars", 2_600, "2026-10-08T09:01:00Z")
    assert held.outcome == ESCALATE
    assert caps.evidence["tripped"] == [
        {"limit": "per_action", "threshold_minor": PACK_PER_ACTION_DEFAULT, "observed_minor": 2_600}
    ]
    assert caps.evidence["limit_sources"] == {
        "per_action": {"limit_source": "definition_default"},
        "window": {"limit_source": "definition_default"},
    }

    allowed, caps = household.check("six-dollars", 600, "2026-10-08T09:02:00Z")
    assert _failed(allowed) == [NEW_MERCHANT]
    assert caps.evidence["per_action_cap_minor"] == PACK_PER_ACTION_DEFAULT
    assert caps.evidence["cap_minor"] == PACK_WINDOW_DEFAULT
    assert _per_action(caps) == {"limit_source": "definition_default"}


# -- lowering is immediate, raising waits 12 hours ----------------------------


def test_lowering_a_limit_takes_effect_at_its_activation(household):
    household.activate(None, "2026-10-08T09:00:00Z")
    five = _profile(500)
    household.activate(five, "2026-10-08T10:00:00Z")

    decision, caps = household.check("one-second-later", 600, "2026-10-08T10:00:01Z")
    assert decision.outcome == ESCALATE
    assert caps.evidence["per_action_cap_minor"] == 500
    assert _per_action(caps) == {"limit_source": "operator_profile", "profile_digest": five.profile_digest()}


def test_a_raise_inside_the_cooling_off_keeps_the_earlier_limit_and_says_why(household):
    five, forty = _profile(500), _profile(4_000)
    household.activate(five, "2026-10-08T09:00:00Z")
    household.activate(forty, "2026-10-08T10:00:00Z")

    held, caps = household.check("inside-cooling-off", 3_000, "2026-10-08T21:59:59Z")
    assert held.outcome == ESCALATE
    assert caps.evidence["tripped"] == [{"limit": "per_action", "threshold_minor": 500, "observed_minor": 3_000}]
    assert _per_action(caps) == {
        "limit_source": "operator_profile",
        "profile_digest": five.profile_digest(),
        "pending_raise": {
            "value_minor": 4_000,
            "effective_at": "2026-10-08T22:00:00Z",
            "limit_source": "operator_profile",
            "profile_digest": forty.profile_digest(),
        },
    }

    # Before the raise was activated, there is nothing pending to report.
    _, caps = household.check("before-the-raise", 100, "2026-10-08T09:30:00Z")
    assert _per_action(caps) == {"limit_source": "operator_profile", "profile_digest": five.profile_digest()}

    allowed, caps = household.check("after-cooling-off", 3_000, "2026-10-08T22:00:00Z")
    assert _failed(allowed) == [NEW_MERCHANT]
    assert caps.evidence["per_action_cap_minor"] == 4_000
    assert _per_action(caps) == {"limit_source": "operator_profile", "profile_digest": forty.profile_digest()}


def test_a_first_profile_above_the_pack_default_waits_too(household):
    forty = _profile(4_000)
    household.activate(forty, "2026-10-08T09:00:00Z")

    decision, caps = household.check("first-profile-raise", 3_000, "2026-10-08T10:00:00Z")
    assert decision.outcome == ESCALATE
    assert caps.evidence["per_action_cap_minor"] == PACK_PER_ACTION_DEFAULT
    assert _per_action(caps) == {
        "limit_source": "definition_default",
        "pending_raise": {
            "value_minor": 4_000,
            "effective_at": "2026-10-08T21:00:00Z",
            "limit_source": "operator_profile",
            "profile_digest": forty.profile_digest(),
        },
    }


def test_removing_a_lower_limit_is_a_raise_back_to_the_default_and_waits(household):
    household.activate(_profile(500), "2026-10-08T09:00:00Z")
    household.activate(None, "2026-10-08T10:00:00Z")

    decision, caps = household.check("profile-removed", 600, "2026-10-08T11:00:00Z")
    assert decision.outcome == ESCALATE
    assert caps.evidence["per_action_cap_minor"] == 500
    assert _per_action(caps)["pending_raise"] == {
        "value_minor": PACK_PER_ACTION_DEFAULT,
        "effective_at": "2026-10-08T22:00:00Z",
        "limit_source": "definition_default",
    }


def test_a_later_lowering_cancels_a_pending_raise(household):
    three = _profile(300)
    household.activate(_profile(500), "2026-10-08T09:00:00Z")
    household.activate(_profile(4_000), "2026-10-08T10:00:00Z")
    household.activate(three, "2026-10-08T11:00:00Z")
    in_force_now = {"limit_source": "operator_profile", "profile_digest": three.profile_digest()}

    # Right after the lowering, nothing is pending: the raise is gone, not queued.
    decision, caps = household.check("raise-cancelled-now", 400, "2026-10-08T12:00:00Z")
    assert decision.outcome == ESCALATE
    assert _per_action(caps) == in_force_now

    # When the cancelled raise would have taken effect, 3.00 is in force.
    decision, caps = household.check("raise-cancelled", 400, "2026-10-08T22:00:01Z")
    assert decision.outcome == ESCALATE
    assert caps.evidence["per_action_cap_minor"] == 300
    assert _per_action(caps) == in_force_now


def test_a_window_limit_follows_the_same_rule_independently(household):
    # One activation lowers the per-action limit and raises the window limit.
    both = _profile(per_action=500, window=20_000)
    household.activate(both, "2026-10-08T09:00:00Z")

    _, caps = household.check("both-limits", 400, "2026-10-08T10:00:00Z")
    assert caps.evidence["per_action_cap_minor"] == 500
    assert caps.evidence["cap_minor"] == PACK_WINDOW_DEFAULT
    assert caps.evidence["limit_sources"] == {
        "per_action": {"limit_source": "operator_profile", "profile_digest": both.profile_digest()},
        "window": {
            "limit_source": "definition_default",
            "pending_raise": {
                "value_minor": 20_000,
                "effective_at": "2026-10-08T21:00:00Z",
                "limit_source": "operator_profile",
                "profile_digest": both.profile_digest(),
            },
        },
    }


def test_what_waits_here_is_what_the_ruleset_diff_calls_a_loosening(household):
    household.activate(_profile(500), "2026-10-08T09:00:00Z")
    household.activate(_profile(4_000), "2026-10-08T10:00:00Z")
    household.activate(_profile(300), "2026-10-08T11:00:00Z")
    first, raised, lowered = (ruleset_from_activation(r.detail) for r in activation_records(household.ledger))
    assert {c.direction for c in diff_rulesets(first, raised).changes} == {"loosened"}
    assert {c.direction for c in diff_rulesets(raised, lowered).changes} == {"tightened"}
    # The raise waited (still pending an hour in); the lowering did not.
    _, caps = household.check("diff-raised", 100, "2026-10-08T10:30:00Z")
    assert caps.evidence["per_action_cap_minor"] == 500
    _, caps = household.check("diff-lowered", 100, "2026-10-08T11:00:00Z")
    assert caps.evidence["per_action_cap_minor"] == 300


# -- the clock, not the action's timestamp, decides when a raise is in force ---


def test_an_action_stamped_after_the_cooling_off_does_not_reach_the_raise_early(household):
    household.activate(_profile(500), "2026-10-08T09:00:00Z")
    household.activate(_profile(4_000), "2026-10-08T10:00:00Z")

    decision, caps = household.check("stamped-late", 3_000, "2026-10-09T00:00:00Z", now="2026-10-08T10:01:00Z")
    assert decision.outcome == ESCALATE
    assert caps.evidence["per_action_cap_minor"] == 500
    assert _per_action(caps)["pending_raise"]["effective_at"] == "2026-10-08T22:00:00Z"


def test_an_action_checked_after_the_cooling_off_but_stamped_inside_it_keeps_the_earlier_limit(household):
    household.activate(_profile(500), "2026-10-08T09:00:00Z")
    household.activate(_profile(4_000), "2026-10-08T10:00:00Z")

    decision, caps = household.check("checked-late", 3_000, "2026-10-08T21:00:00Z", now="2026-10-08T23:00:00Z")
    assert decision.outcome == ESCALATE
    assert caps.evidence["per_action_cap_minor"] == 500


def test_an_action_stamped_before_a_lowering_does_not_escape_it(household):
    household.activate(None, "2026-10-08T09:00:00Z")
    household.activate(_profile(500), "2026-10-08T10:00:00Z")

    decision, caps = household.check("stamped-early", 2_000, "2026-10-08T09:30:00Z", now="2026-10-08T10:01:00Z")
    assert decision.outcome == ESCALATE
    assert caps.evidence["per_action_cap_minor"] == 500


def test_an_engine_built_before_a_new_activation_fails_closed_until_rebuilt(household):
    household.activate(None, "2026-10-08T09:00:00Z")
    engine = household.engine("2026-10-08T10:01:00Z")
    before = engine.check(_purchase("before-lowering", 2_000, "2026-10-08T09:59:00Z"), dry_run=False)
    assert _failed(before) == [NEW_MERCHANT]
    household.activate(_profile(500), "2026-10-08T10:00:00Z")
    # The engine built before the lowering pins the old manifest: it denies
    # rather than run the old limit.
    decision = engine.check(_purchase("stale-engine", 2_000, "2026-10-08T10:01:00Z"), dry_run=False)
    assert decision.outcome == DENY
    assert [(c.id, c.result) for c in decision.constraints] == [("policy_binding", "fail")]

    decision, caps = household.check("fresh-engine", 2_000, "2026-10-08T10:01:00Z")
    assert decision.outcome == ESCALATE
    assert caps.evidence["per_action_cap_minor"] == 500


# -- fail closed: a decision is denied, recorded, never run on unbound limits ---


def _assert_policy_binding_denied(household, reason: str) -> None:
    decision, caps = household.check("unbound", 100, "2026-10-08T12:00:00Z")
    assert decision.outcome == DENY
    assert caps is None
    (binding,) = decision.constraints
    assert (binding.id, binding.result, binding.check_type) == ("policy_binding", "fail", "infra")
    assert f"({reason})" in binding.reason
    assert compute_capsule_id(decision.capsule) == decision.capsule["capsule_id"]


def test_a_profile_no_activation_binds_is_refused(household):
    household.install(_profile(500))
    _assert_policy_binding_denied(household, PROFILE_UNBOUND)


def test_a_profile_installed_after_the_latest_activation_is_refused(household):
    household.activate(None, "2026-10-08T09:00:00Z")
    household.install(_profile(500))
    _assert_policy_binding_denied(household, PROFILE_UNBOUND)


def test_the_same_profile_under_an_unrecorded_manifest_is_refused(household):
    five = _profile(500)
    household.activate(five, "2026-10-08T09:00:00Z")
    # Same profile digest, different manifest (the mode moved): not bound.
    household.install(five, mode="observe")
    _assert_policy_binding_denied(household, PROFILE_UNBOUND)


def test_dropping_a_profile_without_recording_it_is_refused(household):
    household.activate(_profile(500), "2026-10-08T09:00:00Z")
    household.install(None)
    _assert_policy_binding_denied(household, PROFILE_UNBOUND)


def _append_forged_activation(household, *, values: PolicyProfile, cited_digest: str, signer, timestamp: str) -> None:
    """An activation citing the installed manifest, carrying ``values`` under
    ``cited_digest``, chained to the latest real one."""
    detail = copy.deepcopy(dict(activation_records(household.ledger)[-1].detail))
    detail["profile"] = {"profile_digest": cited_digest, "values": values.canonical_dict()}
    capsule = build_event_capsule(
        operator=OPERATOR,
        developer="ops",
        signer=signer,
        event=EVENT_MANIFEST_ACTIVATED,
        detail=detail,
        timestamp=timestamp,
        chain_parent=find_latest_activation(household.ledger).capsule_id,
        chain_relation="epoch_opens",
    )
    household.ledger.append(capsule, consequential=False)


def test_an_activation_not_signed_with_the_nodes_key_is_refused(household):
    # Self-consistent values and digest, citing the installed manifest, and
    # timestamped long ago so a cooling-off would already be over.
    household.activate(_profile(500), "2026-10-07T09:00:00Z")
    big = _profile(900_000)
    _append_forged_activation(household, values=big, cited_digest=big.profile_digest(), signer=OTHER_KEY,
                              timestamp="2026-10-07T09:00:01Z")
    _assert_policy_binding_denied(household, ACTIVATION_UNVERIFIED)


def test_a_signed_activation_whose_values_do_not_match_its_digest_is_refused(household):
    five = _profile(500)
    household.activate(five, "2026-10-08T09:00:00Z")
    # Cites the pinned profile's digest, carries other values.
    _append_forged_activation(household, values=_profile(900_000), cited_digest=five.profile_digest(), signer=SIGNER,
                              timestamp="2026-10-08T09:00:01Z")
    _assert_policy_binding_denied(household, PROFILE_DIGEST_DRIFT)


def test_a_signed_activation_carrying_a_profile_the_manifest_does_not_pin_is_refused(household):
    household.activate(_profile(500), "2026-10-08T09:00:00Z")
    big = _profile(900_000)
    _append_forged_activation(household, values=big, cited_digest=big.profile_digest(), signer=SIGNER,
                              timestamp="2026-10-08T09:00:01Z")
    _assert_policy_binding_denied(household, PROFILE_UNBOUND)


def test_a_backdated_activation_is_refused(household):
    household.activate(_profile(500), "2026-10-08T09:00:00Z")
    household.activate(_profile(4_000), "2026-10-07T20:00:00Z")
    _assert_policy_binding_denied(household, ACTIVATION_OUT_OF_ORDER)


# -- the two ways to give an engine its limits do not mix ----------------------


def test_an_engine_refuses_limits_given_both_ways(household):
    household.activate(None, "2026-10-08T09:00:00Z")
    resolved = household.installed.resolved
    with pytest.raises(ValueError, match="not both"):
        GuardEngine(
            ledger=household.ledger,
            caps_fold=resolved.caps_fold(),
            signer_provider=lambda: SIGNER,
            caps_minor={"money.purchase": 500},
            caps_limits=lambda signer: read_caps_limits(resolved, household.ledger, signer),
        )


def test_the_caps_check_refuses_a_per_action_source_without_a_per_action_limit(household):
    household.activate(None, "2026-10-08T09:00:00Z")
    sources = {"window": {"limit_source": "definition_default"}, "per_action": {"limit_source": "definition_default"}}
    with pytest.raises(ValueError, match="per_action source"):
        check_caps(
            _purchase("mismatched-sources", 100, "2026-10-08T10:00:00Z"),
            household.ledger,
            definition=household.installed.resolved.caps_fold(),
            cap_minor=PACK_WINDOW_DEFAULT,
            limit_sources=sources,
        )
