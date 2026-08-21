# SPDX-License-Identifier: Apache-2.0
"""``capsule setup propose --drafter {deepeval,static}`` CLI wiring
([ldg-propose-live-drafting-mode]). The flag is opt-in and ``None`` by
default (argparse ``default=None``, not a mode name) so the pre-existing
deterministic path is reached with zero code added to it when the flag is
absent -- that's what ``test_default_drafter_makes_zero_model_calls``
below is a RED-before-green guard for: a regression that always
constructs ``DeepEvalRationaleDrafter`` (e.g. hoisting it out of the
``if args.drafter is not None`` branch) fails this test even though
``deepeval`` isn't installed in this repo's own dev environment, because
the patched constructor records that it was called.
"""
from __future__ import annotations

import io

import pytest
import yaml

from capsule_ledger.cli.main import main
from capsule_ledger.guards.signing import LocalSigner
from capsule_ledger.ledger import LedgerStore
from capsule_ledger.setup import prose_drafter as setup_prose_drafter
from capsule_ledger.setup.observe import ObserveRecorder


def _seed_ledger(ledger_dir) -> None:
    signer = LocalSigner(key_id="cli-key", secret=b"cli-secret")
    store = LedgerStore(ledger_dir)
    try:
        recorder = ObserveRecorder(
            ledger=store, signer=signer, operator="op", developer="dev", heartbeat_every=0, heartbeat_stream=io.StringIO()
        )
        events = []
        for i in range(1, 6):
            events.append({"kind": "dispatch", "dispatch_id": f"d{i}", "action_class": "remediation", "tool": "remediate"})
        for i in (1, 2):
            events.append({"kind": "confirmation", "commitment_ref": f"d{i}", "status": "confirmed"})
        recorder.run(events)
    finally:
        store.close()


def test_default_drafter_makes_zero_model_calls(tmp_path, monkeypatch):
    def _boom(self, *, model=None):
        raise AssertionError("DeepEvalRationaleDrafter must never be constructed when --drafter is absent")

    monkeypatch.setattr(setup_prose_drafter.DeepEvalRationaleDrafter, "__init__", _boom)

    ledger_dir = tmp_path / "ledger"
    _seed_ledger(ledger_dir)
    rc = main(["setup", "propose", "--project-dir", str(tmp_path), "--ledger", str(ledger_dir)])
    assert rc == 0


def test_static_drafter_flag_drafts_prose_leaving_verdicts_and_coverage_alone(tmp_path):
    off_dir = tmp_path / "off"
    on_dir = tmp_path / "on"
    ledger_off = off_dir / "ledger"
    ledger_on = on_dir / "ledger"
    _seed_ledger(ledger_off)
    _seed_ledger(ledger_on)

    out_off = tmp_path / "off.yaml"
    out_on = tmp_path / "on.yaml"
    rc_off = main(["setup", "propose", "--project-dir", str(off_dir), "--ledger", str(ledger_off), "--out", str(out_off)])
    rc_on = main(
        [
            "setup", "propose",
            "--project-dir", str(on_dir),
            "--ledger", str(ledger_on),
            "--out", str(out_on),
            "--drafter", "static",
        ]
    )
    assert rc_off == 0
    assert rc_on == 0

    data_off = yaml.safe_load(out_off.read_text())
    data_on = yaml.safe_load(out_on.read_text())
    assert data_off["records_observed"] == data_on["records_observed"]

    by_id_off = {p["outcome_id"]: p for p in data_off["proposals"]}
    by_id_on = {p["outcome_id"]: p for p in data_on["proposals"]}
    assert by_id_off.keys() == by_id_on.keys()
    for outcome_id, p_off in by_id_off.items():
        p_on = by_id_on[outcome_id]
        for field in ("forward_verdict", "backward_verdict", "declaration"):
            assert p_off[field] == p_on[field], f"{outcome_id}.{field} diverged"
        for field in ("coverage_n", "coverage_m", "missing_instrument", "refusal_reason_code"):
            assert p_off.get(field) == p_on.get(field), f"{outcome_id}.{field} diverged"
        # ... the one field the flag is FOR, and it did change:
        assert p_on["rationale"] != p_off["rationale"]
        assert p_off["rationale"] in p_on["rationale"]


def test_deepeval_drafter_missing_dependency_returns_1(tmp_path, capsys):
    if _deepeval_installed():
        pytest.skip("deepeval is installed in this environment -- the not-installed path isn't reachable here")
    ledger_dir = tmp_path / "ledger"
    _seed_ledger(ledger_dir)
    rc = main(["setup", "propose", "--project-dir", str(tmp_path), "--ledger", str(ledger_dir), "--drafter", "deepeval"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "drafter_dependency_missing" in err
    assert "capsule-ledger[judge]" in err


def _deepeval_installed() -> bool:
    try:
        import deepeval  # noqa: F401
    except ImportError:
        return False
    return True
