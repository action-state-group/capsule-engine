# SPDX-License-Identifier: Apache-2.0
"""The pre-rename ``CAPSULE_LEDGER_*`` names still work for one release (#177), and an
install keeps its telemetry state across the directory rename."""
from __future__ import annotations

import pytest

from capsule_engine import packaging
from capsule_engine.envcompat import LEGACY_ALIASES, env_get
from capsule_engine.telemetry import consent, sink, state


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for new, old in LEGACY_ALIASES.items():
        monkeypatch.delenv(new, raising=False)
        monkeypatch.delenv(old, raising=False)


@pytest.mark.parametrize("new,old", sorted(LEGACY_ALIASES.items()))
def test_legacy_name_is_read_and_the_new_name_wins(monkeypatch, new, old):
    assert env_get(new) is None
    monkeypatch.setenv(old, "legacy")
    assert env_get(new) == "legacy"
    monkeypatch.setenv(new, "current")
    assert env_get(new) == "current"


def test_legacy_aliases_reach_each_reader(monkeypatch, tmp_path):
    monkeypatch.setenv("CAPSULE_LEDGER_ARM", "guards-only")
    assert packaging.current_arm() == packaging.GUARDS_ONLY
    monkeypatch.setenv("CAPSULE_LEDGER_TELEMETRY", "1")
    assert consent.is_opted_in()
    monkeypatch.setenv("CAPSULE_LEDGER_TELEMETRY_SINK_PATH", str(tmp_path / "s.jsonl"))
    assert isinstance(sink.default_sink(), sink.LocalJSONLSink)
    monkeypatch.setenv("CAPSULE_LEDGER_STATE_DIR", str(tmp_path / "d"))
    assert state.state_path() == tmp_path / "d" / "telemetry_state.json"


def test_state_dir_prefers_new_and_falls_back_to_the_old_one(monkeypatch, tmp_path):
    monkeypatch.setattr(state.Path, "home", lambda: tmp_path)
    base = tmp_path / ".local" / "state"
    new, old = base / "capsule-engine" / "telemetry_state.json", base / "capsule-ledger" / "telemetry_state.json"
    assert state.state_path() == new  # nothing yet: a fresh install uses the new directory
    old.parent.mkdir(parents=True)
    old.write_text("{}", encoding="utf-8")
    assert state.state_path() == old  # an existing install keeps its id
    new.parent.mkdir(parents=True)
    new.write_text("{}", encoding="utf-8")
    assert state.state_path() == new


def test_telemetry_disclosure_names_both_off_switches(monkeypatch):
    assert consent.ENV_VAR in consent.DISCLOSURE_TEXT and consent.LEGACY_ENV_VAR in consent.DISCLOSURE_TEXT
    monkeypatch.setenv(consent.LEGACY_ENV_VAR, "1")
    assert consent.is_opted_in()  # the old name alone still opts in...
    monkeypatch.setenv(consent.ENV_VAR, "0")
    assert not consent.is_opted_in()  # ...and the new name set to 0 turns it off
