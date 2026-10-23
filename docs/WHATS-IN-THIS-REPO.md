# What's in this repository

This page lists the self-contained pieces of this repository that you can use
on their own, and shows how to run each one. Everything here is Apache-2.0.

## Evidence Contract schema

`python/capsule_engine/schemas/evidence-contract-v0.json` is a JSON Schema (draft 2020-12) for an
Evidence Contract: a document listing the requirements an agent's recorded
actions must meet, with each requirement stating how it is checked.

`python/capsule_engine/schemas/vendor/` holds copies of two schemas the contract refers to
(`epistemic-types.json`, `evidence-result-v0.json`).

## Validator

`python/capsule_engine/packs/contract_validate.py` checks a file against the schema.
It depends only on the schema and `jsonschema`:

```
python -m capsule_engine.packs.contract_validate examples/contracts/*.json
```

Each file prints `valid`, or `INVALID at <path> -- <reason>`. The exit code is
non-zero if any file fails. From Python, use `validate_evidence_contract(doc)`
for a full document or `validate_requirement(doc)` for a single requirement.

## Obligation register format

`python/capsule_engine/register/` defines a register: a flat YAML list of rows, each
row being one obligation. A row has a citation (`clause`), a statement, an
owner, a version and an evidence class (`FACT`, `RULE`, `JUDGED`, `CONFIRM`,
`STATE` or `DOC`). `load_register_file(path)` parses and validates a register.

A two-row sample is in
`python/capsule_engine/register/examples/obligation-register-v0-sample.yaml`. The
EU AI Act catalog pack ships a larger one at
`python/capsule_engine/packs/catalog/eu-ai-act/register.yaml`.

## Sample compiler

`EvidenceCompiler` (`python/capsule_engine/register/compiler.py`) turns one register
row into one obligation requirement. The mapping is a fixed table from
evidence class to compiled fields: no model call, and the row's `clause` is
carried through unchanged. The output is a draft for a person to confirm or
edit, not a finished requirement.

```python
from capsule_engine.register import EvidenceCompiler, load_register_file

register = load_register_file("python/capsule_engine/register/examples/obligation-register-v0-sample.yaml")
compiler = EvidenceCompiler()
requirements = [compiler.compile_requirement(row) for row in register.rows]
```

## Examples

`examples/contracts/` has three valid Evidence Contracts:

- `ai-act-human-oversight.json`
- `dogfood-change-control.json`
- `outcome-claims-adjudication.json`

`examples/contracts/negative/` has nine documents the validator must reject,
one per failure: a missing or unknown profile, a malformed clause or principal
reference, an empty requirement list, and so on. They are a useful test set
for any other implementation of the schema.

## `capsulectl engine check` and the ruleset format

`capsulectl-engine` is a [capsulectl](https://github.com/action-state-group/capsule-cli) plugin
(`cli-plugin/v1`). The package installs it as a console script; put it on a trusted plugin root
with `capsulectl plugin install <path to capsulectl-engine>`. Then:

```
capsulectl engine check --ruleset FILE < input.json
```

reads one `external-check-input/v0` on stdin and prints one `external-check-result/v0` with
`tier: recomputed` and one finding per clause, in the ruleset's order. Exit 0 means a result was
printed, whatever its verdict; any other exit is a refusal, named on stderr, with nothing on
stdout. That is capsulectl's rules-checker contract too, so the same file pins as a deal profile's
rules checker, with `{"command": ["<root>/capsulectl-engine", "check", "--ruleset", "FILE"]}` and
`FILE` an absolute path. capsulectl pins the launcher's bytes, and the launcher is a small stub that
runs the installed `capsule-engine` package, which the pin does not cover, and the
`definition_digest` below covers the ruleset, not the engine code. So what a released check
(such as `record_type_coverage/1`) decides never changes: a change to it is a new version of the
check, a new entry beside the old one. Both
schemas are capsulectl's, vendored from its v0.1.0-rc22 release in
`python/capsule_engine/schemas/vendor/`.

`FILE` is an `engine-ruleset/v0` JSON document. Its JSON Schema is
`python/capsule_engine/schemas/engine-ruleset-v0.json`, and members it does not list are refused:

- `schema`: `engine-ruleset/v0`. A later format is a new value here, never a new reading of v0.
- `ruleset_id`: the name and version the result reports.
- `clauses`: at most 200. Each has an `id` (unique, the finding's id), a `check` (the engine check it runs, as
  name/version), a `disposition` (`ask` or `deny`: what a failing clause does) and the check's
  `params`.

No string in a ruleset holds a control character.

The `definition_digest` the result reports is the lowercase-hex SHA-256 of the RFC 8785 (JCS)
bytes of the document. Any change to the file other than its spelling (member order, whitespace,
escapes) is a new digest, so a profile or a contract can pin it. A member given twice in one
object, NaN, Infinity and an unpaired surrogate are refused.

The one check today is `record_type_coverage/1`: whether each of `registered_kinds` appears as the
string at `kind_field` (a dotted path) in some entry of the input's `history`.

| history                                 | finding          |
|-----------------------------------------|------------------|
| every declared kind is in it            | `pass`           |
| complete, and a declared kind is not    | `fail`           |
| incomplete, and a declared kind is not  | `not_evaluable`  |
| `registered_kinds` is empty             | `not_applicable` |

`not_applicable` is an exclusion: it moves no verdict, so a ruleset whose every finding is
`not_applicable` reports `allow`. The result's verdict is `deny` if a `deny`
clause fails, else `escalate` if an `ask` clause fails, else `not_evaluable` if any finding is,
else `allow`. A finding's `limit` is the number of kinds declared and its `value` the number seen.
`examples/rulesets/everyday-history-coverage.json` is an example.

## Install

See [the README](../README.md#install). `python/tests/test_evidence_contract_schema.py`,
`python/tests/test_register_*.py` and `python/tests/test_engine_plugin.py` exercise all of the above.

## License

Apache-2.0. See [`LICENSE`](../LICENSE).
