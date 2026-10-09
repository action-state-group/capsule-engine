# SPDX-License-Identifier: Apache-2.0
"""A capsulectl deal bundle replayed through the guard: each deal check is
the proposed action its own sealed record states (``action_class``,
``spend_minor``), and under caps/3.0.0 a purchase over the per-action limit
is denied while a cancel is never spend.

The fixtures under ``tests/fixtures/deal-bundles/`` are real
``capsulectl bundle --deal`` output from capsule-cli's deal flow: a $558.80
card purchase, then a cancel. In one the cancel returns that payment
(``money.refund``, ``amount_minor`` 55880, ``direction`` in); in the other
it returns part of it and matches no payment (``external_commitment.other``,
``cancelled_amount_minor`` 30000, ``direction`` in). Every check seals
``spend_minor``: 55880 for the payment, 0 for each cancel.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from agent_action_capsule import json_digest

from capsule_engine.cli.main import main as cli_main
from capsule_engine.policy import load_manifest_file, resolve_manifest
from capsule_engine.report.render import decode_fragment
from capsule_engine.report.replay import action_for_record, load_disclosed, load_records, replay

PACKAGE_DIR = Path(__file__).parent.parent / "capsule_engine"
FOLDS = PACKAGE_DIR / "folds" / "catalog_defs"
WICKETS = PACKAGE_DIR / "guards" / "wickets" / "catalog_defs"
FIXTURES = Path(__file__).parent / "fixtures" / "deal-bundles"
REFUND = FIXTURES / "deal-purchase-then-refund.bundle.json"
PARTIAL = FIXTURES / "deal-purchase-then-partial-cancel.bundle.json"
MANIFEST = FIXTURES / "caps-v3-manifest.yaml"


def _checks(bundle: Path, key: str = "capsule_id") -> dict[str, str]:
    """The checked action of each deal check in ``bundle``, by the check
    capsule's ``capsule_id`` (or ``action_id``)."""
    value = json.loads(bundle.read_text())
    out = {}
    for capsule in value["records"]:
        record = value["disclosures"][capsule["capsule_id"]]["agent_input"]
        if record["x-deal-v0"]["record_type"] == "check":
            out[capsule[key]] = record["body"]["action"]
    return out


def _resolved():
    return resolve_manifest(load_manifest_file(MANIFEST), fold_catalog_dir=FOLDS, wicket_catalog_dir=WICKETS)


def _replay_checks(bundle: Path):
    """Each deal check's (action, decision, caps constraint), replayed under
    the manifest's caps/3.0.0."""
    resolved = _resolved()
    result = replay(
        load_records([bundle]),
        caps_fold=resolved.caps_fold(),
        caps_minor=resolved.caps_minor(),
        per_action_minor=resolved.per_action_minor(),
        disclosed=load_disclosed([bundle]),
    )
    checks = _checks(bundle)
    out = {}
    for sourced in result.decisions:
        action = checks.get(sourced.record["capsule_id"])
        if action is not None:
            caps = next(c for c in sourced.decision.constraints if c.id == "caps")
            out[action] = (sourced.action, sourced.decision, caps)
    return out


def test_the_manifest_resolves_caps_v3():
    resolved = _resolved()
    assert resolved.per_action_minor()["money.purchase"] == 2_500
    assert resolved.per_action_minor()["external_commitment.other"] == 2_500
    assert "money.refund" not in resolved.caps_minor(), "a refund is money arriving: no spend cap covers it"
    assert resolved.caps_fold().fold_id == "spend.weekly/2.0.0"


def test_a_deal_check_is_the_action_its_sealed_record_states():
    records = load_records([REFUND])
    disclosed = load_disclosed([REFUND])
    assert len(records) == len(disclosed) == 10
    checks = _checks(REFUND)
    actions = {checks[r["capsule_id"]]: action_for_record(r, disclosed[r["capsule_id"]]) for r in records if r["capsule_id"] in checks}
    assert actions["pay"].action_class == "money.purchase"
    assert actions["pay"].amount_minor == 55_880
    assert actions["pay"].taxonomy_version == "2"
    assert actions["cancel"].action_class == "money.refund"
    assert actions["cancel"].amount_minor == 0, "a cancel is never spend: its refund amount is not the cap's amount"


def test_a_record_its_capsule_did_not_seal_is_never_read():
    records = load_records([REFUND])
    disclosed = load_disclosed([REFUND])
    pay = next(r for r in records if _checks(REFUND).get(r["capsule_id"]) == "pay")
    altered = copy.deepcopy(disclosed[pay["capsule_id"]])
    altered["body"]["spend_minor"] = 1
    action = action_for_record(pay, altered)
    assert action.action_class is None, "an altered record does not match the capsule's digest, so it is not read"
    assert action.amount_minor is None
    assert action_for_record(pay).action_class is None, "no record disclosed: the capsule alone names no class"


def test_caps_v3_holds_a_purchase_over_the_per_action_limit():
    for bundle in (REFUND, PARTIAL):
        action, decision, caps = _replay_checks(bundle)["pay"]
        assert action.action_class == "money.purchase"
        assert caps.result == "fail"
        assert caps.evidence["per_action_cap_minor"] == 2_500
        assert {"limit": "per_action", "threshold_minor": 2_500, "observed_minor": 55_880} in caps.evidence["tripped"]
        assert decision.outcome == "escalate"


@pytest.mark.parametrize(
    ("bundle", "action_class"),
    [(REFUND, "money.refund"), (PARTIAL, "external_commitment.other")],
)
def test_a_cancel_after_an_over_cap_purchase_passes_caps(bundle, action_class):
    """Stopping a commitment is never blocked by a spend cap: neither the
    cancel that returns the payment (a class no cap covers) nor the one that
    matches no payment (a capped class, with spend_minor 0)."""
    action, decision, caps = _replay_checks(bundle)["cancel"]
    assert action.action_class == action_class
    assert action.amount_minor == 0
    assert caps.result != "fail"
    assert not (caps.evidence or {}).get("tripped")


def _dry_run(tmp_path, capsys, bundle: Path, *extra: str) -> tuple[int, dict]:
    out = tmp_path / "report.html"
    rc = cli_main(["guard", "dry-run", "--ledger", str(bundle), "--since", "all", "--out", str(out), "--share", *extra])
    printed = capsys.readouterr().out
    url = next((line.strip() for line in printed.splitlines() if line.strip().startswith("file://")), "")
    return rc, decode_fragment(url.split("#", 1)[1]) if "#" in url else {}


def _rows(payload: dict) -> list[dict]:
    return [row for guard in payload.get("guards", []) for row in guard.get("rows", [])]


def test_cli_caps_from_manifest_denies_the_purchase_and_passes_the_cancel(tmp_path, capsys):
    for bundle in (REFUND, PARTIAL):
        rc, payload = _dry_run(tmp_path, capsys, bundle, "--manifest", str(MANIFEST), "--caps-from-manifest")
        assert rc == 0
        # A row's capsule is the guard's own decision for the step, under the
        # step's action_id.
        checks = _checks(bundle, key="action_id")
        held = {checks[row["capsule"]["action_id"]]: row for row in _rows(payload) if row["capsule"]["action_id"] in checks}
        assert "per_action limit 2500 exceeded by 55880" in held["pay"]["why"]
        assert "cancel" not in held, "the cancel passes caps"


def test_cli_default_applies_no_manifest_caps(tmp_path, capsys):
    """Without --caps-from-manifest the replay applies only --cap limits, as
    before: the manifest is cited, never applied."""
    rc, payload = _dry_run(tmp_path, capsys, REFUND, "--manifest", str(MANIFEST))
    assert rc == 0
    assert not any("per_action" in row["why"] for row in _rows(payload))


def test_cli_caps_from_manifest_needs_a_manifest(tmp_path, capsys):
    rc = cli_main(
        ["guard", "dry-run", "--ledger", str(REFUND), "--no-manifest", "--caps-from-manifest", "--out", str(tmp_path / "r.html")]
    )
    assert rc == 1
    assert "needs a manifest" in capsys.readouterr().err


def _bound_check(body: dict) -> tuple[dict, dict]:
    """A deal check record with ``body``, and a capsule that seals it (its
    ``agent_input_digest`` is the record's digest), so the reader reads it."""
    record = {
        "body": {"action": "pay", "action_class": "money.purchase", "taxonomy_version": "2", **body},
        "x-deal-v0": {"record_type": "check", "deal_id": "deal-0000000000000000", "seq": 2},
    }
    capsule = {
        "capsule_id": "0" * 64,
        "action_id": "deal-0000000000000000/2",
        "action_type": "fyi",
        "operator": "household-a",
        "developer": "capsulectl-deal",
        "timestamp": "2026-10-07T12:00:00Z",
        "model_attestation": {"compute_attestation": {"agent_input_digest": json_digest(record)}},
    }
    return capsule, record


def test_the_cap_amount_is_spend_minor_never_amount_minor():
    """Where a record's amount and its spend differ, the cap evaluates the
    spend: amount_minor is never the cap's amount."""
    capsule, record = _bound_check({"amount_minor": 55_880, "currency": "USD", "spend_minor": 100})
    action = action_for_record(capsule, record)
    assert action.action_class == "money.purchase"
    assert action.amount_minor == 100


def test_money_moving_in_is_never_the_caps_amount():
    """A record that says the money moved in is never spend, whatever its
    spend_minor says."""
    capsule, record = _bound_check(
        {"action": "cancel", "action_class": "money.refund", "amount_minor": 500, "spend_minor": 500, "direction": "in"}
    )
    assert action_for_record(capsule, record).amount_minor == 0
