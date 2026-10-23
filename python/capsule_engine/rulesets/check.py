# SPDX-License-Identifier: Apache-2.0
"""Load an engine-ruleset/v0 and check one external-check-input/v0 against it.

A ruleset is a JSON file. Its identity is its ``definition_digest``: the
lowercase-hex SHA-256 of the RFC 8785 (JCS) bytes of the document, the same
``json_digest`` a fold definition's digest uses. A profile or a contract pins
it, so the ruleset file carries its own format id (``schema``) from v0 on:
a later format is a new id, never a new reading of these bytes. A clause kind
added later that cites a fold or wicket definition carries that definition's
digest in the file, as a pack's ``fold_ref`` does, and the loader refuses a
digest that no longer matches; the ruleset's digest then covers what it cites.

Input and output are capsule-cli's schemas, vendored from the v0.1.0-rc22
release (``schemas/vendor/README.md``). The result is validated before it is
printed, and a finding carries only the members the vendored result schema
lists: capsulectl refuses any other, so capsule-cli#257's ``limit_source`` and
``profile_digest`` are printed once that schema is re-vendored with them.

A refusal names where and which rule of a schema failed, never a value from
the input: an input carries values a refusal must not repeat on stderr (a
payee keyed per profile, a sale's item_ref, the opening of a floor).
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from functools import cache
from importlib import resources
from pathlib import Path
from typing import Any

from agent_action_capsule.canonical import json_digest
from jsonschema import Draft202012Validator

from ..folds.record_type_coverage import evaluate_record_type_coverage
from ..report.result_from_folds import Finding, finding_from_record_type_coverage

__all__ = ["RULESET_FORMAT", "Refusal", "Ruleset", "check", "encode_result", "load_ruleset"]

RULESET_FORMAT = "engine-ruleset/v0"
INPUT_SCHEMA = "external-check-input/v0"
RESULT_SCHEMA = "external-check-result/v0"


class Refusal(Exception):
    """The ruleset or the input cannot be checked; the message is safe to
    print on stderr."""


@dataclass(frozen=True)
class Ruleset:
    document: dict
    definition_digest: str

    @property
    def ruleset_id(self) -> str:
        return self.document["ruleset_id"]


def _open(node: Any) -> Any:
    """``node`` with every ``additionalProperties: false`` removed."""
    if isinstance(node, dict):
        return {k: _open(v) for k, v in node.items() if not (k == "additionalProperties" and v is False)}
    if isinstance(node, list):
        return [_open(v) for v in node]
    return node


@cache
def _validator(name: str) -> Draft202012Validator:
    text = (resources.files("capsule_engine") / "schemas" / name).read_text(encoding="utf-8")
    schema = json.loads(text)
    if name == _INPUT_SCHEMA_FILE:
        # capsulectl adds optional input members between releases (party_role,
        # item_ref, ...), at the top and inside. The checks read only history
        # and history_scope.complete, so a member this copy does not know yet
        # is no reason to refuse a check.
        schema = _open(schema)
    return Draft202012Validator(schema)


_INPUT_SCHEMA_FILE = "vendor/external-check-input-v0.json"
_RESULT_SCHEMA_FILE = "vendor/external-check-result-v0.json"
# capsule-cli RulesMaxOutput: capsulectl reads no more of a checker's stdout.
MAX_OUTPUT = 1 << 20


def _finding_members() -> frozenset[str]:
    items = _validator(_RESULT_SCHEMA_FILE).schema["properties"]["findings"]["items"]
    return frozenset(items["properties"])


def _where(error) -> str:
    """The failure's location, a step that is a key of an open map (such as a
    counterparty's ids) reading ``*``: such a key is a value. The schema path
    says how each step was reached: by name under ``properties``, or as any
    key under ``additionalProperties`` or ``patternProperties``. A location
    the schema path does not account for reads ``*`` throughout."""
    kinds: list[str] = []
    schema_path = list(error.absolute_schema_path)
    i = 0
    while i < len(schema_path):
        keyword = schema_path[i]
        last = i == len(schema_path) - 1
        if keyword == "properties" and not last:
            kinds.append("name")
            i += 1
        elif keyword in ("additionalProperties", "patternProperties") and not last:
            kinds.append("key")
            i += 1 if keyword == "patternProperties" else 0
        elif keyword in ("items", "prefixItems") and not last:
            kinds.append("index")
            i += 1 if keyword == "prefixItems" else 0
        i += 1
    steps = list(error.absolute_path)
    if len(kinds) != len(steps):
        kinds = ["key"] * len(steps)
    return "$" + "".join(
        f"[{s}]" if isinstance(s, int) else f".{s}" if kind == "name" else ".*" for s, kind in zip(steps, kinds, strict=True)
    )


def _failures(validator: Draft202012Validator, doc: Any) -> list[str]:
    """Each failure's location and the schema keyword that failed, sorted,
    with no value."""
    return sorted({f"{_where(e)}: {e.validator}" for e in validator.iter_errors(doc)})


class _Repeated(ValueError):
    pass


def _decode_json(raw: bytes, what: str) -> Any:
    """One JSON value: no member twice in one object, no NaN or Infinity. A
    refusal says where the text stopped parsing, never what it holds."""

    def refuse_constant(name: str) -> None:
        raise ValueError(name)

    def unique(pairs: list[tuple[str, Any]]) -> dict:
        obj = dict(pairs)
        if len(obj) != len(pairs):
            raise _Repeated
        return obj

    try:
        return json.loads(raw, parse_constant=refuse_constant, object_pairs_hook=unique)
    except json.JSONDecodeError as exc:
        raise Refusal(f"{what} is not JSON (line {exc.lineno}, column {exc.colno})") from exc
    except _Repeated as exc:
        raise Refusal(f"{what} is not JSON: a member appears twice in one object") from exc
    except (ValueError, UnicodeDecodeError) as exc:
        raise Refusal(f"{what} is not JSON") from exc


def load_ruleset(path: str | Path) -> Ruleset:
    """Read and validate an engine-ruleset/v0 file (JSON)."""
    where = f"the ruleset {path}"
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        raise Refusal(f"{where} cannot be read: {exc.strerror}") from exc
    doc = _decode_json(raw, where)
    if not isinstance(doc, dict) or doc.get("schema") != RULESET_FORMAT:
        raise Refusal(f"{where} is not {RULESET_FORMAT} (its schema member must be {RULESET_FORMAT!r})")
    failures = _failures(_validator("engine-ruleset-v0.json"), doc)
    if failures:
        raise Refusal(f"{where} does not fit {RULESET_FORMAT}: " + "; ".join(failures))
    ids = [c["id"] for c in doc["clauses"]]
    repeated = sorted({i for i in ids if ids.count(i) > 1})
    if repeated:
        raise Refusal(f"{where} names a clause id more than once: {', '.join(repeated)}")
    try:
        digest = json_digest(doc)
    except UnicodeEncodeError as exc:
        # A \ud800 escape with no pair: valid JSON text, but no UTF-8 bytes.
        raise Refusal(f"{where} holds a string with an unpaired surrogate") from exc
    return Ruleset(document=doc, definition_digest=digest)


def _record_type_coverage(params: dict, envelope: dict) -> Finding:
    result = evaluate_record_type_coverage(
        envelope["history"],
        registered_kinds=frozenset(params["registered_kinds"]),
        kind_field=params["kind_field"],
    )
    return finding_from_record_type_coverage(result, complete=envelope["history_scope"]["complete"])


# Each engine check a clause may name, by name/version. A new version of a
# check is a new entry: a ruleset that names the old one keeps its meaning.
_CHECKS: dict[str, Callable[[dict, dict], Finding]] = {
    "record_type_coverage/1": _record_type_coverage,
}


def _verdict(findings: list[dict]) -> str:
    """The strictest finding: a failing deny clause denies, a failing ask
    clause escalates, then any not_evaluable; otherwise allow. A
    not_applicable finding is an exclusion and moves nothing."""
    fails = {f["disposition"] for f in findings if f["verdict"] == "fail"}
    if "deny" in fails:
        return "deny"
    if fails:
        return "escalate"
    if any(f["verdict"] == "not_evaluable" for f in findings):
        return "not_evaluable"
    return "allow"


def _decode_input(raw: bytes) -> dict:
    doc = _decode_json(raw, "the input")
    if not isinstance(doc, dict) or doc.get("schema") != INPUT_SCHEMA:
        raise Refusal(f"the input is not {INPUT_SCHEMA}")
    failures = _failures(_validator(_INPUT_SCHEMA_FILE), doc)
    if failures:
        raise Refusal(f"the input does not fit {INPUT_SCHEMA}: " + "; ".join(failures))
    return doc


def check(ruleset: Ruleset, raw_input: bytes) -> dict:
    """One external-check-result/v0 for ``raw_input`` (the bytes capsulectl
    writes to the checker's stdin), validated before it is returned."""
    envelope = _decode_input(raw_input)
    members = _finding_members()
    findings = []
    for clause in ruleset.document["clauses"]:
        finding = _CHECKS[clause["check"]](clause["params"], envelope)
        entry = {
            "id": clause["id"],
            "check": clause["check"],
            "verdict": finding.verdict,
            "disposition": clause["disposition"],
            "reason": finding.reason,
            "limit": finding.limit,
            "value": finding.value,
            "limit_source": finding.limit_source,
            "profile_digest": finding.profile_digest,
        }
        findings.append({k: v for k, v in entry.items() if v is not None and k in members})
    result = {
        "schema": RESULT_SCHEMA,
        "ruleset_id": ruleset.ruleset_id,
        "definition_digest": ruleset.definition_digest,
        "verdict": _verdict(findings),
        "tier": "recomputed",
        "findings": findings,
    }
    failures = _failures(_validator(_RESULT_SCHEMA_FILE), result)
    if failures:
        # A defect here, not a fault in the input or the ruleset.
        raise RuntimeError(f"the result built does not fit {RESULT_SCHEMA}: " + "; ".join(failures))
    return result


def encode_result(result: dict) -> bytes:
    """``result`` as the one line the checker prints: UTF-8, not \\u escapes
    (up to 12 bytes a character), refused when capsulectl would not read it
    all. The ruleset format keeps a result under the limit; this holds it."""
    out = (json.dumps(result, separators=(",", ":"), ensure_ascii=False) + "\n").encode()
    if len(out) > MAX_OUTPUT:
        raise Refusal(f"the result is {len(out)} bytes, more than the {MAX_OUTPUT} capsulectl reads")
    return out
