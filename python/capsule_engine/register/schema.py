# SPDX-License-Identifier: Apache-2.0
"""Obligation register format v0:
a flat, loadable list of register rows -- the GRC-shaped input a compliance team
maintains directly (a spreadsheet of citations), distinct from and upstream of a
pack.yaml's own ``outcomes[]``. ``compiler.compile_requirement`` is what turns one
row into a full ``packs.schema.EvidenceContract`` (profile=='obligation') -- this
module only declares the row shape the compiler consumes; it declares no
evidence_rule/verdict-pair/mode of its own, the same "declare once, compile
forward" split ``packs.schema.Obligation``/``EvidenceContract`` already draw for a
pack's own obligations/outcomes.

**Evidence-class taxonomy: FACT/RULE/JUDGED/CONFIRM/STATE/DOC.** Reconciled to be
ONE definition with the ``eu-ai-act`` catalog pack's pack.yaml, which already established
FACT/RULE/JUDGED/CONFIRM/DOC as its own "Evidence-class -> schema mapping" comment
(mode: FACT -> structural/value, RULE -> structural, JUDGED -> judged, CONFIRM ->
structural + an external-confirmation evidence_instrument, DOC -> structural,
presence-by-digest only, never graded "compliant"). This register adds exactly one
class that pack didn't need: STATE, for a live system-of-record read (the
``system_of_record_fact`` epistemic type of draft-mih-agent-evidence-layer-00
section 4.1, registered in agent-action-capsule spec/REGISTRY.md section 17;
e.g. "payment state == settled") -- a claim that needs a record which doesn't
exist in a capsule at seal time, same shape as FACT/DOC, but about EXTERNAL system
state rather than the agent's own sealed action stream. ``compiler.
EVIDENCE_CLASS_DEFAULTS`` is the one place this taxonomy is cashed out into
``EvidenceContract``'s own mode/verdict-pair/epistemic_type fields -- never
re-derived ad hoc anywhere else.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent_action_capsule.canonical import FloatInDigestError, UnsafeIntegerError, json_digest

from ..packs.schema import ClauseSpec, EvidenceInstrument
from .errors import FLOAT_IN_REGISTER_DIGEST, UNSAFE_INTEGER_IN_REGISTER_DIGEST, RegisterDefinitionError

__all__ = ["EVIDENCE_CLASS_VALUES", "RegisterRow", "ObligationRegister"]

# The six evidence classes a register row may declare -- see module docstring.
EVIDENCE_CLASS_VALUES = frozenset({"FACT", "RULE", "JUDGED", "CONFIRM", "STATE", "DOC"})


@dataclass(frozen=True)
class RegisterRow:
    """One obligation register entry: the minimal, spreadsheet-shaped fact a GRC
    team declares -- WHAT must be established (``statement``), WHERE it comes
    from (``source``, ``clause``), WHAT it covers (``scope``), WHO owns it
    (``owner``), and WHAT KIND of evidence answers it (``evidence_class``).
    ``compiler.compile_requirement`` derives the rest (evidence_rule prose,
    verdict pair, mode, measurability) -- a register row never declares those
    compiled fields itself.

    ``effective_from``/``effective_until`` are THIS REGISTER ENTRY's own
    governance window (when the register started/stopped tracking it) --
    distinct from ``clause.effective_from``, which is when the CITED LAW/POLICY
    itself took effect. Both dates are real and independent: a register can
    start tracking an obligation years after the clause it cites took effect,
    or retire an entry while the underlying clause remains in force (superseded
    by a newer entry).

    ``evidence_instrument``, when set, names the specific signal this row's
    evidence isn't captured by yet (``packs.schema.EvidenceInstrument`` --
    the exact same shape a pack outcome's own ``evidence_instrument`` uses).
    """

    id: str
    statement: str
    source: str
    scope: str
    owner: str
    version: str
    evidence_class: str
    clause: ClauseSpec
    effective_from: str | None = None
    effective_until: str | None = None
    evidence_instrument: EvidenceInstrument | None = None

    def canonical_dict(self) -> dict:
        out: dict[str, Any] = {
            "id": self.id,
            "statement": self.statement,
            "source": self.source,
            "scope": self.scope,
            "owner": self.owner,
            "version": self.version,
            "evidence_class": self.evidence_class,
            "clause": self.clause.to_dict(),
        }
        if self.effective_from is not None:
            out["effective_from"] = self.effective_from
        if self.effective_until is not None:
            out["effective_until"] = self.effective_until
        if self.evidence_instrument is not None:
            out["evidence_instrument"] = self.evidence_instrument.to_dict()
        return out


@dataclass(frozen=True)
class ObligationRegister:
    """A named set of register rows -- the loadable-data unit ``loader.py``
    produces from a register file. Row order is preserved (pack convention),
    never sorted; ``loader.py`` refuses a register with a duplicate row
    ``id``, so ``row()`` below never has to pick between two matches."""

    register_id: str
    rows: tuple[RegisterRow, ...]

    def row(self, row_id: str) -> RegisterRow | None:
        for r in self.rows:
            if r.id == row_id:
                return r
        return None

    def canonical_dict(self) -> dict:
        return {
            "register_id": self.register_id,
            "rows": [r.canonical_dict() for r in self.rows],
        }

    def definition_digest(self) -> str:
        """SHA-256 over the JCS bytes of the canonical register -- same digest
        discipline as ``packs.schema.PackDefinition.definition_digest()``."""
        try:
            return json_digest(self.canonical_dict())
        except FloatInDigestError as exc:
            raise RegisterDefinitionError(FLOAT_IN_REGISTER_DIGEST, str(exc)) from exc
        except UnsafeIntegerError as exc:
            raise RegisterDefinitionError(UNSAFE_INTEGER_IN_REGISTER_DIGEST, str(exc)) from exc
