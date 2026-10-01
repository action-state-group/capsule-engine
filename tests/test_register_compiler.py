# SPDX-License-Identifier: Apache-2.0
"""EvidenceCompiler ([batch1-obligation-register-v0-sample-compiler]): register
row -> obligation-profile EvidenceContract, with clause trace preserved. Proves
the acceptance line directly: the sample four-eyes policy row and the EU AI Act
Article 12(1) logging row (the same evidence-class taxonomy
``[ldg-eu-ai-act-pack]``'s pack.yaml established) both round-trip into a valid
requirement."""
from __future__ import annotations

from pathlib import Path

import pytest

from capsule_engine.packs.loader import load_pack_dir
from capsule_engine.packs.schema import EVIDENCE_PROFILE_VALUES, MODE_VALUES, ClauseSpec, EvidenceContract
from capsule_engine.register.compiler import EVIDENCE_CLASS_DEFAULTS, EvidenceCompiler
from capsule_engine.register.errors import RegisterCompilerError
from capsule_engine.register.loader import load_register_file
from capsule_engine.register.schema import EVIDENCE_CLASS_VALUES, RegisterRow

SAMPLE_REGISTER_PATH = (
    Path(__file__).parent.parent / "capsule_engine" / "register" / "examples" / "obligation-register-v0-sample.yaml"
)

_GENERIC_CLAUSE = ClauseSpec(instrument="Policy P-1", article="§1")


@pytest.fixture()
def register():
    return load_register_file(SAMPLE_REGISTER_PATH)


@pytest.fixture()
def compiler():
    return EvidenceCompiler()


def test_evidence_class_defaults_cover_every_declared_value():
    assert set(EVIDENCE_CLASS_DEFAULTS) == EVIDENCE_CLASS_VALUES


def test_sample_four_eyes_row_compiles_to_a_valid_obligation_requirement(register, compiler):
    row = register.row("sample/four-eyes/1")
    requirement = compiler.compile_requirement(row)

    assert isinstance(requirement, EvidenceContract)
    assert requirement.id == "sample/four-eyes/1"
    assert requirement.profile == "obligation"
    assert requirement.profile in EVIDENCE_PROFILE_VALUES
    assert requirement.mode in MODE_VALUES
    assert requirement.mode == "structural"  # RULE -> structural
    assert requirement.measurability == "measured"  # no evidence_instrument on this row
    assert requirement.evidence_rule.startswith("RULE. ")
    # clause trace: the exact same ClauseSpec object, not re-derived
    assert requirement.clause is row.clause
    assert requirement.clause.instrument == "Policy P-1"
    assert requirement.clause.article == "§4 (Four-Eyes Approval)"


def test_eu_ai_act_logging_row_compiles_to_a_valid_obligation_requirement(register, compiler):
    row = register.row("EU-12-1")
    requirement = compiler.compile_requirement(row)

    assert requirement.id == "EU-12-1"
    assert requirement.profile == "obligation"
    assert requirement.mode == "structural"  # FACT -> structural
    assert requirement.epistemic_type == "OBSERVED_EVENT"
    assert requirement.measurability == "declared_not_measured"  # row names an evidence_instrument
    assert requirement.evidence_instrument is row.evidence_instrument
    assert requirement.evidence_instrument.field == "native_log_event_kind"
    assert requirement.evidence_rule.startswith("FACT. ")
    # clause trace, verbatim
    assert requirement.clause is row.clause
    assert requirement.clause.instrument == "Regulation (EU) 2024/1689"
    assert requirement.clause.article == "Article 12"
    assert requirement.clause.paragraph == "1"
    assert requirement.clause.as_amended_by == ("Regulation (EU) 2026/1744",)


def test_doc_evidence_class_never_graded_compliant(compiler):
    row = RegisterRow(
        id="doc-row",
        statement="Technical documentation exists.",
        source="EU AI Act",
        scope="the model's technical documentation",
        owner="compliance-team",
        version="1.0.0",
        evidence_class="DOC",
        clause=_GENERIC_CLAUSE,
    )
    requirement = compiler.compile_requirement(row)
    assert "never graded 'compliant'" in requirement.evidence_rule
    assert "compliant" not in requirement.statement.lower()


def test_both_sample_rows_are_obtainable_from_a_pack_definition_shape():
    """The compiled requirement must be the SAME dataclass a real pack.yaml's
    obligation-profile outcomes[] uses -- proven by loading the real
    payments-safety pack and confirming both share one EvidenceContract type."""
    pack_dir = Path(__file__).parent.parent / "capsule_engine" / "packs" / "catalog" / "payments-safety"
    pack = load_pack_dir(pack_dir)
    for outcome in pack.outcomes:
        assert isinstance(outcome, EvidenceContract)


@pytest.mark.parametrize("evidence_class", sorted(EVIDENCE_CLASS_VALUES))
def test_every_evidence_class_compiles_without_error(compiler, evidence_class):
    row = RegisterRow(
        id=f"row-{evidence_class.lower()}",
        statement="Some obligation is satisfied.",
        source="Policy P-1",
        scope="some scope",
        owner="compliance-team",
        version="1.0.0",
        evidence_class=evidence_class,
        clause=_GENERIC_CLAUSE,
    )
    requirement = compiler.compile_requirement(row)
    assert requirement.profile == "obligation"
    assert requirement.mode in MODE_VALUES


def test_unknown_evidence_class_refused(compiler):
    row = RegisterRow(
        id="bad-row",
        statement="x",
        source="x",
        scope="x",
        owner="x",
        version="1.0.0",
        evidence_class="NOT-A-REAL-CLASS",
        clause=_GENERIC_CLAUSE,
    )
    with pytest.raises(RegisterCompilerError) as exc:
        compiler.compile_requirement(row)
    assert exc.value.reason == "invalid_evidence_class"
