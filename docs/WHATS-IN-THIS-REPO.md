# What's in this repository

This page lists the self-contained pieces of this repository that you can use
on their own, and shows how to run each one. Everything here is Apache-2.0.

## Evidence Contract schema

`schemas/evidence-contract-v0.json` is a JSON Schema (draft 2020-12) for an
Evidence Contract: a document listing the requirements an agent's recorded
actions must meet, with each requirement stating how it is checked.

`schemas/vendor/` holds copies of two schemas the contract refers to
(`epistemic-types.json`, `evidence-result-v0.json`).

## Validator

`capsule_engine/packs/contract_validate.py` checks a file against the schema.
It depends only on the schema and `jsonschema`:

```
python -m capsule_engine.packs.contract_validate examples/contracts/*.json
```

Each file prints `valid`, or `INVALID at <path> -- <reason>`. The exit code is
non-zero if any file fails. From Python, use `validate_evidence_contract(doc)`
for a full document or `validate_requirement(doc)` for a single requirement.

## Obligation register format

`capsule_engine/register/` defines a register: a flat YAML list of rows, each
row being one obligation. A row has a citation (`clause`), a statement, an
owner, a version and an evidence class (`FACT`, `RULE`, `JUDGED`, `CONFIRM`,
`STATE` or `DOC`). `load_register_file(path)` parses and validates a register.

A two-row sample is in
`capsule_engine/register/examples/obligation-register-v0-sample.yaml`. The
EU AI Act catalog pack ships a larger one at
`capsule_engine/packs/catalog/eu-ai-act/register.yaml`.

## Sample compiler

`EvidenceCompiler` (`capsule_engine/register/compiler.py`) turns one register
row into one obligation requirement. The mapping is a fixed table from
evidence class to compiled fields: no model call, and the row's `clause` is
carried through unchanged. The output is a draft for a person to confirm or
edit, not a finished requirement.

```python
from capsule_engine.register import EvidenceCompiler, load_register_file

register = load_register_file("capsule_engine/register/examples/obligation-register-v0-sample.yaml")
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

## Install

See [the README](../README.md#install). `tests/test_evidence_contract_schema.py`
and `tests/test_register_*.py` exercise all of the above.

## License

Apache-2.0. See [`LICENSE`](../LICENSE).
