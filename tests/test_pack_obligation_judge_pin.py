# SPDX-License-Identifier: Apache-2.0
"""``Obligation.mode`` and ``Obligation.judge_pin``: a judged obligation names
the judge that answers it.

A ``mode: judged`` obligation is measured by a pinned judge -- model, prompt
template hash, answer-schema hash, the action fields it reads, and where the
model runs -- never by a check. The pin is required on a judged obligation
and refused on any other. A judged obligation may ASK but never NEVER, and a
pin to a hosted model cannot claim ``pure_replay``. No prompt text, literal
or interpolated, may appear on an obligation or its pin.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from capsule_engine.packs.errors import (
    INVALID_JUDGE_PIN,
    INVALID_MEASURABILITY,
    INVALID_MODE,
    INVALID_RE_DERIVABILITY_GRADE,
    JUDGED_DISPOSITION_NEVER,
    MISSING_JUDGE_PIN,
    PROMPT_TEXT_IN_PACK,
    PackDefinitionError,
)
from capsule_engine.packs.loader import load_pack_dir
from capsule_engine.packs.schema import JudgePin

CATALOG = Path(__file__).parent.parent / "capsule_engine" / "packs" / "catalog"

TEMPLATE_HASH = "a" * 64
SCHEMA_HASH = "b" * 64


# Builds raw pack YAML, malformed entries included: the test's encoding boundary.
def _pin(**overrides) -> dict:
    pin = {
        "model_id": "example-judge-1",
        "prompt_template_hash": TEMPLATE_HASH,
        "schema_hash": SCHEMA_HASH,
        "input_refs": ["outgoing_content"],
        "model_hosting": "hosted",
    }
    pin.update(overrides)
    return pin


# Builds raw pack YAML, malformed entries included: the test's encoding boundary.
def _judged(**overrides) -> dict:
    entry = {
        "id": "message-commits-the-user",
        "statement": "Asks before sending a message that commits the user to money, a date or a promise.",
        "mode": "judged",
        "judge_pin": _pin(),
        "default_disposition": "ASK",
    }
    entry.update(overrides)
    return entry


# Builds raw pack YAML, malformed entries included: the test's encoding boundary.
def _pack_with(tmp_path: Path, obligation: dict) -> Path:
    """payments-safety with ``obligation`` appended to its obligations."""
    pack_dir = tmp_path / "pack"
    shutil.copytree(CATALOG / "payments-safety", pack_dir)
    doc = yaml.safe_load((pack_dir / "pack.yaml").read_text())
    doc["obligations"].append(obligation)
    (pack_dir / "pack.yaml").write_text(yaml.safe_dump(doc, sort_keys=False))
    return pack_dir


# Builds raw pack YAML, malformed entries included: the test's encoding boundary.
def _refused(tmp_path: Path, obligation: dict) -> str:
    with pytest.raises(PackDefinitionError) as exc:
        load_pack_dir(_pack_with(tmp_path, obligation))
    return exc.value.reason


def test_a_judged_obligation_parses_with_its_pin_and_enters_the_digest(tmp_path):
    base = load_pack_dir(CATALOG / "payments-safety")
    pack = load_pack_dir(_pack_with(tmp_path, _judged()))
    judged = pack.obligations[-1]
    assert judged.mode == "judged"
    assert judged.check is None
    assert judged.measurability == "measured"
    assert judged.judge_pin == JudgePin(
        model_id="example-judge-1",
        prompt_template_hash=TEMPLATE_HASH,
        schema_hash=SCHEMA_HASH,
        input_refs=("outgoing_content",),
        model_hosting="hosted",
    )
    canonical = pack.canonical_dict()["obligations"][-1]
    assert canonical["mode"] == "judged"
    assert canonical["judge_pin"] == _pin()
    assert "check" not in canonical
    assert pack.definition_digest() != base.definition_digest()


def test_each_pin_field_moves_the_digest(tmp_path):
    first = load_pack_dir(_pack_with(tmp_path / "a", _judged())).definition_digest()
    for key, value in (
        ("model_id", "example-judge-2"),
        ("prompt_template_hash", "c" * 64),
        ("schema_hash", "d" * 64),
        ("input_refs", ["outgoing_content", "target"]),
        ("model_hosting", "self_hosted"),
    ):
        moved = load_pack_dir(_pack_with(tmp_path / key, _judged(judge_pin=_pin(**{key: value}))))
        assert moved.definition_digest() != first, key


def test_an_undeclared_mode_and_pin_are_absent_from_the_canonical_form():
    pack = load_pack_dir(CATALOG / "payments-safety")
    for obligation in pack.canonical_dict()["obligations"]:
        assert "mode" not in obligation
        assert "judge_pin" not in obligation


@pytest.mark.parametrize("name", sorted(p.name for p in CATALOG.iterdir() if (p / "pack.yaml").exists()))
def test_no_catalog_pack_declares_a_judged_obligation_yet(name):
    pack = load_pack_dir(CATALOG / name)
    assert all(o.mode == "structural" and o.judge_pin is None for o in pack.obligations)


def test_a_judged_obligation_without_a_pin_is_refused(tmp_path):
    entry = _judged()
    del entry["judge_pin"]
    assert _refused(tmp_path, entry) == MISSING_JUDGE_PIN


def test_a_pin_on_an_obligation_that_is_not_judged_is_refused(tmp_path):
    entry = _judged()
    del entry["mode"]
    entry["check"] = "caps"
    assert _refused(tmp_path, entry) == INVALID_JUDGE_PIN


def test_a_judged_obligation_may_ask(tmp_path):
    pack = load_pack_dir(_pack_with(tmp_path, _judged(default_disposition="ASK")))
    assert pack.obligations[-1].default_disposition == "ASK"


def test_a_judged_obligation_may_not_be_never(tmp_path):
    assert _refused(tmp_path, _judged(default_disposition="NEVER")) == JUDGED_DISPOSITION_NEVER


def test_a_judged_obligation_may_not_cite_a_check(tmp_path):
    assert _refused(tmp_path, _judged(check="caps")) == INVALID_JUDGE_PIN


def test_a_judged_obligation_may_not_be_declared_not_measured(tmp_path):
    entry = _judged(
        measurability="declared_not_measured",
        evidence_instrument={"kind": "structured_field", "field": "commitment_classification"},
    )
    assert _refused(tmp_path, entry) == INVALID_MEASURABILITY


def test_a_hosted_judge_cannot_claim_pure_replay(tmp_path):
    assert _refused(tmp_path, _judged(re_derivability_grade="pure_replay")) == INVALID_RE_DERIVABILITY_GRADE


def test_a_self_hosted_judge_may_declare_pure_replay(tmp_path):
    entry = _judged(re_derivability_grade="pure_replay", judge_pin=_pin(model_hosting="self_hosted"))
    assert load_pack_dir(_pack_with(tmp_path, entry)).obligations[-1].re_derivability_grade == "pure_replay"


@pytest.mark.parametrize("value", ["fold_magic", "JUDGED", ""])
def test_an_obligation_mode_outside_the_closed_set_is_refused(tmp_path, value):
    assert _refused(tmp_path, _judged(mode=value)) == INVALID_MODE


@pytest.mark.parametrize(
    "entry",
    [
        _judged(literal_prompt="Does this message commit the user? Message: ..."),
        _judged(prompt="Does this message commit the user?"),
        _judged(judge_pin={**_pin(), "interpolated_prompt": "Message: ..."}),
        _judged(judge_pin={**_pin(), "prompt_text": "Does this message commit the user?"}),
        # Prompt text is refused on any obligation, judged or not.
        {"id": "x", "statement": "y", "check": "caps", "literal_prompt": "Message: ..."},
    ],
    ids=["obligation-literal", "obligation-prompt", "pin-interpolated", "pin-text", "not-judged"],
)
def test_prompt_text_never_enters_a_pack(tmp_path, entry):
    assert _refused(tmp_path, entry) == PROMPT_TEXT_IN_PACK


@pytest.mark.parametrize(
    ("overrides", "why"),
    [
        ({"model_id": ""}, "empty model_id"),
        ({"prompt_template_hash": "A" * 64}, "uppercase hash"),
        ({"prompt_template_hash": "a" * 63}, "short hash"),
        ({"schema_hash": "a" * 64 + "\n"}, "hash with a trailing newline"),
        ({"schema_hash": None}, "missing schema hash"),
        ({"input_refs": []}, "no input refs"),
        ({"input_refs": ["message_body"]}, "unknown field"),
        ({"input_refs": ["target", "target"]}, "duplicate field"),
        ({"model_hosting": "cloud"}, "hosting outside the closed set"),
        ({"temperature": 0}, "unknown key"),
    ],
)
def test_a_malformed_pin_is_refused(tmp_path, overrides, why):
    assert _refused(tmp_path, _judged(judge_pin=_pin(**overrides))) == INVALID_JUDGE_PIN, why
