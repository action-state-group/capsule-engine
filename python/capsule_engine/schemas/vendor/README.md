# Vendored schemas

These files are copies of schemas that another project owns. They are not
defined here. A change to an owning schema is drift that must be re-vendored
here, never re-derived by hand.

## `evidence-result-v0.json`

- **Owner:** agent-action-capsule, `schemas/evidence-result-v0.json` (spec:
  `spec/evidence-result-v0.md`)
- **Copied from commit:** `627dd8c16f7eb0dd00169f199ba6b938fb179913` (agent-action-capsule
  `main`, PR #157 merged: admits `x-` extension members on the document and on a claim, spec
  section 8a, plus the PROPOSED `coverage_report` section, spec section 7.1, with lower-case
  epistemic types). Byte-identical to the earlier copy from branch commit `b109a9e4`.
- **Copied on:** 2026-10-17
- **sha256:** `cf46d273e0220a6a6e9cfa4e41b331b6a6a155d053ca81cc33f3c004dfa734a9`
  (pinned by `tests/test_report_result_extension_slot.py`)
- **Form:** byte-for-byte; the file carries no local additions. Check with
  `git -C <agent-action-capsule checkout> cat-file blob 627dd8c16f7eb0dd00169f199ba6b938fb179913:schemas/evidence-result-v0.json | cmp - capsule_engine/schemas/vendor/evidence-result-v0.json`.
- **Why it is vendored:** the agent-action-capsule release this repository pins
  does not ship the schema as an importable resource. Drop this copy and import
  the schema from agent-action-capsule once it does (see
  `capsule_engine/report/result.py`'s module docstring).

## `epistemic-types.json`

A transcription of the closed `epistemic_type` value set owned by the
Internet-Draft `draft-mih-agent-evidence-layer-00`, section 4.1 "Epistemic
Type", posted to the IETF datatracker as revision 00
(<https://datatracker.ietf.org/doc/draft-mih-agent-evidence-layer/>). Until
IANA creates its registry, the interim registry is agent-action-capsule
`spec/REGISTRY.md` section 17. Its provenance is recorded inside the file (the
`$comment` and `source` fields), because no machine-readable copy exists
upstream to copy byte-for-byte.

## `external-check-input-v0.json` and `external-check-result-v0.json`

- **Owner:** capsule-cli, `skills/deal/profile/external-check-input-v0.schema.json` and
  `skills/deal/profile/external-check-result-v0.schema.json` (the result is byte-identical to the
  copy capsulectl embeds, `plugins/internal/plugin/assets/external-check-result-v0.schema.json`).
- **Copied from:** the `v0.1.0-rc22` release, commit `e66369627c41ff54a8318b39b7d835c16181beba`.
- **Copied on:** 2026-10-23
- **sha256:** input `468d77cc2492b3888385c582aaaa3a4c44df3eaf01ad6ef612338e6ddb380a39`, result
  `61e5ffe59b2abc8390e69228c7d7e5ba86562c6550a59008c15fb3990a24dd4d` (pinned by
  `tests/test_engine_plugin.py`)
- **Form:** byte-for-byte; no local additions.
- **Why it is vendored:** `capsulectl engine check` (`capsule_engine/rulesets/`) validates what it
  reads and what it prints against them. A finding carries only the members the result schema
  lists, because capsulectl refuses any other. Re-vendor the result schema from a capsulectl
  release that ships capsule-cli#257 to print `limit_source` and `profile_digest`; a release
  before it refuses both.
