# SPDX-License-Identifier: Apache-2.0
"""Design §10.2: "--period week|month as sugar over since/until." Unit tests
for the pure calendar math (``period_bounds``) plus end-to-end ``capsule
bundle --period ...`` wiring against the fixture ledger (records dated
2026-07-06, see ``tests/fixtures/sample_ledger.jsonl``)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from capsule_engine.cli.main import main
from capsule_engine.cli.period import apply_period, period_bounds

FIXTURE_LEDGER = Path(__file__).parent / "fixtures" / "sample_ledger.jsonl"

# A Wednesday, deliberately not a period boundary itself.
_ANCHOR = datetime(2026, 8, 19, 15, 30, 45, 123456, tzinfo=timezone.utc)


def test_period_bounds_week_is_the_monday_through_sunday_iso_week():
    since, until = period_bounds("week", anchor=_ANCHOR)
    assert since == "2026-08-17T00:00:00.000000Z"
    assert until == "2026-08-23T23:59:59.999999Z"


def test_period_bounds_month_is_the_calendar_month():
    since, until = period_bounds("month", anchor=_ANCHOR)
    assert since == "2026-08-01T00:00:00.000000Z"
    assert until == "2026-08-31T23:59:59.999999Z"


def test_period_bounds_month_rolls_over_the_year_boundary():
    since, until = period_bounds("month", anchor=datetime(2026, 12, 10, tzinfo=timezone.utc))
    assert since == "2026-12-01T00:00:00.000000Z"
    assert until == "2026-12-31T23:59:59.999999Z"


def test_period_bounds_rejects_an_unknown_period():
    with pytest.raises(ValueError):
        period_bounds("fortnight", anchor=_ANCHOR)


class _Args:
    def __init__(self, *, period=None, since=None, until=None):
        self.period = period
        self.since = since
        self.until = until


def test_apply_period_fills_in_since_and_until(monkeypatch):
    import capsule_engine.cli.period as period_mod

    monkeypatch.setattr(period_mod, "period_bounds", lambda p, anchor=None: ("SINCE-STUB", "UNTIL-STUB"))
    args = _Args(period="month")
    apply_period(args)
    assert args.since == "SINCE-STUB"
    assert args.until == "UNTIL-STUB"


def test_apply_period_never_overrides_an_explicit_since(monkeypatch):
    import capsule_engine.cli.period as period_mod

    monkeypatch.setattr(period_mod, "period_bounds", lambda p, anchor=None: ("SINCE-STUB", "UNTIL-STUB"))
    args = _Args(period="week", since="2026-01-01T00:00:00.000000Z")
    apply_period(args)
    assert args.since == "2026-01-01T00:00:00.000000Z"
    assert args.until == "UNTIL-STUB"


def test_apply_period_is_a_noop_without_period():
    args = _Args()
    apply_period(args)
    assert args.since is None
    assert args.until is None


def test_bundle_period_including_the_fixture_records_matches_them(tmp_path, monkeypatch):
    import capsule_engine.cli.period as period_mod

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
    import capsule_engine.cli.period as period_mod

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
    import capsule_engine.cli.period as period_mod

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
