# SPDX-License-Identifier: Apache-2.0
"""Register row -> requirement compiler: the OSS SAMPLE path from a
loadable ``RegisterRow`` to a ``packs.schema.EvidenceContract``
(profile=='obligation'), carrying the row's ``clause`` through UNCHANGED -- the
"clause trace" a report renderer needs to walk a compiled requirement back to the
exact legal/contractual anchor it was compiled from.

Named ``EvidenceCompiler``, never a second "Outcome Compiler" -- that name is
reserved for the private, paid-tier compiler (a different repo's
``compiler.terms_desk`` et al.). This is the free, mechanical, OSS reference
path: no model call, no per-row human drafting, a fixed table from
``evidence_class`` to the compiled fields (``EVIDENCE_CLASS_DEFAULTS`` below).
Its output is a compiled CANDIDATE, not yet load-bearing -- a human confirms or
edits it before it ships in a real pack, the same posture
``capsule_judge.prompt_compiler.compile_judge_prompt`` takes for a compiled
judge prompt. This module mirrors THAT function's shape (a small, pure,
no-model compile step with explicit refusal errors over a plain mechanical
construction) rather than importing it: capsule-judge depends on capsule-engine,
never the reverse, so the row -> {prompt, requirement} pattern is mirrored
here, not reused by import.
"""
from __future__ import annotations

from dataclasses import dataclass

from agent_action_capsule.canonical import jcs

from ..packs.schema import EvidenceContract
from .errors import INVALID_EVIDENCE_CLASS, RegisterCompilerError
from .schema import EVIDENCE_CLASS_VALUES, ObligationRegister, RegisterRow

__all__ = [
    "EvidenceCompiler",
    "EVIDENCE_CLASS_DEFAULTS",
    "ObligationsPack",
    "ExcludedRow",
    "EXCLUDED_JUDGMENT_REQUIRED",
    "EXCLUDED_SYSTEM_OF_RECORD_READ_REQUIRED",
    "EXCLUDED_CONTESTED_CLAUSE",
]

# Why ``compile_obligations`` leaves a row out of the deterministic pack.
# A row needing a model or human reading of free text:
EXCLUDED_JUDGMENT_REQUIRED = "judgment_required"
# A row needing a live read of an external system of record, which a sealed
# record does not carry:
EXCLUDED_SYSTEM_OF_RECORD_READ_REQUIRED = "system_of_record_read_required"
# A row whose clause is marked contested: whether it binds at all is a legal
# reading, not a check, even when its evidence class is deterministic.
EXCLUDED_CONTESTED_CLAUSE = "contested_clause"


@dataclass(frozen=True)
class _ClassDefaults:
    mode: str
    forward_verdict: str
    backward_verdict: str
    epistemic_type: str | None


# The ONE reconciliation of the register's evidence-class taxonomy into
# ``packs.schema.EvidenceContract``'s own fields -- see ``schema.py``'s module
# docstring for what each class means. The ``eu-ai-act`` catalog pack's pack.yaml
# already established FACT/RULE/JUDGED/CONFIRM/DOC -> mode (its own
# "Evidence-class -> schema mapping" comment); this table is that SAME mapping
# plus STATE, so a register row and a hand-written pack.yaml outcome never
# disagree about what one of these six letters means. Verdict pairs describe
# the INTRINSIC evidence-gathering shape of the class (independent of any one
# corpus's measurability): FACT/RULE/CONFIRM/DOC are checked mechanically from
# a sealed record once one exists (DETERMINISTIC/DETERMINISTIC); JUDGED needs a
# live reviewer call (UNAVAILABLE-MODEL-REQUIRED/MODEL-ASSISTED); STATE reads
# an external system-of-record whose value a capsule doesn't carry until
# instrumented (UNAVAILABLE-STATE-REQUIRED/WITH-INSTRUMENTATION).
EVIDENCE_CLASS_DEFAULTS: dict[str, _ClassDefaults] = {
    "FACT": _ClassDefaults("structural", "DETERMINISTIC", "DETERMINISTIC", "observed_event"),
    "RULE": _ClassDefaults("structural", "DETERMINISTIC", "DETERMINISTIC", None),
    "JUDGED": _ClassDefaults("judged", "UNAVAILABLE-MODEL-REQUIRED", "MODEL-ASSISTED", "semantic_judgment"),
    "CONFIRM": _ClassDefaults("structural", "DETERMINISTIC", "DETERMINISTIC", "producer_claim"),
    "STATE": _ClassDefaults("value", "UNAVAILABLE-STATE-REQUIRED", "WITH-INSTRUMENTATION", "system_of_record_fact"),
    "DOC": _ClassDefaults("structural", "DETERMINISTIC", "DETERMINISTIC", "obligation_reference"),
}


class EvidenceCompiler:
    """Compiles register rows into obligation-profile ``EvidenceContract``
    requirements. Stateless and deterministic -- an instance carries no
    per-call state; it exists (rather than a bare function) only so a future
    non-default ``EVIDENCE_CLASS_DEFAULTS`` override has somewhere to live
    without changing every call site's signature."""

    def __init__(self, class_defaults: dict[str, _ClassDefaults] | None = None) -> None:
        self._class_defaults = class_defaults if class_defaults is not None else EVIDENCE_CLASS_DEFAULTS

    def compile_requirement(self, row: RegisterRow) -> EvidenceContract:
        """Compile one register row into an obligation-profile
        ``EvidenceContract``.

        ``evidence_rule`` is synthesized, not authored: ``"{evidence_class}.
        {scope}"``, mirroring the pack.yaml convention of prefixing
        evidence_rule prose with the evidence-class token
        (the ``eu-ai-act`` catalog pack's own rows all read "FACT. ...", "JUDGED.
        ...", etc.). A DOC row's evidence_rule always appends the same
        never-graded-compliant disclaimer that pack's own DOC rows carry, so a
        report renderer can never round a DOC hit up to a compliance claim.

        ``measurability`` is ``"declared_not_measured"`` when the row names an
        ``evidence_instrument`` (the register author knows exactly which
        signal isn't captured yet) and ``"measured"`` otherwise -- the same
        conditional ``packs/loader.py`` enforces for a hand-written pack
        outcome.

        ``clause`` is carried through UNCHANGED from the row -- the clause
        trace this compiler exists to preserve.
        """
        if row.evidence_class not in EVIDENCE_CLASS_VALUES or row.evidence_class not in self._class_defaults:
            raise RegisterCompilerError(
                INVALID_EVIDENCE_CLASS,
                f"row {row.id!r} has evidence_class={row.evidence_class!r}, not one of "
                f"{sorted(self._class_defaults)}",
            )
        defaults = self._class_defaults[row.evidence_class]

        evidence_rule = f"{row.evidence_class}. {row.scope.strip()}"
        if row.evidence_class == "DOC":
            evidence_rule += " Presence-by-digest only -- never graded 'compliant'."

        return EvidenceContract(
            id=row.id,
            statement=row.statement,
            evidence_rule=evidence_rule,
            forward_verdict=defaults.forward_verdict,
            backward_verdict=defaults.backward_verdict,
            profile="obligation",
            epistemic_type=defaults.epistemic_type,
            measurability="declared_not_measured" if row.evidence_instrument is not None else "measured",
            evidence_instrument=row.evidence_instrument,
            mode=defaults.mode,
            clause_ref=f"{row.source} {row.clause.article}",
            clause=row.clause,
        )

    def compile_obligations(self, register: ObligationRegister) -> ObligationsPack:
        """Compile every deterministic row of ``register`` and name the rest.

        A row is deterministic when its evidence class's forward AND
        backward verdicts are both ``DETERMINISTIC`` in the class table --
        with the default table that is FACT, RULE, CONFIRM and DOC. JUDGED
        rows are excluded as ``judgment_required``, STATE rows as
        ``system_of_record_read_required``. A deterministic row whose
        ``clause.contested`` is true is excluded as ``contested_clause``.

        No model call and no judgment: the result depends only on the
        register's rows and the class table, so the same register always
        compiles to the same ``canonical_bytes()``.
        """
        compiled: list[EvidenceContract] = []
        excluded: list[ExcludedRow] = []
        for row in register.rows:
            requirement = self.compile_requirement(row)
            if requirement.forward_verdict != "DETERMINISTIC" or requirement.backward_verdict != "DETERMINISTIC":
                reason = (
                    EXCLUDED_JUDGMENT_REQUIRED
                    if requirement.mode == "judged"
                    else EXCLUDED_SYSTEM_OF_RECORD_READ_REQUIRED
                )
                excluded.append(ExcludedRow(id=row.id, evidence_class=row.evidence_class, reason=reason))
            elif row.clause.contested:
                excluded.append(
                    ExcludedRow(id=row.id, evidence_class=row.evidence_class, reason=EXCLUDED_CONTESTED_CLAUSE)
                )
            else:
                compiled.append(requirement)
        return ObligationsPack(
            register_id=register.register_id,
            register_digest=register.definition_digest(),
            compiled=tuple(compiled),
            excluded=tuple(excluded),
        )


@dataclass(frozen=True)
class ExcludedRow:
    """A register row ``compile_obligations`` did not compile, with the
    named reason (one of the ``EXCLUDED_*`` constants)."""

    id: str
    evidence_class: str
    reason: str

    def to_dict(self) -> dict:
        return {"id": self.id, "evidence_class": self.evidence_class, "reason": self.reason}


@dataclass(frozen=True)
class ObligationsPack:
    """The deterministic obligations pack compiled from one register:
    ``compiled`` holds an obligation-profile ``EvidenceContract`` per row that
    can be checked mechanically from sealed records, ``excluded`` names every
    other row and why. Both keep register row order. ``register_digest`` is
    the source register's ``definition_digest()``, so a compiled pack names
    exactly which register it came from."""

    register_id: str
    register_digest: str
    compiled: tuple[EvidenceContract, ...]
    excluded: tuple[ExcludedRow, ...]

    def canonical_dict(self) -> dict:
        return {
            "register_id": self.register_id,
            "register_digest": self.register_digest,
            "compiled": [c.canonical_dict() for c in self.compiled],
            "excluded": [e.to_dict() for e in self.excluded],
        }

    def canonical_bytes(self) -> bytes:
        """The JCS bytes of ``canonical_dict()``: the byte-stable form the
        register vectors pin."""
        return jcs(self.canonical_dict())
