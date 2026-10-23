# SPDX-License-Identifier: Apache-2.0
"""Design §10.2: "--period week|month as sugar over since/until." End-to-end
``capsule bundle --period ...`` wiring against the fixture ledger (records
dated 2026-07-06, see ``tests/fixtures/sample_ledger.jsonl``).

The pure calendar math (``period_bounds``/``apply_period``) moved to
``capsule_emit.period`` and is unit-tested there;
this file keeps only the integration tests that are genuinely this
package's own -- they exercise ``capsule bundle``, not the sugar in
isolation.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from capsule_engine.cli.main import main

FIXTURE_LEDGER = Path(__file__).parent / "fixtures" / "sample_ledger.jsonl"


def test_bundle_period_including_the_fixture_records_matches_them(tmp_path, monkeypatch):
    import capsule_emit.period as period_mod

    # The fixture ledger's records are dated 2026-07-06 -- pin --period month
    # to July 2026 so the sugar resolves to bounds that include them.
    monkeypatch.setattr(
        period_mod,
        "period_bounds",
        lambda p, anchor=None: ("2026-07-01T00:00:00.000000Z", "2026-07-31T23:59:59.999999Z"),
    )
    out_path = tmp_path / "bundle.json"
    rc = main(["bundle", "--ledger", str(FIXTURE_LEDGER), "--period", "month", "--out", str(out_path)])
    assert rc == 0
    bundle = json.loads(out_path.read_text())
    assert len(bundle["records"]) == 4
    assert bundle["query"]["period"] == "month"
    assert bundle["query"]["since"] == "2026-07-01T00:00:00.000000Z"
    assert bundle["query"]["until"] == "2026-07-31T23:59:59.999999Z"


def test_bundle_period_excluding_the_fixture_records_matches_none(tmp_path, monkeypatch):
    import capsule_emit.period as period_mod

    monkeypatch.setattr(
        period_mod,
        "period_bounds",
        lambda p, anchor=None: ("2026-08-01T00:00:00.000000Z", "2026-08-31T23:59:59.999999Z"),
    )
    out_path = tmp_path / "bundle.json"
    rc = main(["bundle", "--ledger", str(FIXTURE_LEDGER), "--period", "month", "--out", str(out_path)])
    assert rc == 0
    bundle = json.loads(out_path.read_text())
    assert bundle["records"] == []


def test_bundle_explicit_since_wins_over_period(tmp_path, monkeypatch):
    import capsule_emit.period as period_mod

    # Even though --period would resolve to August (excluding the fixture),
    # an explicit --since inside July must win and the records must match.
    monkeypatch.setattr(
        period_mod,
        "period_bounds",
        lambda p, anchor=None: ("2026-08-01T00:00:00.000000Z", "2026-08-31T23:59:59.999999Z"),
    )
    out_path = tmp_path / "bundle.json"
    rc = main(
        [
            "bundle",
            "--ledger",
            str(FIXTURE_LEDGER),
            "--period",
            "month",
            "--since",
            "2026-07-01T00:00:00.000000Z",
            "--out",
            str(out_path),
        ]
    )
    assert rc == 0
    bundle = json.loads(out_path.read_text())
    assert len(bundle["records"]) == 4
    assert bundle["query"]["since"] == "2026-07-01T00:00:00.000000Z"
    assert bundle["query"]["until"] == "2026-08-31T23:59:59.999999Z"


def test_period_choices_are_enforced_at_the_cli_level(capsys):
    with pytest.raises(SystemExit):
        main(["bundle", "--ledger", str(FIXTURE_LEDGER), "--period", "fortnight"])
    capsys.readouterr()
