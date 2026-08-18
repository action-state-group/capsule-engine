# SPDX-License-Identifier: Apache-2.0
"""Decision capsules carry the manifest digest they decided under (task
acceptance: "a real test proving this, not just a schema field that's
never populated"). Real ``GuardEngine.check()`` calls, real capsules."""
from __future__ import annotations

from capsule_ledger.guards import Action, GuardEngine


def test_decision_capsule_carries_the_configured_manifest_digest(store, caps_fold, signer, resolved_manifest):
    engine = GuardEngine(
        ledger=store,
        caps_fold=caps_fold,
        signer_provider=lambda: signer,
        manifest_digest=resolved_manifest.manifest_digest,
    )
    action = Action(verb="info_lookup", operator="acme", developer="dev1", action_class="info.query")
    decision = engine.check(action)

    assert decision.capsule is not None
    assert decision.capsule["asg_payload"]["manifest_digest"] == resolved_manifest.manifest_digest


def test_decision_capsule_omits_manifest_digest_when_none_configured(store, caps_fold, signer):
    engine = GuardEngine(ledger=store, caps_fold=caps_fold, signer_provider=lambda: signer)
    action = Action(verb="info_lookup", operator="acme", developer="dev1", action_class="info.query")
    decision = engine.check(action)

    assert decision.capsule is not None
    assert "manifest_digest" not in decision.capsule["asg_payload"]


def test_infra_deny_decision_also_carries_the_manifest_digest(store, caps_fold, signer, resolved_manifest):
    """The staleness/engine-unreachable fail-closed path builds its own
    decision capsule (``GuardEngine._infra_deny``) -- confirm it threads the
    manifest digest too, not just the main happy-path branch."""
    engine = GuardEngine(
        ledger=store,
        caps_fold=caps_fold,
        signer_provider=lambda: signer,
        manifest_digest=resolved_manifest.manifest_digest,
        checkpoint_age_ms=lambda: 999_999,  # far beyond the default freshness bound
    )
    action = Action(verb="transfer_funds", operator="acme", developer="dev1", action_class="money.transfer")
    decision = engine.check(action)

    assert decision.outcome == "deny"
    assert decision.capsule is not None
    assert decision.capsule["asg_payload"]["manifest_digest"] == resolved_manifest.manifest_digest


def test_different_manifests_produce_different_cited_digests(store, caps_fold, signer, resolved_manifest):
    """The mutant: swap in a different (but still valid) manifest digest and
    confirm the capsule cites the one it was actually built under, not a
    fixed/hardcoded value."""
    engine_a = GuardEngine(
        ledger=store, caps_fold=caps_fold, signer_provider=lambda: signer, manifest_digest=resolved_manifest.manifest_digest
    )
    engine_b = GuardEngine(
        ledger=store, caps_fold=caps_fold, signer_provider=lambda: signer, manifest_digest="f" * 64
    )
    action = Action(verb="info_lookup", operator="acme", developer="dev1", action_class="info.query")

    decision_a = engine_a.check(action)
    decision_b = engine_b.check(action)

    assert decision_a.capsule["asg_payload"]["manifest_digest"] == resolved_manifest.manifest_digest
    assert decision_b.capsule["asg_payload"]["manifest_digest"] == "f" * 64
    assert decision_a.capsule["asg_payload"]["manifest_digest"] != decision_b.capsule["asg_payload"]["manifest_digest"]
