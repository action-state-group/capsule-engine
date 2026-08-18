# SPDX-License-Identifier: Apache-2.0
"""The gating-decisions doc §1 failure-semantics table, as a literal test
matrix: one test per row. See docs/failure-semantics.md for the public
short version of the table each test below cites.
"""
from dataclasses import dataclass

from capsule_ledger.guards import Action, GuardEngine, LocalSigner, SigningKeyUnavailable
from capsule_ledger.guards.classes import classify
from capsule_ledger.ledger import LedgerAPI


@dataclass
class FlakyLedger:
    """Wraps a real LedgerAPI; raises on append() when armed, to simulate a
    disk-full/WAL-error failure. Everything else delegates straight through."""

    inner: LedgerAPI
    fail_next_append: bool = False

    def append(self, capsule, *, consequential=True):
        if self.fail_next_append:
            self.fail_next_append = False
            raise OSError("simulated disk full")
        return self.inner.append(capsule, consequential=consequential)

    def scan(self, query=None):
        return self.inner.scan(query)

    def fetch(self, capsule_id):
        return self.inner.fetch(capsule_id)

    def verify(self, capsule_id):
        return self.inner.verify(capsule_id)

    def find_gaps(self):
        return self.inner.find_gaps()

    def reindex(self):
        return self.inner.reindex()


def _action(**overrides):
    defaults = dict(verb="send_report", operator="acme", developer="agent@v1")
    defaults.update(overrides)
    return Action(**defaults)


# -- Row: Ledger append fails (disk full, WAL error) -------------------------


def test_ledger_append_fails_closed_and_records_degradation_on_recovery(store, caps_fold, signer):
    flaky = FlakyLedger(inner=store)
    engine = GuardEngine(ledger=flaky, caps_fold=caps_fold, signer_provider=lambda: signer)

    flaky.fail_next_append = True
    decision = engine.check(_action(action_class="money.transfer"))
    assert decision.outcome == "deny"
    assert decision.degraded is True
    assert decision.degradation_kind == "ledger_append"
    assert decision.capsule is None  # action does not dispatch; nothing could be persisted
    assert engine.open_degradations().get("ledger_append")

    # Recovery: the next successful call also appends a degradation record
    # naming the gap window -- not just a silent resumption.
    decision2 = engine.check(_action(action_class="money.transfer", target="second"))
    assert decision2.capsule is not None
    assert not engine.open_degradations()

    recovered = [
        r
        for r in store.scan()
        if (r.capsule.get("asg_payload") or {}).get("event") == "degradation_recovered"
    ]
    assert len(recovered) == 1
    assert recovered[0].capsule["asg_payload"]["detail"]["kind"] == "ledger_append"


# -- Row: Signing key unavailable --------------------------------------------


def test_signing_key_unavailable_fails_closed_key_id_on_recovery_and_operator_alert(store, caps_fold):
    key_available = {"value": False}
    signer = LocalSigner(key_id="rotated-key-7", secret=b"s3cret")

    def provider():
        if not key_available["value"]:
            raise SigningKeyUnavailable("no key material present")
        return signer

    engine = GuardEngine(ledger=store, caps_fold=caps_fold, signer_provider=provider)

    decision = engine.check(_action())
    assert decision.outcome == "deny"
    assert decision.degraded is True
    assert decision.degradation_kind == "signing_key"
    assert decision.capsule is None  # an unsigned record is not a record

    # Recovery.
    key_available["value"] = True
    decision2 = engine.check(_action(target="post-recovery"))
    assert decision2.capsule is not None
    # (a) the decision capsule that DOES get appended carries the signing key id.
    assert decision2.capsule["asg_signature"]["key_id"] == "rotated-key-7"
    assert not engine.open_degradations()

    # (b) the key-unavailable condition produces an operator-alert record on
    # recovery, not just a silent fail-closed.
    alerts = [
        r for r in store.scan() if (r.capsule.get("asg_payload") or {}).get("event") == "operator_alert"
    ]
    assert len(alerts) == 1
    assert alerts[0].capsule["asg_payload"]["detail"]["kind"] == "signing_key"


# -- Row: Local view unavailable or corrupt ----------------------------------


def test_local_view_unhealthy_fails_closed_then_rebuilds_and_resumes(store, caps_fold, signer):
    healthy = {"value": False}
    engine = GuardEngine(
        ledger=store, caps_fold=caps_fold, signer_provider=lambda: signer, view_healthy=lambda: healthy["value"]
    )

    decision = engine.check(_action())
    assert decision.outcome == "deny"
    assert decision.degraded is True
    assert decision.degradation_kind == "view_rebuild"
    assert decision.capsule is None

    healthy["value"] = True
    decision2 = engine.check(_action(target="post-rebuild"))
    assert decision2.capsule is not None
    assert decision2.outcome == "allow"


# -- Row: View is stale beyond the declared freshness bound ------------------


def test_stale_view_fails_closed_for_consequential_class(store, caps_fold, signer):
    engine = GuardEngine(
        ledger=store,
        caps_fold=caps_fold,
        signer_provider=lambda: signer,
        freshness_bound_ms=5_000,
        checkpoint_age_ms=lambda: 999_999,
    )
    decision = engine.check(_action(action_class="money.transfer"))
    assert decision.outcome == "deny"
    assert decision.capsule is not None  # staleness IS recorded in the outcome, unlike append/signing
    assert decision.checkpoint["age_ms"] == 999_999
    assert any(c.id == "freshness" and c.result == "fail" for c in decision.constraints)


def test_stale_view_fails_open_only_for_explicitly_configured_low_risk_class(store, caps_fold, signer):
    engine = GuardEngine(
        ledger=store,
        caps_fold=caps_fold,
        signer_provider=lambda: signer,
        freshness_bound_ms=5_000,
        checkpoint_age_ms=lambda: 999_999,
        fail_open_classes=frozenset({"info.query"}),
    )
    decision = engine.check(_action(action_class="info.query"))
    assert decision.outcome == "allow"
    assert decision.checkpoint.get("reduced_assurance") is True


# -- Row: Sidecar / engine unreachable ---------------------------------------


def test_engine_unreachable_fails_closed_by_default(store, caps_fold, signer):
    engine = GuardEngine(
        ledger=store, caps_fold=caps_fold, signer_provider=lambda: signer, engine_available=lambda: False
    )
    decision = engine.check(_action(action_class="money.transfer"))
    assert decision.outcome == "deny"
    assert any(c.id == "engine_availability" and c.result == "fail" for c in decision.constraints)


def test_engine_unreachable_fails_open_only_with_explicit_per_class_opt_in(store, caps_fold, signer):
    engine = GuardEngine(
        ledger=store,
        caps_fold=caps_fold,
        signer_provider=lambda: signer,
        engine_available=lambda: False,
        fail_open_classes=frozenset({"info.query"}),
    )
    decision = engine.check(_action(action_class="info.query"))
    assert decision.outcome == "allow"
    assert decision.checkpoint.get("reduced_assurance") is True


# -- Row: Anchor / witness unreachable ---------------------------------------


def test_anchor_witness_unreachable_never_blocks(store, caps_fold, signer):
    engine = GuardEngine(
        ledger=store, caps_fold=caps_fold, signer_provider=lambda: signer, witness_reachable=lambda: False
    )
    decision = engine.check(_action())
    assert decision.outcome == "allow"
    assert decision.checkpoint["anchor_status"] == "unanchored"
    assert decision.checkpoint["witness_reachable"] is False


# -- Row: Classification default (unclassified => consequential) ------------


def test_unclassified_action_defaults_to_consequential_fail_closed():
    ac = classify(None)
    assert ac.consequential is True
    assert ac.fail_open_allowed is False


def test_unclassified_action_cannot_fail_open_even_if_misconfigured(store, caps_fold, signer):
    engine = GuardEngine(
        ledger=store,
        caps_fold=caps_fold,
        signer_provider=lambda: signer,
        freshness_bound_ms=5_000,
        checkpoint_age_ms=lambda: 999_999,
        # A class name that can never match an unclassified action's
        # action_class (None) -- included to show fail-open needs an actual
        # class match, not just a non-empty fail_open_classes set.
        fail_open_classes=frozenset({"unclassified"}),
    )
    decision = engine.check(_action(action_class=None))
    assert decision.outcome == "deny"
