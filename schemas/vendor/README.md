# Vendored schemas

These files are copies of schemas that another project owns. They are not
defined here. A change to an owning schema is drift that must be re-vendored
here, never re-derived by hand.

## `evidence-result-v0.json`

- **Owner:** agent-action-capsule, `schemas/evidence-result-v0.json` (spec:
  `spec/evidence-result-v0.md`)
- **Copied from commit:** `24aaa2fae69ea0fbd4d0122d5a3ebe5971968beb`
- **Copied on:** 2026-10-01
- **Form:** byte-for-byte; the file carries no local additions. Check with
  `git -C <agent-action-capsule checkout> cat-file blob 24aaa2fae69ea0fbd4d0122d5a3ebe5971968beb:schemas/evidence-result-v0.json | cmp - schemas/vendor/evidence-result-v0.json`.
- **Why it is vendored:** the agent-action-capsule release this repository pins
  does not ship the schema as an importable resource. Drop this copy and import
  the schema from agent-action-capsule once it does (see
  `capsule_engine/report/result.py`'s module docstring).

## `epistemic-types.json`

A transcription of the closed `epistemic_type` value set owned by
EvidenceBook's record header. Its provenance is recorded inside the file (the
`$comment` and `source` fields), because no machine-readable copy exists
upstream to copy byte-for-byte.
