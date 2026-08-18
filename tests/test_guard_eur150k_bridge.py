# SPDX-License-Identifier: Apache-2.0
"""The EUR150k bridge scenario: the real amaury sample ledger, capsule
cd0692b3 (a transfer_funds/€150,000 attempt already recorded as `blocked`
by a prior, independent policy engine). This guard, given the same action
fresh, independently re-evaluates it on its own caps evidence and -- per D2
(2026-08-05) -- escalates rather than blocks: `money.transfer` has an
`approver_role` configured (matching the HITL Bridge design's dev/GC
screens, real capsule cd0692b3, checkpoint #217), so a pure cap-exceeded
hold routes to a human (`hitl_dispatched`) instead of an automatic deny. The
new `hitl_dispatched` capsule still closes the stale `blocked` item with a
`supersedes`-chained decision capsule: the registry's own definition of
`supersedes` -- "Terminal transition over the parent -- resolution, expiry,
escalation close/replace the parent's open state" -- covers an escalation
closing/replacing a parent's open state, not only a terminal resolution.
`blocked` is a formal open-item verdict_class per the -02 spec's open-items
predicate; `hitl_dispatched` is itself an open item too, closed only by a
later, human-signed decision capsule (also `supersedes`-chained) that this
v0 does not yet build -- that's the HITL Bridge design's actual close, not
this test's scope.

`relation: resolves`, named in the T3 kickoff's acceptance text, still does
not exist in the chain.relation registry (only `confirms`, `supersedes`,
`epoch_opens`); `supersedes` remains the token used here -- see STATUS.md's
Needs decision (unrelated to D1/D2, not resolved by this task).
"""
from pathlib import Path

from capsule_ledger.guards import Action, GuardEngine, LocalSigner

FIXTURES = Path(__file__).parent / "fixtures"
AMAURY = FIXTURES / "amaury_sample_ledger.jsonl"

CD0692B3 = "cd0692b3349fadfeabe618008301b625059cc819eeb5ca1fb660699be9b6504e"
DEVELOPER = "procurement-agent@v1"
OPERATOR = "acme-research"
EUR150K_MINOR = 15_000_000  # EUR 150,000.00 in minor units (cents)
WEEKLY_CAP_MINOR = 10_000_000  # EUR 100,000.00


def test_eur150k_bridge_scenario_escalates_with_fold_evidence_and_supersedes_chain(store, caps_fold):
    n = store.import_jsonl(AMAURY)
    assert n == 4
    parent = store.fetch(CD0692B3)
    assert parent is not None
    assert parent.capsule["disposition"]["verdict_class"] == "blocked"

    signer = LocalSigner(key_id="bridge-key-1", secret=b"bridge-secret")
    engine = GuardEngine(
        ledger=store,
        caps_fold=caps_fold,
        signer_provider=lambda: signer,
        caps_minor={"money.transfer": WEEKLY_CAP_MINOR},
    )

    action = Action(
        verb="transfer_funds",
        operator=OPERATOR,
        developer=DEVELOPER,
        action_class="money.transfer",
        amount_minor=EUR150K_MINOR,
        currency="EUR",
        target="DE89370400440532013000",
    )

    decision = engine.check(action, chain_parent=CD0692B3, chain_relation="supersedes")

    # -- escalated, not blocked (D2, 2026-08-05) -------------------------
    assert decision.outcome == "escalate"
    assert not decision.dry_run
    assert not decision.degraded

    # -- with fold evidence -----------------------------------------------
    assert len(decision.fold_envelopes) == 1
    envelope = decision.fold_envelopes[0]
    assert envelope["fold"] == caps_fold.definition_digest()
    caps_constraint = next(c for c in decision.constraints if c.id == "caps")
    assert caps_constraint.result == "fail"
    assert caps_constraint.evidence["weekly_spend_minor"] == 0  # no prior *accepted* spend in this ledger
    assert caps_constraint.evidence["amount_minor"] == EUR150K_MINOR
    assert caps_constraint.evidence["cap_minor"] == WEEKLY_CAP_MINOR

    # -- decision capsule chained with the existing `supersedes` relation --
    capsule = decision.capsule
    assert capsule is not None
    assert capsule["chain"] == {"parent_capsule_id": CD0692B3, "relation": "supersedes"}
    assert capsule["disposition"]["decision"] == "hitl_dispatched"
    assert capsule["disposition"]["verdict_class"] == "hitl_dispatched"

    # -- the capsule is real: it verifies, and it closes the parent's open item --
    # (the parent's `blocked` open state is closed/replaced; the new
    # `hitl_dispatched` capsule is itself now the open item, closed only by
    # a later, human-signed decision -- out of this test's scope)
    result = store.verify(capsule["capsule_id"])
    assert result.ok, result.findings

    supersedes = [
        r
        for r in store.scan()
        if (r.capsule.get("chain") or {}).get("parent_capsule_id") == CD0692B3
        and (r.capsule.get("chain") or {}).get("relation") == "supersedes"
    ]
    assert len(supersedes) == 1
    assert supersedes[0].capsule_id == capsule["capsule_id"]
