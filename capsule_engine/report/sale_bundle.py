# SPDX-License-Identifier: Apache-2.0
"""A sale bundle, and the history a replay gives one check of the sale.

capsulectl's ``deal sale bundle`` writes the user's own copy of a sale. It is
the Evidence Bundle of the sale's own log: its ``sale`` root, its one
``task-authority/v0``, and one x-deal-v0 ``thread`` record per thread
registered on it (``body.thread_ref_commitment``, the thread's deal id
committed under the step's own nonce). Each registered thread's own copy is
carried in the private ``x-deal-sale/v0`` extension, ``{"threads": {<thread
deal id>: <evidence-bundle/v2>}}``. The ``x-deal-v0`` section lists
``sale_threads``, one entry per registration, ``{registration, nonce,
thread_id, member}``, where ``member`` is ``present``, ``missing`` or
``never_opened`` (AMENDMENT 10). The sale's log also seals one
``thread_opened`` per opened thread (its registration ref and a
``task_authority_commitment``), and the bundle step seals each thread's
report, then a ``sale_cut`` (a ``head_commitment`` per opened thread, by
registration), then the sale's checkpoint. A ``present`` entry carries
``opened: {nonce}`` and ``head: {nonce, record_digest}`` (AMENDMENT 11).

``read_sale_bundle`` verifies it. It decides whether the replay holds every
thread of the sale, and the sale's key: the digest of the sale's own task
authority, which each thread's sealed report opens as
``sale_authority_opening.text``. Each link below is checked. One that does
not hold is named in ``findings`` and leaves the sale incomplete; no thread
is skipped:

- the sale's bundle, and each carried thread's, verifies VALID as an
  Evidence Bundle (``capsule_emit.evidence_file``);
- each is the whole of its own log, ``deal/<its id>``, at its checkpoint:
  its completeness certificate runs from the first record to the last the
  checkpoint holds, and it carries that many records;
- every record of the sale's log is disclosed and bound, so no
  registration is hidden; the sale's bundle holds its ``sale`` record and
  exactly one ``task-authority/v0`` of the sale;
- ``sale_threads`` is there, and the sale does not say its threads predate
  registration;
- ``sale_threads`` has one entry per ``thread`` record, in seal order, each
  naming that record by digest;
- a ``present`` or ``never_opened`` entry's ``nonce`` and ``thread_id`` open
  that record's ``thread_ref_commitment``, and no two entries open one
  thread. A ``missing`` entry is a thread not shown;
- the threads ``x-deal-sale/v0`` carries are exactly the ``present`` ones;
- each carried thread is the registered one. Its checkpoint is signed with
  the sale's checkpoint key and its records with keys the sale's records
  name. Its one task authority of deal ``thread_id`` names that registration
  in its one ``registration`` ref. Its sealed report's
  ``sale_authority_opening`` opens that task authority's
  ``sale_authority_commitment`` to the sale's key and names that task
  authority by digest;
- the sale's log shows each carried thread opened and whole: exactly one
  ``thread_opened`` names its registration, and ``opened.nonce`` opens it
  to the digest of the thread's task authority; the latest ``sale_cut``
  (the bundle is its whole log at its checkpoint, so the latest at or
  before it) has a head for it, and ``head`` opens it to
  ``record_digest``, the ``agent_input_digest`` of the capsule at the
  thread's last ``seq``. A carried copy cut short is not whole;
- a ``never_opened`` entry is named by no ``thread_opened`` and no head of
  that cut. Every record of the sale's log is disclosed, so none is hidden.

The sale is certified through the earliest of the signed checkpoints: the
sale's and each carried thread's (``certified_until``). A registration, or a
thread's record, appended after a checkpoint is not in the bundle.

``sale_history`` gives one check of a carried thread the history a live
check of it is given (external-check-input/v0): each act with an amount of
every carried thread sealed before the check, oldest first, with the sale's
key beside it as ``item_ref`` and, from the check the act rests on, the
counterparty that check sealed and its ``counterparty_profile`` companion.
``history_scope`` says whether that history is complete, so
``history_ledger`` reads it as it reads a live one. It is complete only when
the sale is complete, and the check's whole second falls before
``certified_until``. Records are ordered by the whole second they name, and
two threads are two logs, so records of two threads are ordered only when
their seconds differ. The history is therefore not complete when another
thread's act is sealed in the check's own second, or two acts of different
threads in it share a second. A record a thread carries without disclosing
it, sealed before the check, is given as an entry with no ``agent_input``.
Whether it is an act cannot be read, so ``history_ledger`` writes it as
``unread``.

What this does not establish:

- The copy is read as its producer signed it. Every checkpoint is the
  producer's own, and no witness receipt is read, so a producer holding the
  profile's keys can sign a log that leaves an act out. Only witnessing the
  sale's and its threads' checkpoints shows a log was not rewritten.
- ``never_opened`` holds against the sale's log as its producer signed it:
  a producer that never sealed a thread's ``thread_opened`` is the case of
  the first point. The absence of ``threads_predate_registration`` is the
  producer's word, but such a copy has no ``thread_opened`` or ``sale_cut``
  for its present threads and is not complete.
- The history holds the sale's registered threads only. A live check is
  given every act of every deal on the profile from its last 31 days, and
  holds a sale's acceptance unknown when an accepted commitment of another
  deal names no item; a replay of the sale does not.
- A ``SaleBundle`` is what ``read_sale_bundle`` returned. One built or
  changed otherwise is its caller's word.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent_action_capsule import json_digest
from capsule_emit.evidence_file import check_evidence_file
from capsule_ledger.ledger.api import LedgerAPI

from .live_history import history_ledger
from .replay import (
    ReplayResult,
    SaleCheck,
    SaleHistory,
    _bound,
    _companion_profiles,
    _minor,
    _parse_ts,
    _text,
    _typed_ref_digest,
    replay,
)

__all__ = [
    "SALE_EXTENSION",
    "SaleBundle",
    "SaleThread",
    "load_sale_bundle",
    "read_sale_bundle",
    "replay_sale",
    "sale_checks",
    "sale_history",
]

SALE_EXTENSION = "x-deal-sale/v0"
_PROFILE = "x-deal-v0"
_PRESENT, _MISSING, _NEVER_OPENED = "present", "missing", "never_opened"
_TASK_AUTHORITY = "task-authority/v0"
_ACT_RECORD = "action-record/v0"
_EVALUATION = "action-evaluation/v0"
_APPROVAL = "action-approval/v0"
_PROPOSED = "proposed-action/v0"
_SEALED_REPORT = "sealed_report"
_DEAL_ID = re.compile(r"^deal-[0-9a-f]{16}$")
_SALE_ID = re.compile(r"^sale-[0-9a-f]{16}$")
_SECOND = timedelta(seconds=1)


@dataclass(frozen=True)
class SaleThread:
    """A thread the sale bundle carries: its deal id, its records in its
    bundle's order, and each record's disclosed ``agent_input`` by
    ``capsule_id``."""

    thread_id: str
    records: tuple[dict, ...]
    disclosed: dict[str, dict]


@dataclass(frozen=True)
class SaleBundle:
    """What ``read_sale_bundle`` established. ``key`` is the digest of the
    sale's own task authority, ``None`` when there is not exactly one.
    ``threads`` are the carried threads, in registration order.
    ``certified_until`` is the earliest signed checkpoint time.
    ``findings`` names each link that does not hold, never a value."""

    key: str | None
    threads: tuple[SaleThread, ...]
    certified_until: datetime | None
    findings: tuple[str, ...]

    @property
    def complete(self) -> bool:
        """Whether the replay holds every thread of the sale, each verified
        and bound to it."""
        return not self.findings

    def records(self) -> list[dict]:
        """Every carried thread's records in seal order: by time, and within
        one second by registration order, then each thread's own order. A
        record with no readable time keeps its thread's order, after those
        with one."""
        indexed = [
            (_second(r.get("timestamp")), t, i, r)
            for t, thread in enumerate(self.threads)
            for i, r in enumerate(thread.records)
        ]
        indexed.sort(key=lambda x: (x[0] is None, x[0] or datetime.min, x[1], x[2]))
        return [r for *_, r in indexed]

    def disclosed(self) -> dict[str, dict]:
        """Every carried thread's disclosed ``agent_input``, by
        ``capsule_id``."""
        return {cid: shown for thread in self.threads for cid, shown in thread.disclosed.items()}


# Reads the file the caller names: the module's decoding boundary.
def load_sale_bundle(path: str | Path) -> SaleBundle:
    """``read_sale_bundle`` of the JSON file at ``path``."""
    return read_sale_bundle(json.loads(Path(path).read_text(encoding="utf-8")))


def read_sale_bundle(bundle: dict) -> SaleBundle:
    """Verify ``bundle`` as a sale bundle (module docstring)."""
    findings: list[str] = []
    sale = check_evidence_file(bundle)
    if sale.verdict != "VALID":
        findings.append("sale_bundle_not_valid")
    disclosed = _disclosed(bundle)
    records = [r for r in bundle.get("records") or [] if isinstance(r, dict)]
    bound = [disclosed[r["capsule_id"]] for r in records
             if r.get("capsule_id") in disclosed and _bound(r, disclosed[r["capsule_id"]])]
    if len(bound) != len(records):
        # A record of the sale's log it does not disclose may be a registration.
        findings.append("sale_record_not_disclosed")
    authorities = [s for s in bound if s.get("type") == _TASK_AUTHORITY
                   and _SALE_ID.fullmatch(_text(s.get("chain_id")) or "")]
    is_sale = any(_record_type(s) == "sale" for s in bound)
    key = json_digest(authorities[0]) if len(authorities) == 1 else None
    if not is_sale or key is None:
        findings.append("not_a_sale_bundle")
    sale_id = _text(authorities[0].get("chain_id")) if len(authorities) == 1 else None
    if sale_id is None or not _covers_its_log(bundle, sale.checkpoint, sale_id):
        findings.append("sale_bundle_does_not_cover_its_log")
    signers = _record_keys(records)
    registrations = sorted((s for s in bound if _record_type(s) == "thread"), key=_seq)
    extensions = bundle.get("extensions") if isinstance(bundle.get("extensions"), dict) else {}
    section = extensions.get(_PROFILE) if isinstance(extensions.get(_PROFILE), dict) else {}
    carrier = extensions.get(SALE_EXTENSION) if isinstance(extensions.get(SALE_EXTENSION), dict) else {}
    carried = carrier.get("threads") if isinstance(carrier.get("threads"), dict) else {}
    if section.get("threads_predate_registration") is True:
        findings.append("threads_predate_registration")
    listed = section.get("sale_threads")
    if not isinstance(listed, list):
        findings.append("no_sale_threads")
        listed = []
    opened, cut = _sale_log_openings(bound, findings)
    present = _present(listed, registrations, opened, cut, findings)
    if set(carried) != set(present):
        findings.append("carried_threads_are_not_the_present_ones")
    threads: list[SaleThread] = []
    times = [_checkpoint_time(sale.checkpoint)]
    for thread_id, (registration, entry) in present.items():
        thread_bundle = carried.get(thread_id)
        if not isinstance(thread_bundle, dict):
            continue
        checked = check_evidence_file(thread_bundle)
        if checked.verdict != "VALID":
            findings.append("thread_not_valid")
        if not _covers_its_log(thread_bundle, checked.checkpoint, thread_id):
            findings.append("thread_does_not_cover_its_log")
        if (checked.checkpoint.get("key_id") != sale.checkpoint.get("key_id")
                or not _record_keys(thread_bundle.get("records") or []) <= signers):
            findings.append("thread_not_signed_as_the_sale")
        times.append(_checkpoint_time(checked.checkpoint))
        shown = _disclosed(thread_bundle)
        held = tuple(r for r in thread_bundle.get("records") or [] if isinstance(r, dict))
        why, authority = _thread_binding(held, shown, thread_bundle, thread_id, registration, key)
        if why is not None:
            findings.append(why)
        elif authority is not None:
            findings.extend(_opened_and_whole(entry, registration, authority, thread_bundle, opened, cut))
        threads.append(SaleThread(thread_id=thread_id, records=held, disclosed=shown))
    certified = None if any(t is None for t in times) else min(t for t in times if t is not None)
    if certified is None:
        findings.append("checkpoint_time_not_read")
    return SaleBundle(key=key, threads=tuple(threads), certified_until=certified, findings=tuple(dict.fromkeys(findings)))


def _covers_its_log(bundle: dict, checkpoint: dict, deal_id: str) -> bool:
    """Whether ``bundle`` is the whole log of ``deal_id`` at its verified
    ``checkpoint``: the checkpoint's log and its completeness certificate's
    are ``deal/<deal_id>``, the certificate runs from the first record to the
    last the checkpoint holds (its ``mmr_size`` read as a leaf count), and
    the bundle carries that many records."""
    log = f"deal/{deal_id}"
    certificate = bundle.get("completeness_certificate")
    leaves = _leaves(_minor(checkpoint.get("mmr_size")))
    records = bundle.get("records")
    return (isinstance(certificate, dict) and checkpoint.get("log_id") == log and certificate.get("log_id") == log
            and leaves is not None and certificate.get("first_seq") == 1 and certificate.get("last_seq") == leaves
            and isinstance(records, list) and len(records) == leaves)


def _leaves(mmr_size: int | None) -> int | None:
    """The number of leaves of a Merkle mountain range of ``mmr_size``
    nodes (``2n - popcount(n)`` nodes hold ``n`` leaves), or ``None`` when
    no leaf count gives that size."""
    if mmr_size is None or mmr_size < 1:
        return None
    for leaves in range(mmr_size // 2, mmr_size + 1):
        if 2 * leaves - bin(leaves).count("1") == mmr_size:
            return leaves
    return None


def _record_keys(records: list) -> frozenset[object]:
    """The ``key_id`` every record names."""
    return frozenset(r.get("key_id") for r in records if isinstance(r, dict))


def _present(listed: list, registrations: list[dict], opened: dict[str, list[str]], cut: dict[str, str] | None,
             findings: list[str]) -> dict[str, tuple[str, dict]]:
    """The ``present`` threads ``sale_threads`` lists, thread id to the
    digest of its registration and its entry, in registration order; each
    entry checked against the ``thread`` record in its place, and a
    ``never_opened`` one against ``opened`` and ``cut`` (module docstring).
    Each failure is appended to ``findings``."""
    if len(listed) != len(registrations):
        findings.append("sale_threads_do_not_match_the_registrations")
    present: dict[str, tuple[str, dict]] = {}
    for entry, registration in zip(listed, registrations, strict=False):
        entry = entry if isinstance(entry, dict) else {}
        digest = json_digest(registration)
        if _typed_ref_digest(entry.get("registration")) != digest:
            findings.append("sale_threads_do_not_match_the_registrations")
            continue
        member = entry.get("member")
        if member == _MISSING:
            findings.append("thread_not_shown")
            continue
        if member not in (_PRESENT, _NEVER_OPENED):
            findings.append("unknown_thread_state")
            continue
        nonce, thread_id = _text(entry.get("nonce")), _text(entry.get("thread_id"))
        body = registration.get("body") if isinstance(registration.get("body"), dict) else {}
        if (nonce is None or thread_id is None or not _DEAL_ID.fullmatch(thread_id) or thread_id in present
                or json_digest({"nonce": nonce, "text": thread_id}) != body.get("thread_ref_commitment")):
            findings.append("registration_opening_does_not_match")
            continue
        if member == _PRESENT:
            present[thread_id] = (digest, entry)
        elif digest in opened:
            findings.append("never_opened_but_opened")
        elif cut is not None and digest in cut:
            findings.append("never_opened_but_in_the_cut")
    return present


def _thread_binding(records: tuple[dict, ...], shown: dict[str, dict], thread_bundle: dict, thread_id: str,
                    registration: str, key: str | None) -> tuple[str | None, str | None]:
    """Why a carried thread is not the registered thread of this sale
    (module docstring), or ``None`` when it is, with the digest of its task
    authority when it is."""
    bound = [shown[r["capsule_id"]] for r in records if r.get("capsule_id") in shown and _bound(r, shown[r["capsule_id"]])]
    authorities = [s for s in bound if s.get("type") == _TASK_AUTHORITY and s.get("chain_id") == thread_id]
    if len(authorities) != 1:
        return "thread_has_no_task_authority_of_this_thread", None
    (authority,) = authorities
    refs = authority.get("refs") if isinstance(authority.get("refs"), list) else []
    named = [r for r in refs if isinstance(r, dict) and r.get("rel") == "registration"]
    if len(named) != 1 or _typed_ref_digest(named[0]) != registration:
        return "thread_does_not_name_its_registration", None
    opening = _sale_authority_opening(thread_bundle, records, shown)
    body = authority.get("body") if isinstance(authority.get("body"), dict) else {}
    nonce, text = _text(opening.get("nonce")), _text(opening.get("text"))
    digest = json_digest(authority)
    if (key is None or nonce is None or text != key or opening.get("record_digest") != digest
            or json_digest({"nonce": nonce, "text": text}) != body.get("sale_authority_commitment")):
        return "thread_is_not_under_this_sale", None
    return None, digest


def _sale_log_openings(bound: list[dict], findings: list[str]) -> tuple[dict[str, list[str]], dict[str, str] | None]:
    """What the sale's log says of its threads (AMENDMENT 11): each
    ``thread_opened`` record's ``task_authority_commitment``, by the digest
    of the registration it names, and the ``head_commitment`` of each
    thread the latest ``sale_cut`` names, by registration (``None`` when the
    log holds no cut). The bundle is the whole log at its certified
    checkpoint, so its latest cut is the latest one at or before it. A
    record of either kind that cannot be read is appended to ``findings``:
    it may be the one that opens a thread."""
    opened: dict[str, list[str]] = {}
    cuts: list[dict] = []
    for shown in bound:
        kind = _record_type(shown)
        body = shown.get("body") if isinstance(shown.get("body"), dict) else {}
        if kind == "thread_opened":
            refs = (shown.get(_PROFILE) or {}).get("refs")
            named = [r for r in refs if isinstance(r, dict)] if isinstance(refs, list) else []
            registration = _typed_ref_digest(named[0]) if len(named) == 1 and named[0].get("rel") == "registration" else None
            commitment = _text(body.get("task_authority_commitment"))
            if registration is None or commitment is None:
                findings.append("thread_opened_not_read")
                continue
            opened.setdefault(registration, []).append(commitment)
        elif kind == "sale_cut":
            cuts.append(shown)
    if not cuts:
        return opened, None
    latest = max(cuts, key=_seq)
    if sum(_seq(c) == _seq(latest) for c in cuts) != 1:
        findings.append("sale_cut_not_read")
        return opened, {}
    body = latest.get("body") if isinstance(latest.get("body"), dict) else {}
    heads = body.get("thread_heads")
    cut: dict[str, str] = {}
    for head in heads if isinstance(heads, list) else [None]:
        head = head if isinstance(head, dict) else {}
        registration, commitment = _typed_ref_digest(head.get("registration")), _text(head.get("head_commitment"))
        if registration is None or commitment is None or registration in cut:
            findings.append("sale_cut_not_read")
            return opened, {}
        cut[registration] = commitment
    return opened, cut


def _opened_and_whole(entry: dict, registration: str, authority: str, thread_bundle: dict,
                      opened: dict[str, list[str]], cut: dict[str, str] | None) -> list[str]:
    """Why the sale's log does not show a present thread opened and whole
    (AMENDMENT 11), or ``[]``: exactly one ``thread_opened`` names its
    registration, and the entry's ``opened.nonce`` opens it to the digest
    of the thread's task authority; the latest ``sale_cut`` has a head for
    it, and the entry's ``head`` opens it to the digest of the carried
    thread's last record."""
    why: list[str] = []
    commitments = opened.get(registration, [])
    opening = entry.get("opened") if isinstance(entry.get("opened"), dict) else {}
    if len(commitments) != 1:
        why.append("opened_not_evidenced")
    elif not _opens(opening.get("nonce"), authority, commitments[0]):
        why.append("opening_does_not_match_the_thread")
    if cut is None:
        why.append("no_sale_cut")
    elif registration not in cut:
        why.append("thread_not_in_the_cut")
    else:
        head = entry.get("head") if isinstance(entry.get("head"), dict) else {}
        digest = _text(head.get("record_digest"))
        last = _last_record_digest(thread_bundle)
        if digest is None or digest != last or not _opens(head.get("nonce"), digest, cut[registration]):
            why.append("thread_not_whole_at_the_cut")
    return why


def _opens(nonce: object, text: str, commitment: str) -> bool:
    """Whether ``nonce`` with ``text`` recomputes to ``commitment``."""
    return isinstance(nonce, str) and json_digest({"nonce": nonce, "text": text}) == commitment


def _last_record_digest(thread_bundle: dict) -> str | None:
    """The ``agent_input_digest`` the capsule at its completeness
    certificate's ``last_seq`` seals: the digest of the thread's last
    record, disclosed or not. ``None`` when no one capsule is there."""
    certificate = thread_bundle.get("completeness_certificate")
    certificate = certificate if isinstance(certificate, dict) else {}
    memberships = certificate.get("memberships") if isinstance(certificate.get("memberships"), dict) else {}
    last = certificate.get("last_seq")
    at = [cid for cid, m in memberships.items()
          if isinstance(m, dict) and isinstance(m.get("log_coordinates"), dict)
          and m["log_coordinates"].get("seq") == last]
    records = [r for r in thread_bundle.get("records") or [] if isinstance(r, dict) and r.get("capsule_id") in at]
    if len(at) != 1 or len(records) != 1:
        return None
    attestation = (records[0].get("model_attestation") or {}).get("compute_attestation") or {}
    return _text(attestation.get("agent_input_digest"))


def _sale_authority_opening(thread_bundle: dict, records: tuple[dict, ...], shown: dict[str, dict]) -> dict:
    """The ``sale_authority_opening`` of the report a thread's copy seals:
    the bound record its ``x-deal-v0`` extension names as ``sealed_report``.
    ``{}`` when there is none."""
    extensions = thread_bundle.get("extensions") if isinstance(thread_bundle.get("extensions"), dict) else {}
    section = extensions.get(_PROFILE) if isinstance(extensions.get(_PROFILE), dict) else {}
    pointer = section.get(_SEALED_REPORT)
    sealed = next((r for r in records if r.get("capsule_id") == pointer), None)
    report_record = shown.get(pointer) if isinstance(pointer, str) else None
    if sealed is None or report_record is None or not _bound(sealed, report_record):
        return {}
    report = report_record.get("report")
    opening = report.get("sale_authority_opening") if isinstance(report, dict) else None
    return opening if isinstance(opening, dict) else {}


def replay_sale(sale: SaleBundle, **options: object) -> ReplayResult:
    """``replay`` of every carried thread's records in seal order
    (``SaleBundle.records``), with their disclosures, the records carried
    without disclosing withheld, and each check's history of the sale
    (``sale_checks``). ``options`` are ``replay``'s other keyword
    arguments."""
    records = sale.records()
    disclosed = sale.disclosed()
    withheld = frozenset(r["capsule_id"] for r in records if isinstance(r.get("capsule_id"), str)) - disclosed.keys()
    return replay(records, disclosed=disclosed, withheld=withheld, sale_history=sale_checks(sale), **options)


def sale_checks(sale: SaleBundle) -> SaleHistory:
    """The ``sale_history`` a replay of ``sale`` is given: for a record of a
    carried thread, it writes the history ``sale_history`` builds for that
    check into the ledger through ``history_ledger``, the reader a live check
    uses, and gives the sale's key."""

    def write(capsule_id: str, ledger: LedgerAPI) -> SaleCheck | None:
        envelope = sale_history(sale, capsule_id)
        if envelope is None:
            return None
        history_ledger(envelope, ledger)
        return SaleCheck(item_ref=sale.key)

    return write


def sale_history(sale: SaleBundle, capsule_id: str) -> dict | None:
    """The external-check-input/v0 envelope of the check ``capsule_id`` in a
    carried thread, as a live check of it is given (module docstring): its
    ``record``, its ``history`` and ``history_scope.complete``. ``None`` when
    no carried thread holds that record."""
    found = next(((t, i) for t, thread in enumerate(sale.threads)
                  for i, r in enumerate(thread.records) if r.get("capsule_id") == capsule_id), None)
    if found is None:
        return None
    own, index = found
    checked = sale.threads[own].records[index]
    at = _second(checked.get("timestamp"))
    complete = (sale.complete and at is not None and sale.certified_until is not None
                and at + _SECOND <= sale.certified_until)
    before: list[tuple[datetime, int, int, dict]] = []
    for t, thread in enumerate(sale.threads):
        index_of = _ThreadIndex.of(thread)
        for i, record in enumerate(thread.records):
            entry = _history_entry(record, index_of, sale.key)
            if entry is None:
                continue
            sealed = _second(record.get("timestamp"))
            if sealed is None or at is None:
                complete = False
                continue
            if t != own and sealed == at:
                # Another thread's act in the check's second: before or after it is not sealed.
                complete = False
                continue
            if (t == own and i < index) or (t != own and sealed < at):
                before.append((sealed, t, i, entry))
    threads_by_second: dict[datetime, set[int]] = {}
    for sealed, t, _, _ in before:
        threads_by_second.setdefault(sealed, set()).add(t)
    if any(len(threads) > 1 for threads in threads_by_second.values()):
        # Two threads' acts in one second: which came first is not sealed.
        complete = False
    before.sort(key=lambda x: (x[0], x[1], x[2]))
    shown = sale.threads[own].disclosed.get(capsule_id)
    record = {**checked, "agent_input": shown} if shown is not None else dict(checked)
    return {"record": record, "history": [entry for *_, entry in before], "history_scope": {"complete": complete}}


@dataclass(frozen=True)
class _ThreadIndex:
    """A carried thread's disclosed records by digest, and each check's
    ``counterparty_profile`` block by the check's digest, read once."""

    thread: SaleThread
    by_digest: dict[str, dict]
    profiles: dict[str, object]

    @classmethod
    def of(cls, thread: SaleThread) -> _ThreadIndex:
        return cls(thread=thread, by_digest={json_digest(s): s for s in thread.disclosed.values()},
                   profiles=_companion_profiles(list(thread.records), thread.disclosed))


def _history_entry(record: dict, index: _ThreadIndex, key: str | None) -> dict | None:
    """The history entry capsulectl gives for ``record`` when it is an act
    with an amount: its capsule, its ``agent_input``, the sale's key as
    ``item_ref``, and, from the check it rests on, the counterparty that
    check sealed and the check's ``counterparty_profile`` companion. A
    record the thread carries without disclosing it is given with no
    ``agent_input``: it may be an act. ``None`` for any other record."""
    shown = index.thread.disclosed.get(record.get("capsule_id", ""))
    item = {"item_ref": key} if key is not None else {}
    if shown is None or not _bound(record, shown):
        return {**record, **item}
    body = shown.get("body") if isinstance(shown.get("body"), dict) else {}
    is_act = shown.get("type") == _ACT_RECORD or _record_type(shown) == "action"
    if not is_act or _minor(body.get("amount_minor")) is None:
        return None
    entry = {**record, "agent_input": shown, **item}
    check = _act_check(shown, index.by_digest)
    if check is None:
        return entry
    counterparty = check["body"].get("counterparty")
    if isinstance(counterparty, dict):
        entry["counterparty"] = counterparty
    profile = index.profiles.get(json_digest(check))
    if profile is not None:
        entry["counterparty_profile"] = profile
    return entry


def _act_check(act: dict, by_digest: dict[str, dict]) -> dict | None:
    """The check a typed act rests on, as capsulectl finds it for the act's
    counterparty: the act's one ``authorized_by`` ref names an evaluation,
    whose one ``checks`` ref names the check, or an approval, whose
    ``proposed_action_ref`` names it. ``None`` when a link is missing or for
    an act that is not typed."""
    verdict = by_digest.get(_one_ref(act, "authorized_by") or "")
    if verdict is None:
        return None
    if verdict.get("type") == _EVALUATION:
        check = by_digest.get(_one_ref(verdict, "checks") or "")
    elif verdict.get("type") == _APPROVAL:
        body = verdict.get("body") if isinstance(verdict.get("body"), dict) else {}
        check = by_digest.get(_typed_ref_digest(body.get("proposed_action_ref")) or "")
    else:
        return None
    if check is None or check.get("type") != _PROPOSED or not isinstance(check.get("body"), dict):
        return None
    return check


def _one_ref(shown: dict, rel: str) -> str | None:
    """The digest of a typed record's one ``rel`` ref, or ``None``."""
    refs = shown.get("refs") if isinstance(shown.get("refs"), list) else []
    found = [r for r in refs if isinstance(r, dict) and r.get("rel") == rel]
    return _typed_ref_digest(found[0]) if len(found) == 1 else None


def _disclosed(bundle: dict) -> dict[str, dict]:
    """A bundle's disclosed ``agent_input``, by ``capsule_id``."""
    members = bundle.get("disclosures") if isinstance(bundle.get("disclosures"), dict) else {}
    found: dict[str, dict] = {}
    for capsule_id, member in members.items():
        agent_input = member.get("agent_input") if isinstance(member, dict) else None
        if isinstance(agent_input, dict):
            found[capsule_id] = agent_input
    return found


def _record_type(shown: dict) -> str | None:
    block = shown.get(_PROFILE)
    return _text(block.get("record_type")) if isinstance(block, dict) else None


def _seq(shown: dict) -> int:
    seq = _minor((shown.get(_PROFILE) or {}).get("seq"))
    return seq if seq is not None else -1


def _second(value: object) -> datetime | None:
    """A capsule's ``timestamp`` in UTC, cut to its whole second, or
    ``None``: records are ordered by the second they name, whatever fraction
    or zone a producer writes."""
    at = _aware(value)
    return at.astimezone(timezone.utc).replace(microsecond=0) if at is not None else None


def _aware(value: object) -> datetime | None:
    """``value`` as an aware time, when it is an RFC 3339 string with a zone."""
    if not isinstance(value, str):
        return None
    try:
        at = _parse_ts(value)
    except ValueError:
        # Not a time: the caller reads the history as incomplete.
        return None
    return at if at.tzinfo is not None else None


def _checkpoint_time(checkpoint: dict) -> datetime | None:
    """The time a verified checkpoint's signed statement states, or ``None``."""
    return _aware(checkpoint.get("timestamp"))
