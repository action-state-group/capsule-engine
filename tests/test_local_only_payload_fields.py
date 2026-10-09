# SPDX-License-Identifier: Apache-2.0
"""Decision capsules carrying a local-only ``asg_payload`` field never leave
this machine: ``capsule bundle`` and ``capsule guard dry-run --share``, the
two engine outputs made for another party, refuse them, naming the fields
and the record count and never a value. Records without those fields export
unchanged.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from agent_action_capsule import compute_capsule_id, json_digest

from capsule_engine.cli.main import main as cli_main
from capsule_engine.guards import LocalSigner
from capsule_engine.guards.action import Action
from capsule_engine.guards.capsule import (
    ALLOW,
    LOCAL_ONLY_PAYLOAD_FIELDS,
    build_decision_capsule,
    local_only_refusal,
)
from capsule_engine.report.render import decode_fragment

DEAL_BUNDLES = Path(__file__).parent / "fixtures" / "deal-bundles"
REFUND = DEAL_BUNDLES / "deal-purchase-then-refund.bundle.json"
REFUND_DEAL_ID = "deal-1a2f6df4f4186be2"
CAPS_MANIFEST = DEAL_BUNDLES / "caps-v3-manifest.yaml"

# One distinctive value per field, so a leak of any value is greppable.
LOCAL_VALUES = {
    "deal_id": "deal-cccccccccccccccc",
    "item_ref": "item-ref-7f3e91",
    "returned_minor": 31_415,
    "reverses_ref": "e" * 64,
}


def test_the_local_only_fields_are_the_ones_sealed_for_local_matching():
    assert frozenset({"deal_id", "item_ref", "returned_minor", "reverses_ref"}) == LOCAL_ONLY_PAYLOAD_FIELDS
    assert set(LOCAL_VALUES) == LOCAL_ONLY_PAYLOAD_FIELDS


def _decision(seq: int, **fields) -> dict:
    action = Action(
        verb="pay",
        operator="household-a",
        developer="dev@v1",
        action_class="money.purchase",
        action_id=f"pay/{seq}",
        timestamp=f"2026-10-0{seq}T12:00:00Z",
        amount_minor=4_500,
        currency="USD",
        **fields,
    )
    return build_decision_capsule(
        action=action, outcome=ALLOW, constraints=[], signer=LocalSigner(key_id="k1", secret=b"s1"), checkpoint={}
    )


def _ledger(tmp_path: Path, capsules: list[dict]) -> Path:
    path = tmp_path / "ledger.jsonl"
    path.write_text("".join(json.dumps(c) + "\n" for c in capsules))
    return path


def _assert_refusal_names_only(message: str, field: str, count: int) -> None:
    assert f"{count} record(s)" in message
    assert field in message
    assert "local-only" in message
    for value in LOCAL_VALUES.values():
        assert str(value) not in message


# -- the refusal itself --------------------------------------------------------


@pytest.mark.parametrize("field", sorted(LOCAL_VALUES))
def test_a_record_carrying_the_field_is_refused_by_name_and_count(field):
    refusal = local_only_refusal([_decision(1), _decision(2, **{field: LOCAL_VALUES[field]})])
    assert refusal is not None
    _assert_refusal_names_only(refusal, field, 1)


def test_records_carrying_none_of_the_fields_are_not_refused():
    assert local_only_refusal([_decision(1), _decision(2)]) is None


# -- capsule bundle ------------------------------------------------------------


@pytest.mark.parametrize("field", sorted(LOCAL_VALUES))
def test_bundle_refuses_a_decision_carrying_the_field(tmp_path, capsys, field):
    ledger = _ledger(tmp_path, [_decision(1), _decision(2, **{field: LOCAL_VALUES[field]})])
    out = tmp_path / "bundle.json"
    rc = cli_main(["bundle", "--ledger", str(ledger), "--out", str(out), "--with-viewer"])
    printed = capsys.readouterr()
    assert rc == 2
    assert not out.exists()
    assert not out.with_suffix(".html").exists()
    assert "verify:" not in printed.out
    _assert_refusal_names_only(printed.err, field, 1)


def test_bundle_exports_records_without_the_fields_byte_for_byte(tmp_path, capsys):
    capsules = [_decision(1), _decision(2)]
    out = tmp_path / "bundle.json"
    rc = cli_main(["bundle", "--ledger", str(_ledger(tmp_path, capsules)), "--out", str(out)])
    assert rc == 0, capsys.readouterr().err
    exported = json.loads(out.read_text())["records"]
    assert [json_digest(c) for c in exported] == [json_digest(c) for c in capsules]


# -- capsule guard dry-run --share ----------------------------------------------


def _deal_check(seq: int, deal_id: str | None) -> tuple[dict, dict]:
    """A deal check record and a capsule binding it: the same purchase each
    time, so the second check repeats the first and the dedupe row cites it."""
    block = {"record_type": "check", "seq": seq, "counterparty": {"fp_alg": "hmac-sha256-deal-key", "ids": {"payee": "a" * 64}}}
    if deal_id is not None:
        block["deal_id"] = deal_id
    record = {
        "body": {"action": "pay", "action_class": "money.purchase", "taxonomy_version": "2", "currency": "USD",
                 "amount_minor": 4_500, "spend_minor": 4_500, "direction": "out"},
        "x-deal-v0": block,
    }
    capsule = {
        "capsule_id": f"{seq:064x}",
        "action_id": f"deal-0000000000000000/{seq}",
        "action_type": "fyi",
        "operator": "household-a",
        "developer": "capsulectl-deal",
        "timestamp": f"2026-10-0{seq}T12:00:00Z",
        "model_attestation": {"compute_attestation": {"agent_input_digest": json_digest(record)}},
    }
    return capsule, record


def _deal_bundle(tmp_path: Path, deal_id: str | None) -> Path:
    checks = [_deal_check(1, deal_id), _deal_check(2, deal_id)]
    path = tmp_path / "deal.bundle.json"
    path.write_text(json.dumps({
        "records": [c for c, _ in checks],
        "disclosures": {c["capsule_id"]: {"agent_input": r} for c, r in checks},
    }))
    return path


def _dry_run(out: Path, ledger: Path, *extra: str) -> int:
    return cli_main(["guard", "dry-run", "--ledger", str(ledger), "--since", "all", "--out", str(out), *extra])


def test_dry_run_share_refuses_a_decision_carrying_the_deal(tmp_path, capsys):
    out = tmp_path / "report.html"
    rc = _dry_run(out, _deal_bundle(tmp_path, LOCAL_VALUES["deal_id"]), "--share", "--no-manifest")
    printed = capsys.readouterr()
    assert rc == 1
    assert not out.exists()
    assert "file://" not in printed.out
    # The dedupe row's decision and the earlier decision it cites.
    _assert_refusal_names_only(printed.err, "deal_id", 2)


def test_dry_run_share_refuses_the_real_deal_bundle(tmp_path, capsys):
    """Real ``capsulectl bundle --deal`` output: every record states its
    deal, so the decision the caps limit denies seals it."""
    out = tmp_path / "report.html"
    rc = _dry_run(out, REFUND, "--share", "--manifest", str(CAPS_MANIFEST), "--caps-from-manifest")
    printed = capsys.readouterr()
    assert rc == 1
    assert not out.exists()
    assert "deal_id" in printed.err
    assert REFUND_DEAL_ID not in printed.err


def test_dry_run_without_share_still_writes_the_report(tmp_path, capsys):
    out = tmp_path / "report.html"
    rc = _dry_run(out, REFUND, "--manifest", str(CAPS_MANIFEST), "--caps-from-manifest")
    assert rc == 0, capsys.readouterr().err
    assert out.exists()


def test_dry_run_share_exports_decisions_without_the_fields_unchanged(tmp_path, capsys):
    rc = _dry_run(tmp_path / "report.html", _deal_bundle(tmp_path, None), "--share", "--no-manifest", "--verify")
    printed = capsys.readouterr()
    # --verify: the shared payload equals a fresh rebuild, and every capsule
    # in it re-derives its own capsule_id.
    assert rc == 0, printed.err
    url = next(line for line in printed.out.splitlines() if line.startswith("file://"))
    rows = [row for guard in decode_fragment(url.split("#", 1)[1])["guards"] for row in guard["rows"]]
    shared = [c for row in rows for c in (row["capsule"], row["cited_capsule"]) if c]
    assert shared, "the repeat produces a dedupe row citing the earlier decision"
    for capsule in shared:
        assert compute_capsule_id(capsule) == capsule["capsule_id"]
        assert not LOCAL_ONLY_PAYLOAD_FIELDS.intersection(capsule["asg_payload"])
