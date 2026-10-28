# SPDX-License-Identifier: Apache-2.0
"""Dry-run report: real replay data shape, the JSON payload, and the
``guard dry-run`` file it writes (capsule bodies only with ``--share``)."""
from __future__ import annotations

import json
from pathlib import Path

from agent_action_capsule import compute_capsule_id

from capsule_engine.cli.main import main as cli_main
from capsule_engine.report import build_dry_run_report, to_payload

FIXTURES = Path(__file__).parent / "fixtures"
NANDA = FIXTURES / "nanda_transaction_ledger.jsonl"
AMAURY = FIXTURES / "amaury_sample_ledger.jsonl"
SAMPLE = FIXTURES / "sample_ledger.jsonl"

CAPS_MINOR = {"money.transfer": 10_000_000}


def test_dedupe_would_have_held_from_real_nanda_replay(caps_fold):
    report = build_dry_run_report([str(NANDA)], caps_fold=caps_fold, since="7d")

    assert report.actions_replayed == 36
    by_id = {s.guard_id: s for s in report.guards}
    assert len(by_id["dedupe"].rows) == 35
    assert len(by_id["caps"].rows) == 0
    assert len(by_id["verify_before_dispatch"].rows) == 0

    row = by_id["dedupe"].rows[0]
    assert row.agent == "biz_capsule-0"
    assert "already recorded" in row.why
    assert row.cited_capsule is not None
    assert row.cited_capsule["capsule_id"] == row.cited_capsule_id


def test_caps_would_have_held_from_real_amaury_replay(caps_fold):
    report = build_dry_run_report([str(AMAURY)], caps_fold=caps_fold, since="7d", caps_minor=CAPS_MINOR)

    by_id = {s.guard_id: s for s in report.guards}
    assert len(by_id["dedupe"].rows) == 0
    assert len(by_id["caps"].rows) == 1
    assert len(by_id["verify_before_dispatch"].rows) == 0

    row = by_id["caps"].rows[0]
    assert row.amount_minor == 15_000_000
    assert row.currency == "EUR"
    assert "150,000.00" in row.why
    assert row.capsule["disposition"]["verdict_class"] == "hitl_dispatched"

    guard_id, consequential_row = report.consequential()
    assert guard_id == "caps"
    assert consequential_row is row


def test_caps_never_triggers_when_unconfigured(caps_fold):
    """Matches ``GuardEngine``'s own default: no configured cap for a class
    means the caps check is always n/a for it, never a fabricated hit."""
    report = build_dry_run_report([str(AMAURY)], caps_fold=caps_fold, since="7d", caps_minor=None)
    by_id = {s.guard_id: s for s in report.guards}
    assert len(by_id["caps"].rows) == 0


def test_since_all_replays_every_record_across_ledgers(caps_fold):
    report = build_dry_run_report(
        [str(NANDA), str(AMAURY)], caps_fold=caps_fold, since=None, caps_minor=CAPS_MINOR
    )
    assert report.actions_replayed == 40
    assert report.record_range == (1, 40)


def test_since_window_drops_older_ledger_when_anchored_to_latest(caps_fold):
    """``since`` anchors to the replayed set's own latest timestamp (never
    the wall clock) -- combining ledgers ~2 weeks apart under a 7d window
    drops the older one entirely, which is real, correct behavior, not a bug."""
    report = build_dry_run_report(
        [str(NANDA), str(AMAURY)], caps_fold=caps_fold, since="7d", caps_minor=CAPS_MINOR
    )
    assert report.actions_replayed == 4  # only amaury's records survive the window
    assert report.operator == "acme-research"


def test_dry_run_never_blocks_real_decisions(caps_fold):
    """Every decision produced by the replay is a real, signed capsule --
    dry_run mode never withholds one, matching engine.py's own contract."""
    report = build_dry_run_report([str(NANDA)], caps_fold=caps_fold, since="7d")
    for section in report.guards:
        for row in section.rows:
            assert row.capsule.get("capsule_id")
            assert row.capsule["asg_payload"]["checkpoint"]["dry_run"] is True


def test_model_note_is_omitted_by_default_and_generator_works_without_it(caps_fold):
    report = build_dry_run_report([str(AMAURY)], caps_fold=caps_fold, since="7d", caps_minor=CAPS_MINOR)
    assert report.model_note is None
    payload = to_payload(report)
    assert payload["model_note"] is None


def test_model_note_passthrough_never_fabricated(caps_fold):
    report = build_dry_run_report(
        [str(AMAURY)],
        caps_fold=caps_fold,
        since="7d",
        caps_minor=CAPS_MINOR,
        model_note="a pre-written operator quote",
        model_id="some-model",
    )
    assert report.model_note.quote == "a pre-written operator quote"
    assert report.model_note.model_id == "some-model"


def test_payload_is_json_and_carries_the_operator(caps_fold):
    report = build_dry_run_report([str(NANDA)], caps_fold=caps_fold, since="7d")
    payload = to_payload(report)
    assert json.loads(json.dumps(payload)) == payload
    assert payload["operator"] == "biz_capsule"


def test_reproducible_replay_is_a_real_precondition_for_verify(caps_fold):
    """Two independent replays of the same ledger must agree byte-for-byte
    (modulo the wall-clock generated_at) -- this is what makes ``--verify``
    a meaningful check rather than a coin flip."""
    first = to_payload(build_dry_run_report([str(NANDA)], caps_fold=caps_fold, since="7d"))
    second = to_payload(build_dry_run_report([str(NANDA)], caps_fold=caps_fold, since="7d"))
    first.pop("generated_at")
    second.pop("generated_at")
    assert first == second


def test_every_cited_capsule_actually_reverifies_with_the_reference_library(caps_fold):
    """Recompute
    each cited capsule's own id with the *public* reference library (not
    this package's code) and confirm it matches."""
    report = build_dry_run_report([str(NANDA)], caps_fold=caps_fold, since="7d")
    checked = 0
    for _, row in report.held_rows:
        assert compute_capsule_id(row.capsule) == row.capsule["capsule_id"]
        checked += 1
        if row.cited_capsule is not None:
            assert compute_capsule_id(row.cited_capsule) == row.cited_capsule["capsule_id"]
            checked += 1
    assert checked > 0


def test_tampering_a_cited_capsule_is_detected_not_silently_passed(caps_fold):
    """The mutant: flip one byte of a real cited capsule's payload and
    confirm recompute stops matching -- a verifier that can't catch this
    isn't verifying anything."""
    report = build_dry_run_report([str(AMAURY)], caps_fold=caps_fold, since="7d", caps_minor=CAPS_MINOR)
    row = report.guards[[s.guard_id for s in report.guards].index("caps")].rows[0]
    tampered = json.loads(json.dumps(row.capsule))
    tampered["asg_payload"]["amount_minor"] = 1  # was 15_000_000

    assert compute_capsule_id(tampered) != tampered["capsule_id"]


# -- CLI ------------------------------------------------------------------


def test_cli_dry_run_writes_report_and_verifies(tmp_path):
    out = tmp_path / "report.json"
    rc = cli_main(
        [
            "guard",
            "dry-run",
            "--store",
            str(AMAURY),
            "--since",
            "7d",
            "--cap",
            "money.transfer=10000000",
            "--out",
            str(out),
            "--verify",
        ]
    )
    assert rc == 0
    assert out.exists()


def test_cli_report_carries_capsule_bodies_only_with_share(tmp_path):
    bare, shared = tmp_path / "bare.json", tmp_path / "shared.json"
    for out, extra in ((bare, []), (shared, ["--share"])):
        assert cli_main(["guard", "dry-run", "--store", str(NANDA), "--since", "7d", "--out", str(out), *extra]) == 0

    def capsules(path):
        rows = [r for g in json.loads(path.read_text())["guards"] for r in g["rows"]]
        return [c for r in rows for c in (r["capsule"], r["cited_capsule"]) if c]

    assert capsules(bare) == []
    shared_capsules = capsules(shared)
    assert shared_capsules
    for capsule in shared_capsules:
        assert compute_capsule_id(capsule) == capsule["capsule_id"]


def test_cli_guards_only_arm_never_writes_capsule_bodies_even_with_share(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPSULE_LEDGER_ARM", "guards-only")
    out = tmp_path / "r.json"
    assert cli_main(["guard", "dry-run", "--store", str(NANDA), "--since", "7d", "--out", str(out), "--share"]) == 0
    rows = [r for g in json.loads(out.read_text())["guards"] for r in g["rows"]]
    assert rows and all(r["capsule"] is None and r["cited_capsule"] is None for r in rows)


def test_cli_rejects_malformed_cap(tmp_path, capsys):
    out = tmp_path / "report.json"
    rc = cli_main(["guard", "dry-run", "--store", str(AMAURY), "--cap", "not-a-cap", "--out", str(out)])
    assert rc == 1
    assert not out.exists()


def test_cli_requires_model_id_alongside_model_note(tmp_path):
    out = tmp_path / "report.json"
    rc = cli_main(
        ["guard", "dry-run", "--store", str(AMAURY), "--model-note", "a quote", "--out", str(out)]
    )
    assert rc == 1
    assert not out.exists()


def test_cli_ledger_directory_binding_reads_a_real_store(tmp_path):
    """The real-deployment path: --ledger pointed at a LedgerStore directory,
    not a loose JSONL file."""
    from capsule_engine.guards.history import open_store
    from capsule_engine.guards.signing import LocalSigner

    store_dir = tmp_path / "store"
    with open_store(store_dir, signer=LocalSigner(key_id="k", secret=b"s"), create=True) as store:
        n = len([store.append(json.loads(line)) for line in AMAURY.read_text(encoding="utf-8").splitlines() if line.strip()])
    assert n == 4

    out = tmp_path / "report.json"
    rc = cli_main(
        [
            "guard",
            "dry-run",
            "--store",
            str(store_dir),
            "--since",
            "7d",
            "--cap",
            "money.transfer=10000000",
            "--out",
            str(out),
            "--verify",
        ]
    )
    assert rc == 0
    assert out.exists()
