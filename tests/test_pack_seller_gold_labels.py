# SPDX-License-Identifier: Apache-2.0
"""Gold labels for the seller pack's fixture corpus: what each row's
expected outcome was established under, captured when the scenarios run.

Run ``python -P tests/test_pack_seller_gold_labels.py`` to rewrite
``fixtures/gold_labels.yaml`` after regenerating the fixture ledger.

The row shape, the validator and the source-tree identity are the everyday
pack's (``tests/test_pack_everyday_gold_labels.py``, which documents every
label); this module only points them at the seller pack. The seller corpus
has no hand-authored row.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import yaml
from capsule_ledger.ledger import LedgerStore

from capsule_engine.packs.loader import load_pack_dir

TESTS = Path(__file__).parent
PACK_DIR = TESTS.parent / "capsule_engine" / "packs" / "catalog" / "seller"
GOLD_PATH = PACK_DIR / "fixtures" / "gold_labels.yaml"
LEDGER_PATH = PACK_DIR / "fixtures" / "mini_ledger.jsonl"


def _load(name: str, filename: str):
    """A sibling test module, loaded by path so this works under pytest and
    run as a script alike."""
    spec = importlib.util.spec_from_file_location(name, TESTS / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gold = _load("everyday_gold_labels", "test_pack_everyday_gold_labels.py")
acceptance = _load("seller_acceptance", "test_pack_seller_acceptance.py")

HEADER = gold.HEADER.replace("everyday fixture corpus", "seller fixture corpus").replace(
    "`python -m tests.test_pack_everyday_gold_labels` from the scenario run that\n"
    "# produced mini_ledger.jsonl -- except the row marked hand_authored, which is\n"
    "# a deliberate constant and must never be replaced by engine output.",
    "`python -P tests/test_pack_seller_gold_labels.py` from the scenario run\n"
    "# that produced mini_ledger.jsonl.",
)


def _run(tmp: Path):
    store = LedgerStore(tmp / "ledger")
    try:
        return acceptance._run_scenarios(store, project_dir=tmp / "project")
    finally:
        store.close()


def _regenerate() -> None:
    import tempfile

    code_value = gold.source_tree_sha256()  # captured at check time, before the run
    with tempfile.TemporaryDirectory() as tmp:
        installed, _, _, records = _run(Path(tmp))
    lines = [json.dumps(r.capsule, separators=(",", ":")) for r in records]
    if lines != LEDGER_PATH.read_text().splitlines():
        raise SystemExit("regenerate mini_ledger.jsonl first (python -P tests/test_pack_seller_acceptance.py)")
    wickets = {w.wicket_id: w.digest for w in installed.manifest.wickets}
    rows = gold.build_rows(installed.pack, wickets, lines, code_value)
    GOLD_PATH.write_text(HEADER + yaml.safe_dump({"rows": rows}, sort_keys=False, width=120))
    print(f"wrote {GOLD_PATH}")


# -- tests ---------------------------------------------------------------------


# Reads raw capsule or fixture JSON/YAML: the test's decoding boundary.
def _gold() -> list[dict]:
    return yaml.safe_load(GOLD_PATH.read_text())["rows"]


def _declared_populations() -> dict[str, str]:
    table = yaml.safe_load((PACK_DIR / "fixtures" / "decision_table.yaml").read_text())["rows"]
    return {
        row["action_id"]: "a" if any(d["population"] == "a" for d in row["n_a"].values()) else "c" for row in table
    }


def test_one_generated_row_per_decision_capsule():
    decisions = [json.loads(line) for line in LEDGER_PATH.read_text().splitlines()]
    assert [r["action_id"] for r in _gold()] == [c["action_id"] for c in decisions if "constraints" in c]
    assert not any(r.get("hand_authored") for r in _gold())


def test_every_row_validates_and_classifies():
    declared = _declared_populations()
    for row in _gold():
        population = declared[row["action_id"]]
        assert gold.classify(dict(row, declared_population=population)) == population


def test_rerunning_each_row_from_its_recorded_pins_reproduces_its_record(tmp_path):
    """Each row's pins still resolve, and replaying the scenarios reproduces
    every recorded capsule_id -- the capsule bytes, verdict included --
    exactly."""
    assert gold.source_tree_sha256() == _gold()[0][gold.FIELD]["value"], (
        "the verdict-producing source changed since the gold labels were captured; "
        "regenerate them with python -P tests/test_pack_seller_gold_labels.py"
    )
    pack = load_pack_dir(PACK_DIR)
    installed, _, capsules, _ = _run(tmp_path)
    replayed = {c["action_id"]: c for c in capsules.values()}
    installed_wickets = {w.wicket_id: w.digest for w in installed.manifest.wickets}
    for row in _gold():
        assert row["config_identity"]["pack_id"] == "asg/seller/0.1.1"
        assert row["config_identity"]["pack_digest"] == pack.definition_digest()
        for wicket_id, digest in row["config_identity"]["wickets"].items():
            assert installed_wickets[wicket_id] == digest, (row["action_id"], wicket_id)
        assert replayed[row["action_id"]]["capsule_id"] == row["capsule_id"], row["action_id"]


if __name__ == "__main__":
    _regenerate()
