# SPDX-License-Identifier: Apache-2.0
"""payments-safety pack, P1 acceptance: install in observe mode, run a
deterministic two-agent-style scenario set (mirroring
``examples/two_agents.py``'s own overlap-spend/dedupe/refusal shapes, scaled
to this pack's action semantics) through a pack-installed ``GuardEngine``,
and confirm every one of the pack's obligations is exercised both ways
(caps allow AND escalate, dedupe pass AND fail, verify_before_dispatch pass-
by-absence AND fail) with pack-attributed, manifest-digest-stamped records.

"Observe mode: records, no enforcement" means every decision below is
computed and recorded via ``dry_run=True`` -- ``caps-escalate`` and
``dedupe-deny`` still show their real, would-be verdict; nothing here
asserts they were actually blocked (nothing in this repo blocks anything --
that is always the calling integration's job, see ``guards/engine.py``'s own
``check()`` docstring).

This test also regenerates the pack's checked-in fixture
(``packs/catalog/payments-safety/fixtures/mini_ledger.jsonl``) and proves it
is reproducible byte-for-byte from this scenario script -- the same
discipline ``docs/test-data.md`` holds every other checked-in fixture to.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from capsule_ledger.guards import Action, LocalSigner
from capsule_ledger.guards.capsule import ALLOW, DENY, ESCALATE
from capsule_ledger.ledger import LedgerStore
from capsule_ledger.packs import build_engine, install_pack, load_pack_dir, record_pack_activation

PACK_DIR = Path(__file__).parent.parent / "capsule_ledger" / "packs" / "catalog" / "payments-safety"
FIXTURE_PATH = PACK_DIR / "fixtures" / "mini_ledger.jsonl"

OPERATOR = "acme-checkout"
TREASURY_DEVELOPER = "checkout-shared-treasury@v1"
GAMMA_DEVELOPER = "checkout-agent-gamma@v1"
DELTA_DEVELOPER = "checkout-agent-delta@v1"
EPSILON_DEVELOPER = "checkout-agent-epsilon@v1"
ZETA_DEVELOPER = "checkout-agent-zeta@v1"
INSTALL_TOOL_DEVELOPER = "capsule-init-tool"

CAPS_MINOR = 1_000_000  # must match capsule_ledger/packs/catalog/payments-safety/pack.yaml's caps_minor

SIGNER_SECRET = b"payments-safety-acceptance-fixture-fixed-key"


def _signer() -> LocalSigner:
    return LocalSigner(key_id="payments-safety-fixture-key", secret=SIGNER_SECRET)


def _run_scenarios(ledger, *, project_dir):
    pack = load_pack_dir(PACK_DIR)
    installed = install_pack(pack, project_dir=project_dir, mode="observe")
    signer = _signer()
    engine = build_engine(installed, ledger=ledger, signer_provider=lambda: signer)

    activation = record_pack_activation(
        installed,
        ledger=ledger,
        operator=OPERATOR,
        developer=INSTALL_TOOL_DEVELOPER,
        signer=signer,
        timestamp="2026-08-10T09:00:00Z",
        action_id="policy_manifest_activated/payments-safety-fixture-install",
    )

    outcomes: dict[str, str] = {}
    capsules: dict[str, dict] = {}

    def _decide(name, action, expected):
        decision = engine.check(action, dry_run=True)
        if decision.outcome != expected:
            raise AssertionError(f"scenario {name!r}: expected {expected!r}, got {decision.outcome!r} ({decision.reason})")
        outcomes[name] = decision.outcome
        capsules[name] = decision.capsule
        return decision

    # -- caps-allow: first draw against a fresh weekly window, well under cap --
    _decide(
        "caps-allow",
        Action(
            verb="dispatch_payout",
            operator=OPERATOR,
            developer=TREASURY_DEVELOPER,
            action_class="money.transfer",
            amount_minor=650_000,
            currency="EUR",
            target="vendor-forge-supplies/invoice-2001",
            action_id="dispatch_payout/payments-safety-fixture-caps-allow",
            timestamp="2026-08-10T09:01:00Z",
        ),
        ALLOW,
    )

    # -- caps-escalate: pooled with the above (same developer, same window)
    # pushes projected spend past the pack's 1,000,000-minor-unit cap; caps
    # is the sole failing constraint and money.transfer has an approver_role
    # configured, so this escalates rather than hard-denying (D2). --
    _decide(
        "caps-escalate",
        Action(
            verb="dispatch_payout",
            operator=OPERATOR,
            developer=TREASURY_DEVELOPER,
            action_class="money.transfer",
            amount_minor=600_000,
            currency="EUR",
            target="vendor-forge-supplies/invoice-2002",
            action_id="dispatch_payout/payments-safety-fixture-caps-escalate",
            timestamp="2026-08-10T09:02:00Z",
        ),
        ESCALATE,
    )

    # -- dedupe-deny: the identical logical action submitted twice. A fresh
    # developer (its own weekly window) and a small amount keep caps out of
    # it, so dedupe is the sole failing constraint -> hard deny. --
    dedupe_action_kwargs = dict(
        verb="dispatch_payout",
        operator=OPERATOR,
        developer=GAMMA_DEVELOPER,
        action_class="money.transfer",
        amount_minor=100_000,
        currency="EUR",
        target="vendor-northwind-logistics/invoice-3001",
    )
    _decide(
        "dedupe-original",
        Action(
            **dedupe_action_kwargs,
            action_id="dispatch_payout/payments-safety-fixture-dedupe-original",
            timestamp="2026-08-10T09:03:00Z",
        ),
        ALLOW,
    )
    _decide(
        "dedupe-deny",
        Action(
            **dedupe_action_kwargs,
            action_id="dispatch_payout/payments-safety-fixture-dedupe-collision",
            timestamp="2026-08-10T09:04:00Z",
        ),
        DENY,
    )

    # -- verify-before-dispatch-refusal: cites a mandate capsule that was
    # never recorded -- an integrity failure, unconditional hard deny
    # regardless of approver_role (D2's "classes explicitly marked deny"
    # applies to non-caps failures too). --
    _decide(
        "verify-before-dispatch-refusal",
        Action(
            verb="dispatch_payout",
            operator=OPERATOR,
            developer=DELTA_DEVELOPER,
            action_class="money.transfer",
            amount_minor=50_000,
            currency="EUR",
            target="vendor-northwind-logistics/invoice-4001",
            cited_mandate_capsule_id="f" * 64,
            action_id="dispatch_payout/payments-safety-fixture-refusal",
            timestamp="2026-08-10T09:05:00Z",
        ),
        DENY,
    )

    # -- verify-before-dispatch-pass: cites a REAL, previously-recorded
    # capsule (this replay's own caps-allow decision) that DOES re-verify --
    # the genuine pass case, distinct from "n/a" (no citation at all). Every
    # scenario above with no cited_mandate_capsule_id resolves "n/a" for
    # this check, never "pass" -- without this one, verify_before_dispatch
    # would be a dead rule on its allow side (declared, never fires clean).
    _decide(
        "verify-before-dispatch-pass",
        Action(
            verb="dispatch_payout",
            operator=OPERATOR,
            developer=EPSILON_DEVELOPER,
            action_class="money.transfer",
            amount_minor=10_000,
            currency="EUR",
            target="vendor-northwind-logistics/invoice-4002",
            cited_mandate_capsule_id=capsules["caps-allow"]["capsule_id"],
            action_id="dispatch_payout/payments-safety-fixture-vbd-pass",
            timestamp="2026-08-10T09:06:00Z",
        ),
        ALLOW,
    )

    # -- caps-boundary-at-cap: a single payment for exactly the configured
    # cap (1,000,000 minor units). check_caps compares `projected <= cap`,
    # so this is the boundary itself -- must PASS, not fail. Golden-
    # decision-table discipline: an off-by-one here (projected < cap
    # instead of <=) would silently deny every payment that lands exactly
    # on a human-chosen round-number cap, the single most common real case.
    _decide(
        "caps-boundary-at-cap",
        Action(
            verb="dispatch_payout",
            operator=OPERATOR,
            developer=ZETA_DEVELOPER,
            action_class="money.transfer",
            amount_minor=CAPS_MINOR,
            currency="EUR",
            target="vendor-northwind-logistics/invoice-5001",
            action_id="dispatch_payout/payments-safety-fixture-caps-boundary",
            timestamp="2026-08-10T09:07:00Z",
        ),
        ALLOW,
    )

    records = list(ledger.scan())
    return installed, activation, outcomes, capsules, records


def test_payments_safety_pack_observe_mode_acceptance(tmp_path):
    ledger_dir = tmp_path / "ledger"
    project_dir = tmp_path / "project"
    store = LedgerStore(ledger_dir)
    try:
        installed, activation, outcomes, capsules, records = _run_scenarios(store, project_dir=project_dir)
        # Every capsule this pack produced must independently, structurally
        # re-verify -- not just "the outcome matched what we expected".
        # This is what caught, and now locks in, a real bug: action_type is
        # a base-spec field with a closed {fyi, decide} vocabulary (§5.1),
        # and this pack briefly wrote its own action-type name into it.
        verify_results = {name: store.verify(c["capsule_id"]) for name, c in capsules.items()}
    finally:
        store.close()
    for name, result in verify_results.items():
        assert result.ok, f"{name}: capsule failed to re-verify: {[f.detail for f in result.findings]}"

    # Every fixture scenario pack.yaml declares actually ran, at the
    # declared outcome -- the pack's own obligations, each exercised.
    pack = installed.pack
    declared = {s.id: s.outcome for s in pack.fixtures.scenarios}
    for scenario_id, expected_outcome in declared.items():
        assert outcomes[scenario_id] == expected_outcome

    # Pack-attributed: every decision capsule carries the manifest_digest
    # this exact pack install resolved to -- "what was in force" is
    # checkable directly off the record, not just claimed by this test.
    for name, capsule in capsules.items():
        assert capsule["asg_payload"]["manifest_digest"] == installed.resolved.manifest_digest, name

    # Observe mode: recorded-as-would-deny, not denied -- every decision is
    # tagged dry_run in its checkpoint.
    for name, capsule in capsules.items():
        assert capsule["asg_payload"]["checkpoint"]["dry_run"] is True, name

    # The activation capsule that opens this ledger's policy epoch names
    # this pack, at this digest, in observe mode.
    packs_detail = activation["asg_payload"]["detail"]["packs"]
    assert packs_detail == [{"pack_id": "asg/payments-safety/1.0.0", "digest": pack.definition_digest(), "mode": "observe"}]

    # 1 activation + 7 decisions (caps-allow, caps-escalate, dedupe-original,
    # dedupe-deny, verify-before-dispatch-refusal, verify-before-dispatch-pass,
    # caps-boundary-at-cap -- dedupe-original is not itself a declared
    # fixture scenario, it's what makes dedupe-deny real).
    assert len(records) == 8


def test_pack_gives_identical_verdicts_regardless_of_action_origin(tmp_path):
    """Architecture rule: a pack binds to normalized capsule fields only,
    never a framework object -- so it must decide identically whether the
    ``Action`` was built directly (raw ``emit()``-style integration code) or
    replayed from an already-recorded, foreign capsule (``Action.from_capsule()``
    -- the same path a LangChain-adapter-produced capsule would go through;
    the adapter itself lives in the sibling ``capsule-emit`` repo, so this
    proves the property this repo controls without a cross-repo langchain
    dependency: nothing in ``payments-safety``'s wickets/folds ever inspects
    where an ``Action`` came from)."""
    from capsule_ledger.guards.action import Action as ActionCls

    ledger_direct = LedgerStore(tmp_path / "ledger-direct")
    ledger_replayed = LedgerStore(tmp_path / "ledger-replayed")
    try:
        pack = load_pack_dir(PACK_DIR)
        installed_direct = install_pack(pack, project_dir=tmp_path / "project-direct", mode="observe")
        installed_replayed = install_pack(pack, project_dir=tmp_path / "project-replayed", mode="observe")
        signer = _signer()
        engine_direct = build_engine(installed_direct, ledger=ledger_direct, signer_provider=lambda: signer)
        engine_replayed = build_engine(installed_replayed, ledger=ledger_replayed, signer_provider=lambda: signer)

        # (a) raw, hand-built Action -- what a direct emit()-style integration does.
        direct_action = Action(
            verb="dispatch_payout",
            operator=OPERATOR,
            developer="adapter-parity-check@v1",
            action_class="money.transfer",
            amount_minor=250_000,
            currency="EUR",
            target="vendor-forge-supplies/invoice-9001",
            action_id="dispatch_payout/adapter-parity-check",
            timestamp="2026-08-10T09:10:00Z",
        )
        direct_decision = engine_direct.check(direct_action, dry_run=True)

        # (b) the same logical action, but arriving as a foreign capsule this
        # guard did not itself produce -- the shape any adapter (raw emit(),
        # LangChain's on_tool_end callback, or otherwise) hands back, replayed
        # via Action.from_capsule() exactly like a dry-run replay would.
        foreign_capsule = {
            "action_id": "dispatch_payout/adapter-parity-check",
            "operator": OPERATOR,
            "developer": "adapter-parity-check@v1",
            "action_type": "decide",  # spec §5.1: action_type is 'fyi'/'decide' only, never a pack action type
            "timestamp": "2026-08-10T09:10:00Z",
            "asg_payload": {
                "amount_minor": 250_000,
                "currency": "EUR",
                "target": "vendor-forge-supplies/invoice-9001",
            },
        }
        replayed_action = ActionCls.from_capsule(foreign_capsule, action_class="money.transfer")
        replayed_decision = engine_replayed.check(replayed_action, dry_run=True)

        assert direct_decision.outcome == replayed_decision.outcome == ALLOW
        assert [c.id for c in direct_decision.constraints] == [c.id for c in replayed_decision.constraints]
        assert [c.result for c in direct_decision.constraints] == [c.result for c in replayed_decision.constraints]
    finally:
        ledger_direct.close()
        ledger_replayed.close()


def test_fixture_is_reproducible_byte_for_byte(tmp_path):
    """The checked-in fixture is a real, recomputable export of this exact
    scenario script -- not hand-edited, not a placeholder. Regenerate it
    into a temp dir and diff against what's checked in."""
    if not FIXTURE_PATH.is_file():
        pytest.skip("fixture not yet generated -- run `python -m tests.test_pack_payments_safety_acceptance`")

    ledger_dir = tmp_path / "ledger"
    project_dir = tmp_path / "project"
    store = LedgerStore(ledger_dir)
    try:
        _, _, _, _, records = _run_scenarios(store, project_dir=project_dir)
    finally:
        store.close()

    regenerated = [json.dumps(r.capsule, separators=(",", ":")) for r in records]
    checked_in = FIXTURE_PATH.read_text().splitlines()
    assert regenerated == checked_in


def _regenerate_fixture() -> None:
    """Regenerate the checked-in mini_ledger.jsonl fixture from this exact
    scenario script (mirrors ``examples/two_agents.py --out``'s own
    fixture-export pattern)."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        store = LedgerStore(tmp / "ledger")
        try:
            _, _, _, _, records = _run_scenarios(store, project_dir=tmp / "project")
        finally:
            store.close()

    FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(FIXTURE_PATH, "w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record.capsule, separators=(",", ":")) + "\n")
    print(f"wrote {len(records)} record(s) to {FIXTURE_PATH}")


if __name__ == "__main__":
    _regenerate_fixture()
