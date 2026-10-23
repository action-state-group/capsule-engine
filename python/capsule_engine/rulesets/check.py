# SPDX-License-Identifier: Apache-2.0
"""Load an engine-ruleset/v0 and check one external-check-input/v0 against it.

The ruleset's identity is its ``definition_digest``: the lowercase-hex
SHA-256 of the RFC 8785 (JCS) bytes of the document as parsed, the same
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

import yaml
from agent_action_capsule.canonical import json_digest
from jsonschema import Draft202012Validator

from ..folds.record_type_coverage import evaluate_record_type_coverage
from ..report.result_from_folds import Finding, finding_from_record_type_coverage

__all__ = ["RULESET_FORMAT", "Refusal", "Ruleset", "check", "load_ruleset"]

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


@cache
def _validator(name: str) -> Draft202012Validator:
    text = (resources.files("capsule_engine") / "schemas" / name).read_text(encoding="utf-8")
    return Draft202012Validator(json.loads(text))


def _finding_members() -> frozenset[str]:
    items = _validator("vendor/external-check-result-v0.json").schema["properties"]["findings"]["items"]
    return frozenset(items["properties"])


def _failures(validator: Draft202012Validator, doc: Any) -> list[str]:
    """Each failure's location and the schema keyword that failed, sorted,
    with no value."""
    return sorted({f"{e.json_path}: {e.validator}" for e in validator.iter_errors(doc)})


class _UniqueKeyLoader(yaml.SafeLoader):
    """A YAML mapping with a key twice is refused: the digest is over the
    parsed document, and a repeated key would make the file say two things."""


def _mapping(loader: _UniqueKeyLoader, node: yaml.MappingNode, deep: bool = False) -> dict:
    seen = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in seen:
            raise yaml.constructor.ConstructorError(None, None, f"key {key!r} appears twice", key_node.start_mark)
        seen.add(key)
    return loader.construct_mapping(node, deep=deep)


_UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def load_ruleset(path: str | Path) -> Ruleset:
    """Read and validate an engine-ruleset/v0 file (YAML or JSON)."""
    where = f"the ruleset {path}"
    try:
        doc = yaml.load(Path(path).read_text(encoding="utf-8"), Loader=_UniqueKeyLoader)
    except OSError as exc:
        raise Refusal(f"{where} cannot be read: {exc.strerror}") from exc
    except (yaml.YAMLError, UnicodeDecodeError) as exc:
        raise Refusal(f"{where} is not YAML or JSON: {exc}") from exc
    if not isinstance(doc, dict) or doc.get("schema") != RULESET_FORMAT:
        raise Refusal(f"{where} is not {RULESET_FORMAT} (its schema member must be {RULESET_FORMAT!r})")
    failures = _failures(_validator("engine-ruleset-v0.json"), doc)
    if failures:
        raise Refusal(f"{where} does not fit {RULESET_FORMAT}: " + "; ".join(failures))
    ids = [c["id"] for c in doc["clauses"]]
    repeated = sorted({i for i in ids if ids.count(i) > 1})
    if repeated:
        raise Refusal(f"{where} names a clause id more than once: {', '.join(repeated)}")
    return Ruleset(document=doc, definition_digest=json_digest(doc))


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
    def refuse_constant(name: str) -> None:
        raise ValueError(name)

    try:
        doc = json.loads(raw, parse_constant=refuse_constant)
    except (ValueError, UnicodeDecodeError) as exc:
        raise Refusal(f"the input is not one {INPUT_SCHEMA} JSON object") from exc
    if not isinstance(doc, dict) or doc.get("schema") != INPUT_SCHEMA:
        raise Refusal(f"the input is not {INPUT_SCHEMA}")
    failures = _failures(_validator("vendor/external-check-input-v0.json"), doc)
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
    failures = _failures(_validator("vendor/external-check-result-v0.json"), result)
    if failures:
        # A defect here, not a fault in the input or the ruleset.
        raise RuntimeError(f"the result built does not fit {RESULT_SCHEMA}: " + "; ".join(failures))
    return result
