# SPDX-License-Identifier: Apache-2.0
"""A sale bundle whose thread reports are standalone (capsule-cli#299).

capsulectl seals a shelf deal's copy report off its log, in
``x-deal-report/v0``, bound to the copy's checkpoint. ``read_sale_bundle``
reads it only when it holds as capsulectl's ``verify --bundle`` holds it
(``standaloneReportEntry``); these tests take each of those checks in turn.

They run on ``fixtures/release-on-acceptance/`` (capsulectl after rc19). Its
keys are not vendored, so a case that changes a signed byte re-seals the
whole copy (``_relog``): every record of the sale and of its threads is
re-signed under one test key, every log's certificate and checkpoint is
rebuilt over the new records and signed under one test checkpoint key, and
each thread's report is bound to its new checkpoint and re-signed. A
Capsule's id does not cover its signature, so only a record that is moved
or edited gets a new id. The re-sealed copy reads complete, so each case
shows exactly the check it breaks.

The rc14 fixture (``fixtures/sale-replay/``) seals its reports in its
threads' logs and reads as it always did.
"""
from __future__ import annotations

import base64
import copy
import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from agent_action_capsule import json_digest
from capsule_emit import LocalKeypairSigner
from capsule_emit.canonicalization import compute_capsule_id
from capsule_emit.signing import sign_producer_envelope
from cll.checkpoint import (
    CheckpointRecord,
    MemoryNodeStore,
    add_leaf,
    checkpoint_to_cose,
    core,
    leaf_hash,
    peaks,
    root_from_peaks,
)

import capsule_engine
from capsule_engine.guards.checks import RUNNABLE_CHECKS
from capsule_engine.guards.history_state import RULES_HISTORY_DAYS
from capsule_engine.packs import install_pack, load_pack_dir
from capsule_engine.report.replay import replay
from capsule_engine.report.sale_bundle import SALE_EXTENSION, read_sale_bundle, sale_checks

HERE = Path(__file__).parent
PRODUCED = HERE / "fixtures" / "release-on-acceptance"
RC14 = HERE / "fixtures" / "sale-replay"
PACK = load_pack_dir(Path(capsule_engine.__file__).parent / "packs" / "catalog" / "seller")
REPORT = "x-deal-report/v0"
UNVERIFIED = {"sealed_report_unverified", "thread_is_not_under_this_sale"}


# Reads the vendored fixtures: the test's decoding boundary.
def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _produced() -> dict:
    return _load(PRODUCED / "sale.bundle.json")


def _check_record(name: str) -> dict:
    return _load(PRODUCED / "check-inputs" / f"{name}.json")["record"]


def _threads(bundle: dict) -> dict:
    return bundle["extensions"][SALE_EXTENSION]["threads"]


def _first(bundle: dict) -> dict:
    """The first thread the copy carries."""
    return next(iter(_threads(bundle).values()))


def _findings(bundle: dict) -> set[str]:
    return set(read_sale_bundle(bundle).findings)


@pytest.fixture(scope="module")
def keys(tmp_path_factory):
    """One test producer key and one test checkpoint key."""
    root = tmp_path_factory.mktemp("keys")
    return LocalKeypairSigner(root / "producer.pem"), LocalKeypairSigner(root / "checkpoint.pem")


# -- re-sealing a copy under test keys ---------------------------------------------


def _proof(p) -> dict:
    return {"v": p.v, "kind": p.kind, "size": p.size, "leaf_index": p.leaf_index, "witness": list(p.witness),
            "peaks_left": list(p.peaks_left), "peaks_right": list(p.peaks_right)}


def _renamed(value, renamed: dict[str, str]):
    """``value`` with every string that is a renamed record's old id replaced."""
    if isinstance(value, str):
        return renamed.get(value, value)
    if isinstance(value, list):
        return [_renamed(v, renamed) for v in value]
    if isinstance(value, dict):
        return {k: _renamed(v, renamed) for k, v in value.items()}
    return value


def _sign(record: dict, signer) -> dict:
    """``record`` with its id recomputed and signed under ``signer``."""
    record = {k: v for k, v in record.items() if k not in ("signature", "key_id")}
    record["capsule_id"] = compute_capsule_id(record)
    record["signature"], record["key_id"] = sign_producer_envelope(signer, record["capsule_id"])
    return record


def _reseal_log(bundle: dict, signer, log_signer, move=None, signer_of=None) -> None:
    """Every record of ``bundle``'s log re-signed under ``signer`` (``move``
    may give a record a new time first; ``signer_of``, given a record by its
    id before re-sealing, may name another key for it), and its certificate
    and checkpoint rebuilt over them, the checkpoint at its own time under
    ``log_signer``."""
    certificate = bundle["completeness_certificate"]
    log_id = certificate["log_id"]
    seq = {cid: m["log_coordinates"]["seq"] for cid, m in certificate["memberships"].items()}
    renamed: dict[str, str] = {}
    records = []
    for record in sorted(bundle["records"], key=lambda r: seq[r["capsule_id"]]):
        old = record["capsule_id"]
        record = _renamed(record, renamed)
        at = move(record) if move is not None else None
        if at is not None:
            record["timestamp"] = at
            if isinstance(record.get("evidence_record_header"), dict):
                record["evidence_record_header"]["committed_at"] = at
        record = _sign(record, (signer_of(old) if signer_of is not None else None) or signer)
        renamed[old] = record["capsule_id"]
        records.append(record)
    bundle["records"] = records
    bundle["root"] = renamed.get(bundle["root"], bundle["root"])
    bundle["disclosures"] = {renamed.get(k, k): v for k, v in bundle.get("disclosures", {}).items()}
    store = MemoryNodeStore()
    for record in records:
        add_leaf(store, leaf_hash(bytes.fromhex(record["capsule_id"])))
    size = store.size()
    peak_hashes = [store.node(p) for p in peaks(size)]
    root = root_from_peaks(peak_hashes).hex()
    timestamp = bundle["checkpoint"]["timestamp"]
    cp = CheckpointRecord(v=1, kind="mmr_checkpoint", log_id=log_id, mmr_size=size, root=root, prev_size=0,
                          prev_root="", key_id=log_signer.key_id, timestamp=timestamp, signature="")
    raw = checkpoint_to_cose(cp, log_signer, peak_hashes)
    bundle["checkpoint"] = {
        "cose": base64.urlsafe_b64encode(raw).rstrip(b"=").decode(), "key_id": log_signer.key_id,
        "kind": "mmr_checkpoint", "log_id": log_id, "mmr_size": size, "prev_root": "", "prev_size": 0,
        "root": root, "statement": base64.b64encode(raw).decode(), "timestamp": timestamp, "v": 1,
    }
    ids = [r["capsule_id"] for r in records]
    rp = core.range_proof(store, 0, len(ids) - 1, size)
    bundle["completeness_certificate"] = {
        "log_id": log_id, "range_root": root, "first_seq": 1, "last_seq": len(ids), "body_digests": ids,
        "range_proof": {"from_seq": 1, "to_seq": len(ids), "size": rp.size, "from_index": rp.from_index,
                        "to_index": rp.to_index, "witness": list(rp.witness)},
        "memberships": {cid: {"log_coordinates": {"leaf_index": i, "log_id": log_id, "seq": i + 1},
                              "inclusion_proof": _proof(core.inclusion_proof(store, i, size))}
                        for i, cid in enumerate(ids)},
    }


def _reseal_report(thread: dict, signer, edit=None) -> None:
    """The thread's standalone report bound to its checkpoint, ``edit``
    applied to its payload, and re-signed under ``signer``; the pointer
    follows its new id."""
    carried = thread["extensions"][REPORT]
    payload = carried["agent_input"]
    checkpoint = thread["checkpoint"]
    payload["deal_checkpoint"] = {k: checkpoint[k] for k in ("log_id", "mmr_size", "root")}
    if edit is not None:
        edit(payload)
    record = carried["record"]
    record["model_attestation"]["compute_attestation"]["agent_input_digest"] = json_digest(payload)
    carried["record"] = _sign(record, signer)
    thread["extensions"]["x-deal-v0"]["sealed_report"] = carried["record"]["capsule_id"]


def _relog(bundle: dict, keys, *, move=None, edit=None, report_signer=None, signer_of=None) -> dict:
    """``bundle`` re-sealed under the test ``keys`` (module docstring).
    ``move`` gives a thread record a new time; ``edit`` changes the first
    thread's report payload before it is sealed; ``report_signer`` signs
    that report instead of the producer key; ``signer_of`` may name another
    key for a record of any log, by its id before re-sealing."""
    signer, log_signer = keys
    out = copy.deepcopy(bundle)
    _reseal_log(out, signer, log_signer, signer_of=signer_of)
    for n, thread in enumerate(_threads(out).values()):
        _reseal_log(thread, signer, log_signer, move=move, signer_of=signer_of)
        first = n == 0
        _reseal_report(thread, (report_signer or signer) if first else signer, edit if first else None)
    return out


# -- what capsulectl wrote, and the in-log copies before it ------------------------


def test_capsulectls_copy_is_read_through_its_standalone_reports():
    bundle = _produced()
    assert all(REPORT in t["extensions"] and set(t["extensions"]["x-deal-v0"]) == {"sealed_report"}
               for t in _threads(bundle).values())
    assert not any(r.get("action_id") == "capsulectl-deal-report" for t in _threads(bundle).values()
                   for r in t["records"])
    assert read_sale_bundle(bundle).findings == ()


def test_an_rc14_copy_seals_its_reports_in_its_logs_and_reads_as_before():
    bundle = _load(RC14 / "sale.bundle.json")
    for thread in _threads(bundle).values():
        assert REPORT not in thread["extensions"]
        pointer = thread["extensions"]["x-deal-v0"]["sealed_report"]
        assert any(r["capsule_id"] == pointer for r in thread["records"])
    assert read_sale_bundle(bundle).findings == ()


def test_the_resealed_copy_reads_complete(keys):
    """The control every re-sealed case starts from."""
    assert _findings(_relog(_produced(), keys)) == set()


# -- each check of standaloneReportEntry -------------------------------------------


def _edit_report_text(bundle: dict) -> None:
    _first(bundle)["extensions"][REPORT]["agent_input"]["report"]["did_line"] = "edited"


def _edit_and_rebind(bundle: dict) -> None:
    carried = _first(bundle)["extensions"][REPORT]
    carried["agent_input"]["report"]["did_line"] = "edited"
    carried["record"]["model_attestation"]["compute_attestation"]["agent_input_digest"] = json_digest(
        carried["agent_input"])


def _set(path: tuple, value):
    def edit(bundle: dict) -> None:
        target = _first(bundle)["extensions"]
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
    return edit


UNSEALED = {
    "the payload edited": _edit_report_text,
    "the payload edited and re-bound, so its signature does not cover it": _edit_and_rebind,
    "another action_id": _set((REPORT, "record", "action_id"), "capsulectl-deal-step"),
    "a pointer that is not the record's id": _set(("x-deal-v0", "sealed_report"), "0" * 64),
    "another member in the deal section": _set(("x-deal-v0", "audience"), "keep"),
    "another member in the report section": _set((REPORT, "note"), "x"),
    "no deal section": lambda b: _first(b)["extensions"].pop("x-deal-v0"),
    "a report section that is not an object": _set((REPORT,), []),
}


@pytest.mark.parametrize("edit", UNSEALED.values(), ids=UNSEALED.keys())
def test_a_report_edited_in_the_copy_opens_nothing(edit):
    bundle = _produced()
    edit(bundle)
    assert UNVERIFIED <= _findings(bundle)


def _report(payload: dict) -> dict:
    return payload["report"]


def _as_of(payload: dict, old: str, new: str) -> None:
    lifecycle = _report(payload)["lifecycle"]
    lifecycle["as_of"] = lifecycle["as_of"].replace(old, new)


RESEALED = {
    "a checkpoint of another root": lambda p: p["deal_checkpoint"].update(root="0" * 64),
    "a checkpoint of another size": lambda p: p["deal_checkpoint"].update(mmr_size=p["deal_checkpoint"]["mmr_size"] - 1),
    "a checkpoint of another log": lambda p: p["deal_checkpoint"].update(log_id="deal/deal-0000000000000000"),
    "a checkpoint with another member": lambda p: p["deal_checkpoint"].update(timestamp="x"),
    "an earlier as_of": lambda p: _report(p)["lifecycle"].update(as_of="2026-10-22T06:52:00Z"),
    "an as_of a second later": lambda p: _report(p)["lifecycle"].update(
        as_of=(datetime.fromisoformat(_report(p)["lifecycle"]["as_of"].replace("Z", "+00:00"))
               + timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ")),
    "an as_of that is not RFC 3339": lambda p: _report(p)["lifecycle"].update(
        as_of=_report(p)["lifecycle"]["as_of"].replace("T", " ")),
    "another type": lambda p: p.update(type="deal_step"),
    "another profile": lambda p: p.update(profile="x-deal-v1"),
    "another deal": lambda p: p.update(deal_id="deal-0000000000000000"),
    "another audience": lambda p: p.update(audience="public"),
    "an audience that is a list": lambda p: p.update(audience=[]),
    "an audience that is an object": lambda p: p.update(audience={}),
    "a type that is a list": lambda p: p.update(type=[]),
    "a profile that is an object": lambda p: p.update(profile={}),
    "a deal that is a list": lambda p: p.update(deal_id=[]),
    "a checkpoint size that is a list": lambda p: p["deal_checkpoint"].update(mmr_size=[]),
    "a checkpoint root that is an object": lambda p: p["deal_checkpoint"].update(root={}),
    "a deal_checkpoint that is a list": lambda p: p.update(deal_checkpoint=[]),
    "an as_of that is a list": lambda p: _report(p)["lifecycle"].update(as_of=[]),
    # A datetime keeps microseconds only; Go compares the nanosecond.
    "an as_of a nanosecond later": lambda p: _report(p)["lifecycle"].update(
        as_of=_report(p)["lifecycle"]["as_of"].replace("Z", ".000000001Z")),
    # Go reads ASCII digits only: each of these is the checkpoint's instant
    # to a reader of Unicode digits.
    "an as_of with a non-ASCII digit in its fraction": lambda p: _as_of(p, "Z", ".\u0660Z"),
    "an as_of with a non-ASCII digit in its seconds": lambda p: _report(p)["lifecycle"].update(
        as_of=_report(p)["lifecycle"]["as_of"][:-2] + chr(0x0660 + int(_report(p)["lifecycle"]["as_of"][-2])) + "Z"),
    "an as_of with non-ASCII digits in its offset": lambda p: _as_of(p, "Z", "+\u0660\u0660:\u0660\u0660"),
    # The checkpoint's instant written at an offset of 99 minutes.
    "an as_of whose offset minute is past 59": lambda p: _report(p)["lifecycle"].update(
        as_of=_stamp(_at(_report(p)["lifecycle"]["as_of"]) + timedelta(minutes=99)).replace("Z", "+00:99")),
    "an as_of whose offset hour is past 23": lambda p: _as_of(p, "Z", "+24:00"),
    "an as_of before year 1 in UTC": lambda p: _report(p)["lifecycle"].update(as_of="0001-01-01T00:00:00+01:00"),
    "an as_of after year 9999 in UTC": lambda p: _report(p)["lifecycle"].update(as_of="9999-12-31T23:59:59-01:00"),
}


@pytest.mark.parametrize("edit", RESEALED.values(), ids=RESEALED.keys())
def test_a_signed_report_that_does_not_hold_opens_nothing(keys, edit):
    """Each one signed under the key of the deal's last step, so the check
    it breaks is the only one."""
    assert _findings(_relog(_produced(), keys, edit=edit)) == UNVERIFIED


@pytest.mark.parametrize("as_of", [".0Z", ".000000000Z", ".0000000001Z", "+00:00"])
def test_an_as_of_at_the_checkpoints_very_instant_holds(keys, as_of):
    """The same instant written otherwise; a tenth fractional digit is
    dropped, as Go's parser drops it."""
    edit = lambda p: _report(p)["lifecycle"].update(as_of=_report(p)["lifecycle"]["as_of"].replace("Z", as_of))  # noqa: E731
    assert _findings(_relog(_produced(), keys, edit=edit)) == set()


UNSEALED_TYPES = {
    "a pointer that is a list": _set(("x-deal-v0", "sealed_report"), []),
    "an action_id that is an object": _set((REPORT, "record", "action_id"), {}),
    "a record that is a list": _set((REPORT, "record"), []),
    "an agent_input that is a string": _set((REPORT, "agent_input"), "x"),
}


@pytest.mark.parametrize("edit", UNSEALED_TYPES.values(), ids=UNSEALED_TYPES.keys())
def test_a_member_of_the_wrong_type_opens_nothing(edit):
    bundle = _produced()
    edit(bundle)
    assert UNVERIFIED <= _findings(bundle)


def test_a_step_whose_record_type_is_not_a_string_is_not_a_step(keys):
    """The last step's header type made a list: it is no step, so the step
    before it is the last (as capsulectl's lastStepRecord reads it), signed
    by the same key, and the copy reads complete."""
    bundle = _produced()
    thread = _first(bundle)
    seq = {cid: m["log_coordinates"]["seq"] for cid, m in thread["completeness_certificate"]["memberships"].items()}
    last = max(thread["records"], key=lambda r: seq[r["capsule_id"]])
    last["evidence_record_header"]["record_type"] = []
    assert _findings(_relog(bundle, keys)) == set()


def test_a_report_signed_by_another_key_than_the_last_steps_opens_nothing(keys, tmp_path):
    other = LocalKeypairSigner(tmp_path / "other.pem")
    assert _findings(_relog(_produced(), keys, report_signer=other)) == UNVERIFIED


def _by_seq(thread: dict) -> list[dict]:
    seq = {cid: m["log_coordinates"]["seq"] for cid, m in thread["completeness_certificate"]["memberships"].items()}
    return sorted(thread["records"], key=lambda r: seq[r["capsule_id"]])


@pytest.mark.parametrize(("report_by_second_key", "findings"), [
    (False, UNVERIFIED),  # signed by an earlier step's key
    (True, set()),  # signed by the last step's key
])
def test_the_report_key_is_the_last_steps(keys, tmp_path, report_by_second_key, findings):
    """Only the first thread's last step is signed by a second key (one of
    the sale's records too, so the sale names it): the report must be
    signed by that key, not by the key of the steps before it."""
    second = LocalKeypairSigner(tmp_path / "second.pem")
    bundle = _produced()
    named = {_by_seq(_first(bundle))[-1]["capsule_id"], bundle["records"][-1]["capsule_id"]}
    resealed = _relog(bundle, keys, signer_of=lambda cid: second if cid in named else None,
                      report_signer=second if report_by_second_key else None)
    assert _findings(resealed) == findings


@pytest.mark.parametrize(("report_by_second_key", "findings"), [
    (True, UNVERIFIED),  # signed by the key of a record that is no step
    (False, set()),  # signed by the last step's key
])
def test_a_record_after_the_last_step_that_is_no_step_does_not_name_the_key(
        keys, tmp_path, report_by_second_key, findings):
    """The first thread's last record made a non-step (another header type)
    and signed by a second key the sale names: the last step is the one
    before it, and only its key signs the report."""
    second = LocalKeypairSigner(tmp_path / "second.pem")
    bundle = _produced()
    last = _by_seq(_first(bundle))[-1]
    last["evidence_record_header"]["record_type"] = "deal_note"
    named = {last["capsule_id"], bundle["records"][-1]["capsule_id"]}
    resealed = _relog(bundle, keys, signer_of=lambda cid: second if cid in named else None,
                      report_signer=second if report_by_second_key else None)
    assert _findings(resealed) == findings


def test_a_report_signed_over_its_content_but_carrying_another_id_opens_nothing(keys):
    """Its signature checks over the id its content gives, but it carries
    another id, and the pointer names that one: the Capsule does not verify
    (``verify_disclosure_envelope``), so it opens nothing."""
    resealed = _relog(_produced(), keys)
    thread = _first(resealed)
    record = thread["extensions"][REPORT]["record"]
    assert compute_capsule_id(record) == record["capsule_id"]
    record["capsule_id"] = "ab" * 32
    thread["extensions"]["x-deal-v0"]["sealed_report"] = record["capsule_id"]
    assert _findings(resealed) == UNVERIFIED


def test_a_copy_that_also_seals_a_report_in_its_log_opens_nothing(keys):
    """Its first step relabelled a deal report, in a log re-sealed over it."""
    bundle = _produced()
    _first(bundle)["records"][0]["action_id"] = "capsulectl-deal-report"
    assert _findings(_relog(bundle, keys)) == UNVERIFIED


def test_a_signed_report_is_still_held_to_the_commitment_in_its_log(keys):
    """A report that holds, opening to something its thread's task authority
    does not commit to, opens nothing: the report's checks pass."""
    findings = _findings(_relog(_produced(), keys, edit=lambda p: _report(p)["sale_authority_opening"].update(
        nonce="0" * 64)))
    assert findings == {"thread_is_not_under_this_sale"}


# -- the 31-day window through sale_checks, on a moved copy ------------------------


def _at(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _stamp(at: datetime) -> str:
    return at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _replayed(sale, watch: str):
    """``replay_sale`` of ``sale``, its ``sale_checks`` watched: the decision
    of ``watch`` and, by window, the action classes written for it."""
    given = sale_checks(sale)
    seen: dict = {}

    def write(capsule_id, ledger, days=None):
        result = given(capsule_id, ledger, days)
        if capsule_id == watch:
            seen[days] = [r.capsule["asg_payload"].get("action_class") for r in ledger.find()]
        return result

    records = sale.records()
    disclosed = sale.disclosed()
    withheld = frozenset(r["capsule_id"] for r in records) - disclosed.keys()
    with tempfile.TemporaryDirectory() as tmp:
        installed = install_pack(PACK, project_dir=Path(tmp) / "project", mode="observe")
        resolved = installed.resolved
        result = replay(records, disclosed=disclosed, withheld=withheld, sale_history=write,
                        caps_fold=resolved.caps_fold(), caps_minor=resolved.caps_minor(),
                        manifest_digest=resolved.manifest_digest,
                        wickets=resolved.configured_wickets(RUNNABLE_CHECKS), pack=installed.pack)
    (decision,) = [d.decision for d in result.decisions if d.record["capsule_id"] == watch]
    return decision, seen


@pytest.mark.parametrize(("offset", "s13"), [
    (timedelta(seconds=1), "pass"),  # just inside the window
    (timedelta(0), "pass"),  # exactly 31 days before the share: in
    (timedelta(seconds=-1), "fail"),  # just outside
])
def test_s13_reads_the_acceptance_only_in_its_window_and_s11_always(keys, offset, s13):
    """The copy capsulectl wrote, every thread record sealed before A's phone
    share moved back so A's commit act lands ``offset`` after 31 days before
    the share, and the copy re-sealed: it reads complete, s13 is given the
    acceptance only inside its 31-day window, and the whole history a check
    like s11 is given keeps it."""
    share = _check_record("a-share-phone")
    at = _at(share["timestamp"])
    bundle = _produced()
    (commit,) = [r for t in _threads(bundle).values() for r in t["records"]
                 if t["disclosures"].get(r["capsule_id"], {}).get("agent_input", {}).get("body", {}).get(
                     "action_class") == "agreement.accept" and r["capsule_id"] in t["disclosures"]
                 and t["disclosures"][r["capsule_id"]]["agent_input"].get("type") == "action-record/v0"]
    by = _at(commit["timestamp"]) - (at - timedelta(days=RULES_HISTORY_DAYS) + offset)
    assert by > timedelta(days=30)

    def move(record: dict) -> str | None:
        sealed = _at(record["timestamp"])
        return _stamp(sealed - by) if sealed < at else None

    moved = _relog(bundle, keys, move=move)
    sale = read_sale_bundle(moved)
    assert sale.findings == ()
    (kept,) = [r for t in sale.threads for r in t.records if r["timestamp"] == share["timestamp"]
               and r["action_id"] == share["action_id"]]
    decision, seen = _replayed(sale, kept["capsule_id"])
    (found,) = [c for c in decision.constraints if c.id == "release_on_acceptance"]
    assert found.result == s13, found.reason
    assert "agreement.accept" in seen[None]
    assert ("agreement.accept" in seen[RULES_HISTORY_DAYS]) is (s13 == "pass")
