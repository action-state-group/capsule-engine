# SPDX-License-Identifier: Apache-2.0
"""payments-safety pack, P2 acceptance: the full lifecycle the starter-packs
plan describes --

    capsule init --pack payments-safety      (observe: records, no enforcement)
    capsule thresholds propose                (fold observed traffic -> a proposal)
    capsule guard dry-run --proposals          (report: what would ALSO be held)
    capsule enforce --pack payments-safety    (human accepts -> observe -> enforce)

-- run end to end against real traffic, not mocked at any step: install in
observe mode, submit real decisions (dry_run=True, recorded but never
blocking), fold a proposer over that recorded traffic, build a report
showing the proposed-cap delta, accept the proposal, and confirm the SAME
kind of traffic now genuinely escalates (dry_run=False) under the accepted
cap -- the plan's own acceptance bar: "same traffic now actually denies/
escalates, records chain correctly."
"""
from __future__ import annotations

from pathlib import Path

from capsule_ledger.folds.loader import load_definition_file
from capsule_ledger.guards import Action, LocalSigner
from capsule_ledger.guards.capsule import ALLOW, ESCALATE
from capsule_ledger.ledger import LedgerStore
from capsule_ledger.packs import (
    build_engine,
    enforce_pack,
    install_pack,
    load_pack_dir,
    propose_thresholds,
    record_pack_activation,
)
from capsule_ledger.report.build import build_dry_run_report_with_proposal
from capsule_ledger.report.replay import load_records

PACK_DIR = Path(__file__).parent.parent / "capsule_ledger" / "packs" / "catalog" / "payments-safety"
FOLD_FILE = PACK_DIR / "folds" / "spend_weekly.yaml"

OPERATOR = "acme-checkout"
TREASURY_DEVELOPER = "checkout-shared-treasury@v1"
SECRET = b"p2-lifecycle-fixed-key"


def _signer() -> LocalSigner:
    return LocalSigner(key_id="p2-lifecycle-key", secret=SECRET)


def test_full_p2_lifecycle(tmp_path):
    pack = load_pack_dir(PACK_DIR)
    observe_ledger_dir = tmp_path / "observe-ledger"
    observe_project = tmp_path / "observe-project"

    # -- 1. capsule init --pack payments-safety (observe) --
    signer = _signer()
    ledger = LedgerStore(observe_ledger_dir)
    installed_observe = install_pack(pack, project_dir=observe_project, mode="observe")
    assert installed_observe.manifest.packs[0].mode == "observe"
    engine = build_engine(installed_observe, ledger=ledger, signer_provider=lambda: signer)

    init_activation = record_pack_activation(
        installed_observe,
        ledger=ledger,
        operator=OPERATOR,
        developer="capsule-init-tool",
        signer=signer,
        timestamp="2026-08-11T09:00:00Z",
        action_id="policy_manifest_activated/p2-lifecycle-init",
    )

    # -- observed traffic: two payments in the same weekly window, well
    # under a loose cap, so both genuinely ALLOW (accepted, not held) -- the
    # thing a proposer should be able to fold real spend out of. --
    a1 = Action(
        verb="dispatch_payout", operator=OPERATOR, developer=TREASURY_DEVELOPER,
        action_class="money.transfer",
        amount_minor=400_000, currency="EUR", target="vendor-forge-supplies/invoice-5001",
        action_id="dispatch_payout/p2-lifecycle-1", timestamp="2026-08-11T09:01:00Z",
    )
    d1 = engine.check(a1, dry_run=True)
    assert d1.outcome == ALLOW

    a2 = Action(
        verb="dispatch_payout", operator=OPERATOR, developer=TREASURY_DEVELOPER,
        action_class="money.transfer",
        amount_minor=200_000, currency="EUR", target="vendor-forge-supplies/invoice-5002",
        action_id="dispatch_payout/p2-lifecycle-2", timestamp="2026-08-11T09:02:00Z",
    )
    d2 = engine.check(a2, dry_run=True)
    assert d2.outcome == ALLOW
    ledger.close()

    # -- 2. capsule thresholds propose: fold the observed traffic --
    fold = load_definition_file(FOLD_FILE)
    records = load_records([observe_ledger_dir])
    proposal = propose_thresholds(pack, fold, records, action_class="money.transfer", percentile=100)
    # max-seen over the observed window: 400k, then 600k pooled -- p100 picks the max sample.
    assert proposal.proposed_cap_minor == 600_000
    assert proposal.rationale["strategy"] == "percentile"
    assert proposal.rationale["sample_size"] > 0

    # -- 3. capsule guard dry-run --proposals: report shows what would ALSO
    # be held under the proposed cap, with real capsule ids attached. --
    report = build_dry_run_report_with_proposal(
        [observe_ledger_dir],
        caps_fold=fold,
        proposed_caps_minor={"money.transfer": proposal.proposed_cap_minor},
        since=None,
        caps_minor={},  # nothing configured today -- everything currently allows
    )
    proposed_section = next(s for s in report.guards if s.guard_id == "caps_proposed")
    # p100 picks the observed max as the proposed cap -- by construction
    # nothing observed can exceed its own max, so the section is honestly
    # empty here. The non-trivial "this cap WOULD catch something new" case
    # is exercised below (step 5, with a tighter, human-accepted cap).
    assert proposed_section.rows == ()

    # -- 4. capsule enforce --pack payments-safety: accept a TIGHTER cap
    # (below what was observed) so the transition is provably real. --
    accepted_cap = 500_000
    enforce_project = tmp_path / "enforce-project"
    installed_enforce = enforce_pack(pack, project_dir=enforce_project, accepted={"money.transfer": accepted_cap})
    assert installed_enforce.manifest.packs[0].mode == "enforce"
    assert installed_enforce.resolved.caps_minor() == {"money.transfer": accepted_cap}
    # A real config change -- the manifest this produced is NOT the same as
    # the observe-mode one ("provable what was in force" through a change).
    assert installed_enforce.resolved.manifest_digest != installed_observe.resolved.manifest_digest

    enforce_ledger_dir = tmp_path / "enforce-ledger"
    enforce_ledger = LedgerStore(enforce_ledger_dir)
    enforce_activation = record_pack_activation(
        installed_enforce,
        ledger=enforce_ledger,
        operator=OPERATOR,
        developer="capsule-enforce-tool",
        signer=signer,
        timestamp="2026-08-11T09:10:00Z",
        action_id="policy_manifest_activated/p2-lifecycle-enforce",
    )

    # -- 5. same kind of traffic, now ACTUALLY escalates (dry_run=False),
    # under the newly-accepted, tighter cap -- a fresh ledger, so this
    # single payment alone (600k) must exceed the accepted cap (500k) on
    # its own for the assertion to mean anything. --
    enforced_engine = build_engine(installed_enforce, ledger=enforce_ledger, signer_provider=lambda: signer)
    a3 = Action(
        verb="dispatch_payout", operator=OPERATOR, developer=TREASURY_DEVELOPER,
        action_class="money.transfer",
        amount_minor=600_000, currency="EUR", target="vendor-forge-supplies/invoice-5003",
        action_id="dispatch_payout/p2-lifecycle-3", timestamp="2026-08-11T09:11:00Z",
    )
    d3 = enforced_engine.check(a3, dry_run=False)
    assert d3.outcome == ESCALATE
    assert d3.dry_run is False
    assert d3.capsule["asg_payload"]["checkpoint"].get("dry_run") is not True
    assert d3.capsule["asg_payload"]["manifest_digest"] == installed_enforce.resolved.manifest_digest

    enforce_ledger.close()

    # -- chain: both activations are real, ordered records; the enforce
    # transition itself is a recorded policy-manifest change (D2/plan). --
    assert init_activation["asg_payload"]["detail"]["packs"][0]["mode"] == "observe"
    assert enforce_activation["asg_payload"]["detail"]["packs"][0]["mode"] == "enforce"
