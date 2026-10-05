# Vendored schemas

These files are copies of schemas that another project owns. They are not
defined here. A change to an owning schema is drift that must be re-vendored
here, never re-derived by hand.

## `evidence-result-v0.json`

- **Owner:** agent-action-capsule, `schemas/evidence-result-v0.json` (spec:
  `spec/evidence-result-v0.md`)
- **Copied from commit:** `8fdf5b9bfdce069ac83568b6e1ad71f72e7904e6` (agent-action-capsule
  branch `feat/result-coverage-report`: main at `6e7deff`, with the EXAMPLE-ORG placeholder, plus
  the PROPOSED `coverage_report` section, spec section 7.1, with lower-case epistemic types).
  Re-copy from agent-action-capsule's main once that branch merges.
- **Copied on:** 2026-10-01
- **Form:** byte-for-byte; the file carries no local additions. Check with
  `git -C <agent-action-capsule checkout> cat-file blob 8fdf5b9bfdce069ac83568b6e1ad71f72e7904e6:schemas/evidence-result-v0.json | cmp - schemas/vendor/evidence-result-v0.json`.
- **Why it is vendored:** the agent-action-capsule release this repository pins
  does not ship the schema as an importable resource. Drop this copy and import
  the schema from agent-action-capsule once it does (see
  `capsule_engine/report/result.py`'s module docstring).

## `epistemic-types.json`

A transcription of the closed `epistemic_type` value set owned by the
published Internet-Draft `draft-mih-agent-evidence-layer-00`, section 4.1
"Epistemic Type" (source: agent-action-capsule
`spec/draft-mih-agent-evidence-layer-00.md`). Its provenance is recorded inside the file (the
`$comment` and `source` fields), because no machine-readable copy exists
upstream to copy byte-for-byte.
