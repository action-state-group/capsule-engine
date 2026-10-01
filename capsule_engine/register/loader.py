# SPDX-License-Identifier: Apache-2.0
"""YAML front door for the obligation register: a register file/dict ->
a validated ``ObligationRegister``.

Mirrors ``packs/loader.py``'s own discipline for the ``clause``/
``evidence_instrument`` sub-shapes (named-reason errors, a message that names
the field and shows a correct example) but does not import from it: the two
loaders validate the SAME ``packs.schema.ClauseSpec``/``EvidenceInstrument``
shapes into DIFFERENT top-level documents (a register file here, a pack.yaml
there), with different error-message context (``register[...]`` vs
``outcomes[...]``), and ``packs/loader.py``'s own parsing helpers are private
(leading-underscore) by design. Duplicated on purpose rather than forced into
a shared module for two call sites -- keep both in sync by hand if
``ClauseSpec``/``EvidenceInstrument`` grow a field.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from ..packs.schema import ClauseSpec, EvidenceInstrument
from .errors import (
    DUPLICATE_ROW_ID,
    INVALID_CLAUSE,
    INVALID_EFFECTIVE_DATE,
    INVALID_EVIDENCE_CLASS,
    INVALID_EVIDENCE_INSTRUMENT,
    MALFORMED_REGISTER,
    MISSING_REQUIRED_FIELD,
    RegisterDefinitionError,
)
from .schema import EVIDENCE_CLASS_VALUES, ObligationRegister, RegisterRow

__all__ = ["load_register_dict", "load_register_file"]

# Same two constants/purpose as packs/loader.py's own copies: clause.effective_from
# / register row effective_from|effective_until / clause.text_snapshot_digest are
# compared/looked up as plain strings downstream, so a malformed value here would
# silently miscompare rather than raise.
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
_EVIDENCE_INSTRUMENT_KINDS = frozenset({"structured_field", "tool_call_name"})


def _require_mapping(data: Any, what: str) -> dict:
    if not isinstance(data, dict):
        raise RegisterDefinitionError(MALFORMED_REGISTER, f"{what} must be a mapping, got {type(data).__name__}")
    return data


def _require_nonempty_str(value: Any, field_name: str, example: str) -> str:
    if not isinstance(value, str) or not value:
        raise RegisterDefinitionError(
            MISSING_REQUIRED_FIELD, f"{field_name!r} is required and must be a non-empty string, e.g. {example!r}"
        )
    return value


def _parse_clause_spec(raw: Any, *, row_id: str) -> ClauseSpec:
    raw = _require_mapping(raw, f"register[{row_id!r}].clause")
    instrument = _require_nonempty_str(
        raw.get("instrument"), f"register[{row_id!r}].clause.instrument", "Regulation (EU) 2024/1689"
    )
    article = _require_nonempty_str(raw.get("article"), f"register[{row_id!r}].clause.article", "Article 12")

    as_amended_by_raw = raw.get("as_amended_by", [])
    if not isinstance(as_amended_by_raw, list) or not all(isinstance(v, str) for v in as_amended_by_raw):
        raise RegisterDefinitionError(
            INVALID_CLAUSE,
            f"register[{row_id!r}].clause.as_amended_by must be a list of strings or omitted, "
            'e.g. as_amended_by: ["Regulation (EU) 2026/1744"]',
        )

    for field_name, example in (
        ("paragraph", "1"),
        ("jurisdiction", "EU"),
        ("source_url", "https://eur-lex.europa.eu/..."),
    ):
        value = raw.get(field_name)
        if value is not None and not isinstance(value, str):
            raise RegisterDefinitionError(
                INVALID_CLAUSE,
                f"register[{row_id!r}].clause.{field_name} must be a string or omitted, e.g. {example!r}",
            )

    text_snapshot_digest = raw.get("text_snapshot_digest")
    if text_snapshot_digest is not None and not _SHA256_HEX_RE.match(text_snapshot_digest):
        raise RegisterDefinitionError(
            INVALID_CLAUSE,
            f"register[{row_id!r}].clause.text_snapshot_digest must be a 64-char lowercase hex SHA-256 "
            "digest or omitted",
        )

    clause_effective_from = raw.get("effective_from")
    if clause_effective_from is not None and not _ISO_DATE_RE.match(clause_effective_from):
        raise RegisterDefinitionError(
            INVALID_CLAUSE,
            f"register[{row_id!r}].clause.effective_from must be an ISO-8601 date (YYYY-MM-DD) or omitted, "
            'e.g. effective_from: "2027-12-02"',
        )

    contested = raw.get("contested", False)
    if not isinstance(contested, bool):
        raise RegisterDefinitionError(
            INVALID_CLAUSE, f"register[{row_id!r}].clause.contested must be a boolean or omitted"
        )

    return ClauseSpec(
        instrument=instrument,
        article=article,
        as_amended_by=tuple(as_amended_by_raw),
        paragraph=raw.get("paragraph"),
        jurisdiction=raw.get("jurisdiction"),
        text_snapshot_digest=text_snapshot_digest,
        effective_from=clause_effective_from,
        source_url=raw.get("source_url"),
        contested=contested,
    )


def _parse_evidence_instrument(raw: Any, *, row_id: str) -> EvidenceInstrument:
    raw = _require_mapping(raw, f"register[{row_id!r}].evidence_instrument")
    kind = raw.get("kind")
    if kind not in _EVIDENCE_INSTRUMENT_KINDS:
        raise RegisterDefinitionError(
            INVALID_EVIDENCE_INSTRUMENT,
            f"register[{row_id!r}].evidence_instrument.kind={kind!r} must be one of "
            f"{sorted(_EVIDENCE_INSTRUMENT_KINDS)}",
        )
    if kind == "structured_field":
        field_name = raw.get("field")
        if not isinstance(field_name, str) or not field_name:
            raise RegisterDefinitionError(
                INVALID_EVIDENCE_INSTRUMENT,
                f"register[{row_id!r}].evidence_instrument.field is required and must be a non-empty string "
                "for kind: structured_field, e.g. field: native_log_event_kind",
            )
        return EvidenceInstrument(kind=kind, field=field_name)
    name = raw.get("name")
    if not isinstance(name, str) or not name:
        raise RegisterDefinitionError(
            INVALID_EVIDENCE_INSTRUMENT,
            f"register[{row_id!r}].evidence_instrument.name is required and must be a non-empty string "
            "for kind: tool_call_name, e.g. name: issue_refund",
        )
    return EvidenceInstrument(kind=kind, name=name)


def _parse_row(entry: Any, *, index: int) -> RegisterRow:
    entry = _require_mapping(entry, f"rows[{index}]")
    row_id = _require_nonempty_str(entry.get("id"), f"rows[{index}].id", "eu-ai-act/art-12-1")
    statement = _require_nonempty_str(
        entry.get("statement"), f"register[{row_id!r}].statement", "The agent automatically records ..."
    )
    source = _require_nonempty_str(entry.get("source"), f"register[{row_id!r}].source", "EU AI Act")
    scope = _require_nonempty_str(entry.get("scope"), f"register[{row_id!r}].scope", "all agent actions")
    owner = _require_nonempty_str(entry.get("owner"), f"register[{row_id!r}].owner", "compliance-team")
    version = _require_nonempty_str(entry.get("version"), f"register[{row_id!r}].version", "1.0.0")

    evidence_class = entry.get("evidence_class")
    if evidence_class not in EVIDENCE_CLASS_VALUES:
        raise RegisterDefinitionError(
            INVALID_EVIDENCE_CLASS,
            f"register[{row_id!r}].evidence_class={evidence_class!r} must be one of "
            f"{sorted(EVIDENCE_CLASS_VALUES)}",
        )

    clause_raw = entry.get("clause")
    if clause_raw is None:
        raise RegisterDefinitionError(
            MISSING_REQUIRED_FIELD,
            f"register[{row_id!r}].clause is required -- every register row is anchored to a specific "
            "legal/contractual clause, e.g.:\n"
            "clause:\n"
            "  instrument: Regulation (EU) 2024/1689\n"
            "  article: Article 12\n"
            '  paragraph: "1"',
        )
    clause = _parse_clause_spec(clause_raw, row_id=row_id)

    effective_from = entry.get("effective_from")
    effective_until = entry.get("effective_until")
    for field_name, value in (("effective_from", effective_from), ("effective_until", effective_until)):
        if value is not None and not _ISO_DATE_RE.match(value):
            raise RegisterDefinitionError(
                INVALID_EFFECTIVE_DATE,
                f"register[{row_id!r}].{field_name} must be an ISO-8601 date (YYYY-MM-DD) or omitted, "
                f'e.g. {field_name}: "2026-01-01"',
            )

    evidence_instrument_raw = entry.get("evidence_instrument")
    evidence_instrument = (
        _parse_evidence_instrument(evidence_instrument_raw, row_id=row_id)
        if evidence_instrument_raw is not None
        else None
    )

    return RegisterRow(
        id=row_id,
        statement=statement,
        source=source,
        scope=scope,
        owner=owner,
        version=version,
        evidence_class=evidence_class,
        clause=clause,
        effective_from=effective_from,
        effective_until=effective_until,
        evidence_instrument=evidence_instrument,
    )


def load_register_dict(raw: Any) -> ObligationRegister:
    """Validate an already-parsed register mapping (e.g. from ``yaml.safe_load``)
    into an ``ObligationRegister``."""
    raw = _require_mapping(raw, "register")
    register_id = _require_nonempty_str(raw.get("register_id"), "register_id", "asg/obligation-register/0.1.0")

    rows_raw = raw.get("rows")
    if not isinstance(rows_raw, list) or not rows_raw:
        raise RegisterDefinitionError(
            MISSING_REQUIRED_FIELD, "'rows' is required (a register ships at least one row)"
        )

    rows: list[RegisterRow] = []
    seen_ids: set[str] = set()
    for index, entry in enumerate(rows_raw):
        row = _parse_row(entry, index=index)
        if row.id in seen_ids:
            raise RegisterDefinitionError(DUPLICATE_ROW_ID, f"duplicate register row id {row.id!r}")
        seen_ids.add(row.id)
        rows.append(row)

    return ObligationRegister(register_id=register_id, rows=tuple(rows))


def load_register_file(path: str | Path) -> ObligationRegister:
    """Read and validate a register YAML file into an ``ObligationRegister``."""
    path = Path(path)
    if not path.is_file():
        raise RegisterDefinitionError(MALFORMED_REGISTER, f"register file not found: {path}")
    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    return load_register_dict(raw)
