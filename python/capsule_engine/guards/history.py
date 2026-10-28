# SPDX-License-Identifier: Apache-2.0
"""What the checks read of earlier records, and where the engine writes its own.

Each check asks a :class:`History` for the records that can bear on it, by
equality on indexed fields (:data:`INDEX_PATHS`) and optionally a lower time
bound, and runs its own rule over them: the history never decides "accepted",
"first", "last" or "count". No check reads the whole log.

Two implementations:

- :class:`StoreHistory` reads and writes a CLL entry log beside capsule-emit's
  artifact store. A record is written with artifact ``put``, then CLL
  ``append``; the append is the commit, so a record put but never appended is
  not in the history. Each record is stored with a producer envelope signed by
  an Ed25519 key derived from the operator's own signing secret and trusted by
  the store directory alone (:func:`producer_identity`, :func:`open_store`).
- :class:`MemoryHistory` holds records in memory: a check input's history, a
  replay's records, tests.

The Go guard (``go/guard/history.go``) has the same shape and declares the same
paths, so both open one store namespace alike.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from agent_action_capsule import Finding, VerificationResult, compute_capsule_id
from agent_action_capsule.verify import verify as verify_capsule

from .signing import Signer

__all__ = [
    "INDEX_PATHS",
    "lookup",
    "History",
    "HistoryRecord",
    "MemoryHistory",
    "NotAStoreError",
    "Query",
    "StoreHistory",
    "open_store",
    "producer_identity",
    "verify_record",
    "verify_stored",
]

#: The capsule paths a store indexes, which :attr:`Query.where` names. The
#: Go guard declares the same set (``go/guard/history.go`` ``IndexPaths``).
INDEX_PATHS: tuple[str, ...] = (
    "operator",
    "developer",
    "action_type",
    "asg_payload.target",
    "asg_payload.deal_id",
    "asg_payload.stated.deal_id",
    "asg_payload.item_ref",
    "chain.parent_capsule_id",
    "asg_payload.event",
    "asg_payload.detail.session_id",
    "asg_payload.detail.referenced_capsule_ids",
)

#: The artifact store namespace :func:`open_store` uses.
NAMESPACE = "capsule-engine"


@dataclass(frozen=True)
class Query:
    """Records whose indexed fields hold every value in ``where`` (a list
    field holds it when it has it as an element; a ``None`` value asks
    nothing of its field) and, with ``since``, whose
    timestamp is at or after it. ``since`` is an RFC 3339 time or a date (a
    date alone is midnight UTC; a time with no offset is UTC); a record whose
    timestamp cannot be read as a time does not match a bounded query."""

    where: Mapping[str, str | None] = field(default_factory=dict)
    since: str | None = None


@dataclass(frozen=True)
class HistoryRecord:
    """One record of a history: its position in the log, its capsule and the
    capsule's id."""

    seq: int
    capsule: dict
    capsule_id: str


@runtime_checkable
class History(Protocol):
    def find(self, query: Query | None = None) -> list[HistoryRecord]:
        """Every record matching ``query`` (every record without one), in log order."""
        ...

    def get(self, capsule_id: str) -> HistoryRecord | None:
        """The record with ``capsule_id``, or ``None``."""
        ...

    def size(self) -> int:
        """The number of records in the log."""
        ...

    def append(self, capsule: dict) -> HistoryRecord:
        """Write ``capsule`` as the log's next record."""
        ...


def verify_record(history: History, record: HistoryRecord | None) -> VerificationResult | None:
    """The reference verification of ``record``'s capsule in ``history``, or
    ``None`` without a record: ``agent_action_capsule.verify`` with the store
    check that its chain parent is in ``history``, plus the time-fenced
    key-revocation check over ``history``'s own ``key_rotation`` events
    (``guards/revocation.py``), as the ledger's ``verify`` checked them."""
    from .revocation import build_key_timeline, check_time_fenced_revocation

    if record is None:
        return None
    parent = (record.capsule.get("chain") or {}).get("parent_capsule_id")
    present = isinstance(parent, str) and history.get(parent) is not None
    result = verify_capsule(record.capsule, store=[parent] if present else [])
    revocation = check_time_fenced_revocation(record.capsule, build_key_timeline(history))
    if not revocation.ok:
        result.findings.append(Finding("key_revoked_at_timestamp", revocation.reason, severity="error"))
        result.ok = False
    return result


def lookup(history: History, capsule_id: str) -> HistoryRecord | None:
    """The record with ``capsule_id``, or the one whose id it is an
    unambiguous prefix of (a prefix reads every record, as a person typing
    one at a prompt does, never a check)."""
    record = history.get(capsule_id)
    if record is not None or len(capsule_id) >= 64:
        return record
    ids = getattr(history, "capsule_ids", None)
    # The prefix is resolved from the ids alone, so a record that cannot be
    # read never decides which one is meant.
    candidates = ids() if ids is not None else [r.capsule_id for r in history.find()]
    matches = [i for i in candidates if i.startswith(capsule_id)]
    return history.get(matches[0]) if len(matches) == 1 else None


def verify_stored(history: History, capsule_id: str) -> tuple[str, VerificationResult] | None:
    """The id of the record ``capsule_id`` names (or is an unambiguous prefix
    of, :func:`lookup`) and its :func:`verify_record`, or ``None`` when there
    is none.
    A record the artifact store refuses to read (a capsule or producer
    envelope that no longer verifies, an untrusted signer) is a failed
    verification, never an error."""
    from capsule_emit.artifact.record import ArtifactError

    ids = getattr(history, "capsule_ids", None)
    if ids is not None and len(capsule_id) < 64:
        matches = [i for i in ids() if i.startswith(capsule_id)]
        if len(matches) != 1:
            return None
        capsule_id = matches[0]
    try:
        record = lookup(history, capsule_id)
        if record is None:
            return None
        return record.capsule_id, verify_record(history, record)
    except ArtifactError as exc:
        # The record itself, or one its verification reads (its chain
        # parent, a key rotation), is refused.
        finding = Finding("stored_record_invalid", f"the artifact store refused a record: {exc}", severity="error")
        return capsule_id, VerificationResult(ok=False, capsule_id=capsule_id, findings=[finding])


# -- matching (the in-memory history) ----------------------------------------

_RFC3339 = re.compile(r"(\d{4}-\d\d-\d\d)[Tt ](\d\d:\d\d:\d\d)(?:\.(\d+))?([Zz]|[+-]\d\d:\d\d)?")


def parse_instant(value: object) -> datetime | None:
    """``value`` (an RFC 3339 time, any fraction length, read to the
    microsecond; no offset is UTC) as an aware instant, or ``None``. The
    rule ``cll.ledger``'s scan read times by, kept so a check input's history
    is read as before."""
    if not isinstance(value, str):
        return None
    match = _RFC3339.fullmatch(value.strip())
    if match is None:
        return None
    date, clock, fraction, offset = match.groups()
    zone = "+00:00" if offset in (None, "Z", "z") else offset
    try:
        return datetime.fromisoformat(f"{date}T{clock}.{(fraction or '')[:6].ljust(6, '0')}{zone}")
    except ValueError:
        return None


def _bound(value: str) -> datetime:
    """A query's ``since`` as an instant."""
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d\d-\d\d", value.strip()):
        try:
            return datetime.fromisoformat(value.strip()).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    parsed = parse_instant(value)
    if parsed is None:
        raise ValueError(f"Query.since is not an RFC 3339 time or date: {value!r}")
    return parsed


def _values(capsule: Mapping[str, Any], path: str) -> list[str]:
    """The index entries ``capsule`` has at ``path``: a string is one, a list
    gives each string element, anything else none (the artifact store's
    rule)."""
    node: Any = capsule
    for segment in path.split("."):
        if not isinstance(node, Mapping):
            return []
        node = node.get(segment)
    if isinstance(node, str):
        return [node]
    if isinstance(node, list):
        return [v for v in node if isinstance(v, str)]
    return []


def _where(where: Mapping[str, str | None]) -> dict[str, str]:
    """``where`` without its ``None`` values, refusing a field no history indexes."""
    unknown = sorted(set(where) - set(INDEX_PATHS))
    if unknown:
        raise ValueError(f"a history is not indexed by {unknown}")
    return {path: value for path, value in where.items() if value is not None}


class MemoryHistory:
    """A :class:`History` over capsules held in memory, in the order given.
    Each is kept as its JSON reads back, as a stored record is; one with no
    ``capsule_id`` is held under the id computed from it."""

    def __init__(self, capsules: Iterable[dict] = ()) -> None:
        self._records: list[HistoryRecord] = []
        self._lock = threading.Lock()
        for capsule in capsules:
            self.append(capsule)

    def find(self, query: Query | None = None) -> list[HistoryRecord]:
        query = query or Query()
        where = _where(query.where)
        since = _bound(query.since) if query.since is not None else None
        out = []
        for record in self._records:
            if any(want not in _values(record.capsule, path) for path, want in where.items()):
                continue
            if since is not None:
                at = parse_instant(record.capsule.get("timestamp"))
                if at is None or at < since:
                    continue
            out.append(record)
        return out

    def get(self, capsule_id: str) -> HistoryRecord | None:
        return next((r for r in self._records if r.capsule_id == capsule_id), None)

    def capsule_ids(self) -> list[str]:
        """Every record's id, in log order."""
        return [r.capsule_id for r in self._records]

    def size(self) -> int:
        return len(self._records)

    def close(self) -> None:
        """Nothing to release; here so a caller may close any history."""

    def __enter__(self) -> MemoryHistory:
        return self

    def __exit__(self, *_: object) -> None:
        pass

    def append(self, capsule: dict) -> HistoryRecord:
        stored = json.loads(json.dumps(capsule))
        capsule_id = stored.get("capsule_id")
        if "capsule_id" in stored and (not isinstance(capsule_id, str) or not capsule_id):
            raise ValueError(f"capsule_id {capsule_id!r} is not a non-empty string")
        capsule_id = capsule_id or compute_capsule_id(stored)
        with self._lock:
            if self.get(capsule_id) is not None:
                raise ValueError(f"{capsule_id} is already in this history")
            record = HistoryRecord(seq=len(self._records) + 1, capsule=stored, capsule_id=capsule_id)
            self._records.append(record)
        return record


# -- the store-backed history -----------------------------------------------

_FIND_LIMIT = 1000


class NotAStoreError(FileNotFoundError):
    """A path :func:`open_store` cannot open as a store directory."""


class StoreHistory:
    """A :class:`History` over a CLL entry log (``cll.log``) and capsule-emit's
    artifact store (``capsule_emit.artifact``), which must implement the index
    extension. It declares :data:`INDEX_PATHS` on the store's namespace,
    indexing the records already stored. ``identity`` (a
    ``capsule_emit.produce.SigningIdentity``) signs each written record's
    producer envelope; without one the history only reads. ``sync``, when
    given, makes a put durable; it runs before the log append, so the log
    never commits a record the artifact store could lose."""

    def __init__(self, artifacts: Any, log: Any, *, identity: Any = None, sync: Callable[[], None] | None = None) -> None:
        artifacts.init_index(INDEX_PATHS)
        self._artifacts = artifacts
        self._log = log
        self._identity = identity
        self._sync = sync
        self._lock = threading.Lock()

    def check_committed(self) -> None:
        """Refuse a store whose log commits a record the artifact store does
        not hold (a lost or truncated artifact file): its queries would miss
        that record and read the history as complete."""
        held: set[str] = set()
        after = None
        while True:
            page = self._artifacts.find(after=after, limit=_FIND_LIMIT)
            held.update(page.ids)
            if not page.next:
                break
            after = page.next
        missing, seq = 0, 0
        while True:
            entries = self._log.scan_entries(seq, _FIND_LIMIT)
            if not entries:
                break
            missing += sum(1 for e in entries if bytes(e.value).hex() not in held)
            seq = entries[-1].seq
        if missing:
            raise RuntimeError(f"the log commits {missing} record(s) the artifact store does not hold")

    def find(self, query: Query | None = None) -> list[HistoryRecord]:
        """Every page of the artifact store's ``find``, less the ids the log
        does not hold, in log order. The bound goes to the store in UTC,
        truncated to the microsecond, its own precision; a record whose
        timestamp the store does not read as a time (no offset, a space or a
        lower-case ``t`` or ``z``) is not found under a bound."""
        query = query or Query()
        where = _where(query.where)
        since = None
        if query.since is not None:
            since = _bound(query.since).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        found: list[HistoryRecord] = []
        after = None
        while True:
            page = self._artifacts.find(where=where, since=since, after=after, limit=_FIND_LIMIT)
            for capsule_id in page.ids:
                record = self.get(capsule_id)
                if record is not None:
                    found.append(record)
            if not page.next:
                break
            after = page.next
        return sorted(found, key=lambda r: r.seq)

    def get(self, capsule_id: str) -> HistoryRecord | None:
        from cll.log import NotFoundError

        try:
            value = bytes.fromhex(capsule_id)
        except (TypeError, ValueError):
            return None
        if len(value) != 32:
            return None
        try:
            entry = self._log.get_entry(value)
        except NotFoundError:
            return None
        stored = self._artifacts.get(capsule_id)
        return HistoryRecord(seq=entry.seq, capsule=json.loads(stored.capsule), capsule_id=capsule_id)

    def size(self) -> int:
        return self._log.entry_count()

    def capsule_ids(self) -> list[str]:
        """Every committed record's id, in log order, read from the log
        alone (no record is read or verified)."""
        out, seq = [], 0
        while True:
            entries = self._log.scan_entries(seq, _FIND_LIMIT)
            if not entries:
                return out
            out.extend(bytes(e.value).hex() for e in entries)
            seq = entries[-1].seq

    def close(self) -> None:
        """Release the log (a JSONL log holds a writer lock while open)."""
        self._log.close()

    def __enter__(self) -> StoreHistory:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def reindex(self) -> None:
        """Rebuild the artifact store's index from the stored records (the
        engine's view rebuild)."""
        self._artifacts.reindex(INDEX_PATHS)

    def append(self, capsule: dict) -> HistoryRecord:
        """Put the capsule with its producer envelope, then append its id to
        the log (the commit). A capsule the log already holds is refused, as
        :class:`MemoryHistory` refuses it; one put but not appended (a write
        that stopped between the two) is appended."""
        from capsule_emit.artifact.record import Record
        from capsule_emit.produce import sign
        from capsule_emit.produce.model import BuiltPayload
        from cll.log import AppendInput

        if self._identity is None:
            raise RuntimeError("this history has no signing identity, so it cannot write")
        capsule_id = capsule.get("capsule_id")
        if not isinstance(capsule_id, str) or not re.fullmatch(r"[0-9a-f]{64}", capsule_id):
            raise ValueError(f"capsule_id {capsule_id!r} is not 64 lowercase hex")
        data = json.dumps(capsule, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        envelope = sign(BuiltPayload(capsule_id=capsule_id, value=capsule, json=data), self._identity)
        with self._lock:
            if self.get(capsule_id) is not None:
                raise ValueError(f"{capsule_id} is already in this history")
            self._artifacts.put(Record(capsule_id=capsule_id, capsule=data, producer_envelope=envelope))
            if self._sync is not None:
                self._sync()
            appended = self._log.append(
                AppendInput(value=bytes.fromhex(capsule_id), appended_at=datetime.now(timezone.utc))
            )
        return HistoryRecord(seq=appended.entry.seq, capsule=json.loads(data), capsule_id=capsule_id)


# -- a store directory -----------------------------------------------------

LOG_FILE = "log.jsonl"
ARTIFACTS_FILE = "artifacts.jsonl"
TRUSTED_KEYS_FILE = "trusted_keys"

#: What a producer key is derived under (:func:`producer_identity`).
PRODUCER_KEY_LABEL = "capsule-engine/producer-key/v1"


def producer_identity(signer: Signer) -> Any:
    """The Ed25519 identity that signs the producer envelopes of ``signer``'s
    records: a seed derived from ``signer``'s signature over a fixed label,
    so it is the operator's own key, never written anywhere, and the same
    each time the same signing secret is given. ``signer`` must sign
    deterministically, as ``LocalSigner`` does.

    The derivation holds only while nothing else has ``signer`` sign the
    label's digest: every other ``sign`` call in this package signs a
    capsule's pre-signature digest, the JCS digest of a JSON object, which
    the digest of this plain label never is.
    ``tests/test_history.py`` pins that; a KDF over the secret itself would
    not need it."""
    from capsule_emit.produce import new_ed25519_signing_identity

    label = hashlib.sha256(PRODUCER_KEY_LABEL.encode("ascii")).hexdigest()
    seed = hashlib.sha256(PRODUCER_KEY_LABEL.encode("ascii") + b"\0" + signer.sign(label).encode("utf-8")).digest()
    return new_ed25519_signing_identity(seed)


def _trusted_keys(path: Path) -> list[bytes]:
    if not path.exists():
        return []
    return [bytes.fromhex(line) for line in path.read_text(encoding="utf-8").split() if line]


def open_store(
    directory: os.PathLike[str] | str, *, signer: Signer | None = None, create: bool = False
) -> StoreHistory:
    """The :class:`StoreHistory` of a store directory: the CLL entry log
    (``log.jsonl``), the artifact store (``artifacts.jsonl``) and the
    producer public keys the store trusts (``trusted_keys``, one hex key per
    line). With ``signer`` the history writes, signing with
    :func:`producer_identity`, whose public key is added to ``trusted_keys``
    when it is not listed; without one it only reads. ``create`` makes a
    missing directory and log; otherwise the log must exist."""
    from capsule_emit.artifact.jsonl import JsonlArtifactStore
    from cll.log import jsonl as cll_jsonl

    directory = Path(directory)
    log_path = directory / LOG_FILE
    if not log_path.is_file():
        if not create or (directory.is_dir() and any(directory.iterdir())):
            # A directory that holds files but no log is not a store: a
            # ledger directory from an earlier release (segments/,
            # index.sqlite3) is read with that release. Never start an empty
            # history inside it.
            raise NotAStoreError(
                f"{directory}: not a store directory (no {LOG_FILE}); a ledger directory written by an "
                "earlier capsule-engine release is read with that release"
            )
        directory.mkdir(parents=True, exist_ok=True)
    # The log's writer lock is taken first and held while the history is
    # open, so no other process writes between reading the artifact store
    # and using it.
    cll_jsonl.init(log_path)
    log = cll_jsonl.open(log_path)
    try:
        keys_path = directory / TRUSTED_KEYS_FILE
        trusted = _trusted_keys(keys_path)
        identity = producer_identity(signer) if signer is not None else None
        if identity is not None and identity.public_key not in trusted:
            trusted.append(identity.public_key)
            with keys_path.open("a", encoding="utf-8") as f:
                f.write(identity.public_key.hex() + "\n")
        artifacts_path = directory / ARTIFACTS_FILE
        artifacts = JsonlArtifactStore(artifacts_path, NAMESPACE, trusted)
        artifacts.init()
        history = StoreHistory(artifacts, log, identity=identity, sync=lambda: _fsync(artifacts_path))
        history.check_committed()
    except BaseException:
        log.close()
        raise
    return history


def _fsync(path: Path) -> None:
    """Flush ``path``'s written data to disk (the JSONL artifact store's put
    does not)."""
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
