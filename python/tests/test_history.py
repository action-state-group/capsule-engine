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


def test_a_put_is_made_durable_before_the_log_commits(tmp_path):
    seen = []
    store = open_store(tmp_path / "s", signer=SIGNER, create=True)
    try:
        store._sync = lambda: seen.append(store.size())
        store.append(_event(0))
        store.append(_event(1))
        # Each sync ran before its record's log append.
        assert seen == [0, 1] and store.size() == 2
    finally:
        store.close()


def test_a_store_whose_log_outruns_its_artifacts_is_refused(tmp_path):
    with open_store(tmp_path / "s", signer=SIGNER, create=True) as store:
        store.append(_event(0))
        store.append(_event(1))
    artifacts = tmp_path / "s" / history_mod.ARTIFACTS_FILE
    first = artifacts.read_bytes().splitlines(keepends=True)[0]
    artifacts.write_bytes(first)
    with pytest.raises(RuntimeError, match="1 record"):
        open_store(tmp_path / "s")
    artifacts.unlink()
    with pytest.raises(RuntimeError, match="2 record"):
        open_store(tmp_path / "s")
    # The refusal released the log: opening again is refused for the same
    # reason, not for a lock still held (ContentionError).
    with pytest.raises(RuntimeError, match="2 record"):
        open_store(tmp_path / "s")


def test_an_open_store_holds_the_log_until_closed(tmp_path):
    from cll.log import ContentionError

    with open_store(tmp_path / "s", signer=SIGNER, create=True):
        with pytest.raises(ContentionError):
            open_store(tmp_path / "s")
    open_store(tmp_path / "s").close()


def test_verify_reports_a_record_the_store_refuses_as_a_failure(tmp_path, capsys):
    from capsule_engine.cli.main import main

    capsule = _event(0)
    with open_store(tmp_path / "s", signer=SIGNER, create=True) as store:
        store.append(capsule)
    assert main(["verify", capsule["capsule_id"], "--store", str(tmp_path / "s"), "--json"]) == 0
    capsys.readouterr()
    # Its producer key is no longer trusted: the store refuses the record.
    (tmp_path / "s" / TRUSTED_KEYS_FILE).write_text(producer_identity(LocalSigner(key_id="x", secret=b"x")).public_key.hex() + "\n")
    assert main(["verify", capsule["capsule_id"], "--store", str(tmp_path / "s"), "--json"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False and out["findings"][0]["code"] == "stored_record_invalid"


def test_every_other_signature_is_over_a_pre_signature_digest():
    """``producer_identity`` derives its seed from the signer's signature of
    a fixed label. That is sound only while no other call has the signer sign
    a caller-chosen string, so every other ``.sign(`` in the package must sign
    a capsule's pre-signature digest."""
    import ast

    allowed = {"presig_digest", "json_digest(presig)"}
    found = []
    for path in sorted((REPO / "python" / "capsule_engine").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "sign":
                arg = ast.unparse(node.args[0]) if node.args else ""
                if path.name == "history.py" and arg == "label":
                    continue
                if arg not in allowed:
                    found.append(f"{path.relative_to(REPO)}: sign({arg})")
    assert found == []


def test_a_directory_with_files_but_no_log_is_never_made_a_store(tmp_path):
    legacy = tmp_path / "ledger"
    (legacy / "segments").mkdir(parents=True)
    (legacy / "index.sqlite3").write_bytes(b"")
    for create in (False, True):
        with pytest.raises(history_mod.NotAStoreError, match="earlier capsule-engine release"):
            open_store(legacy, signer=SIGNER, create=create)
    assert sorted(p.name for p in legacy.iterdir()) == ["index.sqlite3", "segments"]
    # An empty directory may be made one.
    (tmp_path / "empty").mkdir()
    open_store(tmp_path / "empty", signer=SIGNER, create=True).close()


def test_the_mcp_server_and_init_refuse_a_ledger_directory(tmp_path, monkeypatch, capsys):
    from capsule_engine.cli.main import main
    from capsule_engine.mcp.config import load_config, open_backend

    legacy = tmp_path / "ledger"
    (legacy / "segments").mkdir(parents=True)
    monkeypatch.setenv("CAPSULE_STORE", str(legacy))
    with pytest.raises(history_mod.NotAStoreError):
        open_backend(load_config())
    rc = main(["init", "--pack", "everyday", "--project-dir", str(tmp_path / "p"), "--store", str(legacy),
               "--secret", "s"])
    assert rc == 2 and "not a store directory" in capsys.readouterr().err


def test_a_tenant_upgrade_over_a_ledger_directory_is_a_provision_error(tmp_path):
    from capsule_engine.tenants import TenantProvisionError, tenant_layout, upgrade_tenant

    layout = tenant_layout(tmp_path, "acme")
    (layout.root / "ledger" / "segments").mkdir(parents=True)
    layout.metadata_path.write_text(json.dumps({"tenant_id": "acme", "manifest_digest": "old"}))
    with pytest.raises(TenantProvisionError) as raised:
        upgrade_tenant(tmp_path, "acme", key_id="k", secret=b"s")
    assert raised.value.reason == "not_a_store"


def test_a_store_refuses_a_capsule_without_a_well_formed_id(tmp_path):
    with open_store(tmp_path / "s", signer=SIGNER, create=True) as store:
        for capsule_id in (None, "ABC", "0" * 63):
            capsule = _event(0)
            if capsule_id is None:
                del capsule["capsule_id"]
            else:
                capsule["capsule_id"] = capsule_id
            with pytest.raises(ValueError, match="64 lowercase hex"):
                store.append(capsule)
        assert store.size() == 0
