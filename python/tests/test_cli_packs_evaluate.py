# SPDX-License-Identifier: Apache-2.0
"""`capsule-engine packs evaluate`: a live pack's rows decided on a list of
actions (capsule-engine#286, for evidencebook-skills `terms test`)."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from capsule_engine import __version__
from capsule_engine.cli.main import main

PACK = Path(__file__).parent / "fixtures" / "packs" / "live-evaluate"


def _dispatch(at: str) -> dict:
    return {"action_type": "payment.dispatch", "target": "acct-1", "amount_minor": 100, "currency": "USD", "at": at}


def _run(tmp_path, capsys, actions, pack=PACK):
    src = tmp_path / "input.json"
    src.write_text(json.dumps({"actions": actions}))
    rc = main(["packs", "evaluate", "--pack", str(pack), "--input", str(src)])
    out = capsys.readouterr()
    return rc, out.out, out.err


def test_one_dispatch_meets_the_row(tmp_path, capsys):
    rc, out, _ = _run(tmp_path, capsys, [_dispatch("2026-11-01T00:00:00Z")])
    assert rc == 0
    doc = json.loads(out)
    assert doc["engine"] == {"name": "capsule-engine", "version": __version__}
    assert doc["pack"]["id"] == "asg/live-sample/0.1.0"
    assert [(r["row"], r["result"]) for r in doc["rows"]] == [("no-duplicate-dispatch", "met")]


def test_a_repeated_dispatch_does_not_meet_the_row(tmp_path, capsys):
    rc, out, _ = _run(tmp_path, capsys, [_dispatch("2026-11-01T00:00:00Z"), _dispatch("2026-11-02T00:00:00Z")])
    assert rc == 0
    (row,) = json.loads(out)["rows"]
    assert (row["row"], row["result"]) == ("no-duplicate-dispatch", "not_met")
    assert "already recorded" in row["reason"]


def test_input_from_stdin(tmp_path, capsys, monkeypatch):
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"actions": [_dispatch("2026-11-01T00:00:00Z")]})))
    assert main(["packs", "evaluate", "--pack", str(PACK)]) == 0
    assert json.loads(capsys.readouterr().out)["rows"][0]["result"] == "met"


def test_unloadable_pack_exits_2_with_the_reason(tmp_path, capsys):
    rc, out, err = _run(tmp_path, capsys, [_dispatch("2026-11-01T00:00:00Z")], pack=tmp_path / "nope")
    assert rc == 2 and out == ""
    assert "pack failed to load" in err


@pytest.mark.parametrize(
    "doc, why",
    [
        ({"actions": []}, "at least one action"),
        ({"cases": []}, "at least one action"),
        ({"actions": ["x"]}, "must be a JSON object"),
        ({"actions": [{"action_type": "payment.dispatch", "amount": 5}]}, "unknown action field(s): amount"),
        ({"actions": [{"at": "t", "timestamp": "t"}]}, "both 'at' and 'timestamp'"),
    ],
)
def test_unusable_input_exits_2(tmp_path, capsys, doc, why):
    src = tmp_path / "input.json"
    src.write_text(json.dumps(doc))
    assert main(["packs", "evaluate", "--pack", str(PACK), "--input", str(src)]) == 2
    out = capsys.readouterr()
    assert out.out == "" and why in out.err


def test_input_that_is_not_json_exits_2(tmp_path, capsys):
    src = tmp_path / "input.json"
    src.write_text("{")
    assert main(["packs", "evaluate", "--pack", str(PACK), "--input", str(src)]) == 2
    assert "unusable input" in capsys.readouterr().err


def test_a_row_the_action_does_not_reach_is_not_evaluable(tmp_path, capsys):
    seller = Path(__file__).parent.parent / "capsule_engine" / "packs" / "catalog" / "seller"
    rc, out, _ = _run(tmp_path, capsys, [{"action_type": "message.send", "target": "x", "at": "2026-11-01T00:00:00Z"}], pack=seller)
    assert rc == 0
    rows = json.loads(out)["rows"]
    row = next(r for r in rows if r["row"] == "s03-price-below-the-floor")
    assert (row["result"], row["reason"]) == ("not_evaluable", "the rule is not configured for this action class")


def test_a_row_no_check_measures_is_not_evaluable(tmp_path, capsys):
    pack = tmp_path / "pack"
    shutil.copytree(PACK, pack)
    source = pack / "pack-source.yaml"
    source.write_text(
        source.read_text().replace(
            "action_semantics:",
            "  - id: unmeasured\n    statement: Declared, not measured.\n    measurability: declared_not_measured\n"
            "    evidence_instrument:\n      kind: structured_field\n      field: some_field\n\naction_semantics:",
            1,
        )
    )
    rc, out, _ = _run(tmp_path, capsys, [_dispatch("2026-11-01T00:00:00Z")], pack=pack)
    assert rc == 0
    assert [(r["row"], r["result"]) for r in json.loads(out)["rows"]] == [
        ("no-duplicate-dispatch", "met"),
        ("unmeasured", "not_evaluable"),
    ]
