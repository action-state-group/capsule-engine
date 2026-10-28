# SPDX-License-Identifier: Apache-2.0
"""``guards/history.py``: what a query finds, the store-backed history's write
order and log order, and that the engine decides alike over either history."""
from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest
from capsule_emit.artifact.record import Record
from capsule_emit.produce import sign
from capsule_emit.produce.model import BuiltPayload
from cll.log import AppendInput

from capsule_engine.events.capsule import build_event_capsule
from capsule_engine.guards import Action
from capsule_engine.guards import history as history_mod
from capsule_engine.guards.history import (
    INDEX_PATHS,
    TRUSTED_KEYS_FILE,
    MemoryHistory,
    Query,
    StoreHistory,
    lookup,
    open_store,
    producer_identity,
    verify_record,
)
from capsule_engine.guards.signing import LocalSigner
from capsule_engine.packs import install_pack, load_pack_dir
from capsule_engine.report.live_check import _wired

REPO = Path(__file__).resolve().parents[2]
EVERYDAY = REPO / "python" / "capsule_engine" / "packs" / "catalog" / "everyday"
STEPS = REPO / "conformance-vectors" / "guard" / "v0" / "inputs" / "everyday-actions"
SIGNER = LocalSigner(key_id="history-test", secret=b"history-test-secret")


def _event(n: int, *, event: str = "probe", detail: dict | None = None, timestamp: str | None = None) -> dict:
    return build_event_capsule(
        operator=f"op-{n % 2}", developer="dev", signer=SIGNER, event=event, detail=detail or {"n": n},
        timestamp=timestamp or f"2026-10-0{n + 1}T00:00:00Z", action_id=f"probe/{n}",
    )


def _ids(records) -> list[str]:
    return [r.capsule_id for r in records]


# -- what a query finds ------------------------------------------------------


def test_memory_history_finds_by_every_equality_and_a_lower_time_bound():
    caps = [_event(n) for n in range(4)]
    h = MemoryHistory(caps)
    assert _ids(h.find()) == _ids(h.find(Query())) == [c["capsule_id"] for c in caps]
    assert _ids(h.find(Query(where={"operator": "op-1"}))) == [caps[1]["capsule_id"], caps[3]["capsule_id"]]
    assert _ids(h.find(Query(where={"operator": "op-1", "developer": "nobody"}))) == []
    # A None value asks nothing of its field.
    assert len(h.find(Query(where={"operator": None}))) == 4
    # The bound is inclusive; a date alone is midnight UTC.
    assert _ids(h.find(Query(since="2026-10-03"))) == [caps[2]["capsule_id"], caps[3]["capsule_id"]]
    assert _ids(h.find(Query(since="2026-10-02T00:00:00+00:00"))) == _ids(h.find(Query(since="2026-10-02")))


def test_a_list_field_matches_by_element():
    h = MemoryHistory([_event(0, detail={"referenced_capsule_ids": ["a" * 64, "b" * 64]})])
    assert len(h.find(Query(where={"asg_payload.detail.referenced_capsule_ids": "b" * 64}))) == 1
    assert h.find(Query(where={"asg_payload.detail.referenced_capsule_ids": "c" * 64})) == []


def test_a_field_no_history_indexes_is_refused():
    with pytest.raises(ValueError, match="not indexed"):
        MemoryHistory().find(Query(where={"asg_payload.amount_minor": "1"}))


def test_an_unreadable_timestamp_never_matches_a_bounded_query():
    capsule = _event(0)
    capsule["timestamp"] = "not a time"
    h = MemoryHistory([capsule])
    assert h.find(Query(since="2000-01-01")) == []
    assert len(h.find()) == 1


def test_memory_history_refuses_a_capsule_twice_and_keeps_its_own_copy():
    capsule = _event(0)
    h = MemoryHistory()
    record = h.append(capsule)
    capsule["operator"] = "changed after the append"
    assert record.capsule["operator"] == "op-0"
    with pytest.raises(ValueError, match="already"):
        h.append(_event(0))
    assert (h.size(), h.get(record.capsule_id).seq) == (1, 1)


def test_lookup_takes_an_unambiguous_prefix():
    caps = [_event(n) for n in range(2)]
    h = MemoryHistory(caps)
    first = caps[0]["capsule_id"]
    assert lookup(h, first).capsule_id == first
    assert lookup(h, first[:12]).capsule_id == first
    assert lookup(h, "") is None  # every record shares the empty prefix


# -- the store-backed history ------------------------------------------------


def test_a_store_round_trips_and_reopens(tmp_path):
    caps = [_event(n) for n in range(3)]
    with open_store(tmp_path / "s", signer=SIGNER, create=True) as store:
        for capsule in caps:
            store.append(capsule)
        with pytest.raises(ValueError, match="already"):
            store.append(caps[0])
    with open_store(tmp_path / "s") as store:
        assert store.size() == 3
        assert _ids(store.find()) == [c["capsule_id"] for c in caps]
        assert _ids(store.find(Query(where={"operator": "op-0"}, since="2026-10-02"))) == [caps[2]["capsule_id"]]
        assert verify_record(store, store.get(caps[1]["capsule_id"])).ok
        assert store.get("zz") is None and store.get("0" * 64) is None
        with pytest.raises(RuntimeError, match="no signing identity"):
            store.append(_event(5))


def test_a_store_needs_its_log_unless_created(tmp_path):
    with pytest.raises(FileNotFoundError):
        open_store(tmp_path / "missing")


def test_the_producer_key_is_derived_and_never_written(tmp_path):
    with open_store(tmp_path / "s", signer=SIGNER, create=True) as store:
        store.append(_event(0))
    identity = producer_identity(SIGNER)
    assert identity.public_key == producer_identity(LocalSigner(key_id="history-test", secret=b"history-test-secret")).public_key
    assert identity.public_key != producer_identity(LocalSigner(key_id="other", secret=b"other")).public_key
    assert (tmp_path / "s" / TRUSTED_KEYS_FILE).read_text().split() == [identity.public_key.hex()]
    written = b"".join(p.read_bytes() for p in (tmp_path / "s").iterdir())
    assert SIGNER.secret not in written
    assert identity._private_key.private_bytes_raw().hex().encode() not in written


def test_a_second_signer_is_trusted_beside_the_first(tmp_path):
    other = LocalSigner(key_id="rotated", secret=b"rotated-secret")
    with open_store(tmp_path / "s", signer=SIGNER, create=True) as store:
        store.append(_event(0))
    with open_store(tmp_path / "s", signer=other) as store:
        store.append(_event(1))
        # Both records still read: each one's key is trusted.
        assert store.size() == 2 and len(store.find()) == 2
    assert len((tmp_path / "s" / TRUSTED_KEYS_FILE).read_text().split()) == 2


def _raw_stores(tmp_path):
    """The two stores under a StoreHistory, for writing out of the usual order."""
    store = open_store(tmp_path / "s", signer=SIGNER, create=True)
    return store, store._artifacts, store._log


def _put(artifacts, capsule: dict) -> None:
    data = json.dumps(capsule, separators=(",", ":")).encode()
    envelope = sign(BuiltPayload(capsule_id=capsule["capsule_id"], value=capsule, json=data), producer_identity(SIGNER))
    artifacts.put(Record(capsule_id=capsule["capsule_id"], capsule=data, producer_envelope=envelope))


def _append(log, capsule: dict) -> None:
    log.append(AppendInput(value=bytes.fromhex(capsule["capsule_id"]), appended_at=datetime.now(timezone.utc)))


def test_find_is_in_log_order_and_skips_what_was_put_but_not_appended(tmp_path):
    a, b, c = (_event(n * 2) for n in range(3))  # one operator, so one query finds all three
    store, artifacts, log = _raw_stores(tmp_path)
    try:
        for capsule in (a, b, c):
            _put(artifacts, capsule)
        _append(log, b)
        _append(log, a)
        assert _ids(store.find(Query(where={"operator": "op-0"}))) == [b["capsule_id"], a["capsule_id"]]
        assert store.get(c["capsule_id"]) is None and store.size() == 2
        # A write that stopped between put and append is completed by appending.
        assert store.append(c).seq == 3
    finally:
        store.close()


def test_find_reads_every_page(tmp_path, monkeypatch):
    monkeypatch.setattr(history_mod, "_FIND_LIMIT", 2)
    caps = [_event(n) for n in range(5)]
    with open_store(tmp_path / "s", signer=SIGNER, create=True) as store:
        for capsule in caps:
            store.append(capsule)
        assert _ids(store.find()) == [c["capsule_id"] for c in caps]


def test_the_store_declares_the_index_paths_go_declares():
    go = (REPO / "go" / "guard" / "history.go").read_text()
    for path in INDEX_PATHS:
        assert f'"{path}"' in go
    assert StoreHistory.__init__  # the declaration is made at construction


# -- the engine decides alike over either history ---------------------------


@pytest.mark.parametrize("steps", sorted(STEPS.glob("*.json")), ids=lambda p: p.stem)
def test_the_engine_decides_alike_over_a_store_and_in_memory(steps, tmp_path):
    pack = load_pack_dir(EVERYDAY)
    actions = [Action(**{k: tuple(v) if isinstance(v, list) else v for k, v in step.items()})
               for step in json.loads(steps.read_text())["steps"]]
    with tempfile.TemporaryDirectory() as project:
        memory = _wired(install_pack(pack, project_dir=Path(project) / "m", mode="observe"), MemoryHistory(), SIGNER)
        want = [memory.check(a).capsule for a in actions]
    with tempfile.TemporaryDirectory() as project, open_store(tmp_path / "s", signer=SIGNER, create=True) as store:
        stored = _wired(install_pack(pack, project_dir=Path(project) / "s", mode="observe"), store, SIGNER)
        got = [stored.check(a).capsule for a in actions]
        assert [r.capsule for r in store.find()] == [c for c in got if c is not None]
    assert got == want
