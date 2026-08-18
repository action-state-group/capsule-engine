# SPDX-License-Identifier: Apache-2.0
"""Opt-in-disclosed, aggregate-only telemetry: default-off, no PII/ledger
content in any payload, and the CLI hooks that turn real usage into the
six raw metric facts. See ``capsule_ledger/telemetry/`` for the modules under
test and ``test_two_arm_packaging.py`` for the arm-visibility side of T8.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from capsule_ledger.cli.main import main as cli_main
from capsule_ledger.telemetry import consent, events, funnel, record
from capsule_ledger.telemetry.sink import LocalJSONLSink, emit

FIXTURES = Path(__file__).parent / "fixtures"
NANDA = FIXTURES / "nanda_transaction_ledger.jsonl"


@pytest.fixture
def state_dir(tmp_path, monkeypatch):
    d = tmp_path / "state"
    monkeypatch.setenv("CAPSULE_LEDGER_STATE_DIR", str(d))
    return d


@pytest.fixture
def opted_out(monkeypatch):
    monkeypatch.delenv(consent.ENV_VAR, raising=False)


@pytest.fixture
def opted_in(monkeypatch):
    monkeypatch.setenv(consent.ENV_VAR, "1")


# -- consent / opt-in default -------------------------------------------------


def test_opted_out_by_default(opted_out):
    assert consent.is_opted_in() is False


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "on", "yes"])
def test_opt_in_values(monkeypatch, value):
    monkeypatch.setenv(consent.ENV_VAR, value)
    assert consent.is_opted_in() is True


@pytest.mark.parametrize("value", ["0", "false", "off", "", "nope"])
def test_non_opt_in_values_stay_off(monkeypatch, value):
    monkeypatch.setenv(consent.ENV_VAR, value)
    assert consent.is_opted_in() is False


def test_no_state_file_written_when_opted_out(opted_out, state_dir):
    record.record_guard_configured("full")
    record.record_guard_evaluated("full")
    record.record_install_seen("full")
    assert not state_dir.exists()


# -- payload shape: no PII, no ledger content, exactly the metric schema ----


def test_metric_event_payload_is_exactly_the_allowed_schema():
    event = events.m1_activation_event(install_id="abc-123", arm="full", activated=True)
    payload = event.to_dict()
    assert set(payload) == events.ALLOWED_FIELDS
    assert payload["metric"] == "m1_activation"
    assert payload["arm"] == "full"
    assert payload["value"] is True


@pytest.mark.parametrize(
    "builder",
    [
        events.m1_activation_event,
        events.m2_enforcement_on_event,
        events.m3_day14_alive_event,
        events.m5_evidence_pull_event,
    ],
)
def test_no_metric_event_can_carry_ledger_shaped_fields(builder):
    """Structural check: MetricEvent is a closed dataclass with exactly five
    fields, so no builder can be called with e.g. an ``agent=`` or
    ``amount_minor=`` kwarg -- it would raise TypeError, not silently
    smuggle the field through."""
    event = builder(install_id=str(uuid.uuid4()), arm="full")
    payload = event.to_dict()
    for forbidden in ("agent", "developer", "operator", "amount_minor", "capsule_id", "capsule", "why", "target"):
        assert forbidden not in payload


def test_install_id_is_a_random_uuid_not_derived_from_the_machine(state_dir, opted_out):
    from capsule_ledger.telemetry.state import load_state

    s1 = load_state()
    uuid.UUID(s1.install_id)  # raises if not a valid UUID
    import getpass
    import socket

    assert s1.install_id != socket.gethostname()
    assert s1.install_id != getpass.getuser()


def test_metric_event_is_a_closed_schema_no_extra_kwarg_accepted():
    """``MetricEvent`` is a plain frozen dataclass with exactly five fields
    -- passing anything else raises TypeError at construction, which is
    what makes "no PII in the payload" a property of the type rather than
    something every call site has to remember."""
    with pytest.raises(TypeError):
        events.MetricEvent(
            metric="m1_activation", arm="full", install_id="x", value=True, emitted_at="now", agent="nope"
        )


def test_emit_raises_if_a_payload_ever_carries_an_extra_field(tmp_path, monkeypatch):
    """The mutant: if ``sink.emit``'s field-set check were ever deleted or
    weakened, this confirms the guard clause actually fires against a
    widened payload -- not just that a well-formed event happens to pass.
    Simulated via ``to_dict`` since ``MetricEvent`` itself cannot construct
    a widened payload (previous test)."""

    def _leaking_to_dict(self):
        return {"metric": self.metric, "arm": self.arm, "install_id": self.install_id, "value": self.value,
                "emitted_at": self.emitted_at, "agent": "should never be here"}

    monkeypatch.setattr(events.MetricEvent, "to_dict", _leaking_to_dict)
    event = events.m1_activation_event(install_id="x", arm="full")
    with pytest.raises(ValueError):
        emit(event, opted_in=True, sink=LocalJSONLSink(tmp_path / "events.jsonl"))


def test_emit_is_a_noop_when_not_opted_in(tmp_path):
    event = events.m1_activation_event(install_id="x", arm="full")
    sink = LocalJSONLSink(tmp_path / "events.jsonl")
    assert emit(event, opted_in=False, sink=sink) is False
    assert not (tmp_path / "events.jsonl").exists()


# -- CLI hooks: real usage -> real facts, only when opted in -----------------


def test_guard_dry_run_configuring_a_cap_emits_m1_when_opted_in(opted_in, state_dir, tmp_path):
    sink_path = tmp_path / "events.jsonl"
    import os

    os.environ["CAPSULE_LEDGER_TELEMETRY_SINK_PATH"] = str(sink_path)
    try:
        out = tmp_path / "report.html"
        rc = cli_main(
            ["guard", "dry-run", "--ledger", str(NANDA), "--since", "7d", "--out", str(out), "--cap", "money.transfer=1"]
        )
        assert rc == 0
    finally:
        del os.environ["CAPSULE_LEDGER_TELEMETRY_SINK_PATH"]

    lines = [json.loads(line) for line in sink_path.read_text().splitlines()]
    metrics = {line["metric"] for line in lines}
    assert "m1_activation" in metrics
    assert "install_seen" in metrics
    for line in lines:
        assert set(line) == events.ALLOWED_FIELDS


def test_guard_dry_run_without_opt_in_emits_nothing(opted_out, state_dir, tmp_path):
    out = tmp_path / "report.html"
    rc = cli_main(
        ["guard", "dry-run", "--ledger", str(NANDA), "--since", "7d", "--out", str(out), "--cap", "money.transfer=1"]
    )
    assert rc == 0
    assert not state_dir.exists()


# -- the funnel report: raw values only, over synthetic or real events ------


def test_funnel_computes_raw_rates_from_synthetic_fixture():
    from importlib import resources

    text = resources.files("capsule_ledger.telemetry").joinpath("fixtures", "synthetic_events.jsonl").read_text()
    raw_events = [json.loads(line) for line in text.splitlines() if line.strip()]
    report = funnel.compute_funnel(raw_events)

    assert report.m1_activation["guards-only"].numerator == 4
    assert report.m1_activation["guards-only"].denominator == 10
    assert report.m1_activation["full"].numerator == 5
    assert report.m1_activation["full"].denominator == 10
    assert report.m6_viral_unit == 2  # tok-A (3 opens) and tok-B (2 opens); tok-C (1 open) doesn't count


def test_render_funnel_report_shows_exactly_six_metrics():
    report = funnel.compute_funnel([])
    text = funnel.render_funnel_report(report)
    for label in ("M1", "M2", "M3", "M4", "M5", "M6"):
        assert label in text
    # no seventh metric, no verdict language
    assert "M7" not in text
    for banned in ("PASS", "WORRY", "FAIL", "pass", "worry", "fail"):
        assert banned not in text


def test_funnel_with_no_data_reports_na_not_zero():
    report = funnel.compute_funnel([])
    assert report.m1_activation["full"].rate is None
    assert report.m4_evidence_tax is None


def test_cli_telemetry_funnel_dry_run(capsys):
    rc = cli_main(["telemetry", "funnel", "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "M1" in out and "M6" in out
    assert "dry run" in out.lower()


def test_cli_telemetry_status_shows_disclosure_and_off_by_default(opted_out, capsys):
    rc = cli_main(["telemetry", "status"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "OFF (default)" in out
    assert consent.ENV_VAR in out
