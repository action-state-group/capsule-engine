# Vendored schemas

These files are copies of schemas that another project owns. They are not
defined here. A change to an owning schema is drift that must be re-vendored
here, never re-derived by hand.

## `evidence-result-v0.json`

- **Owner:** agent-action-capsule, `schemas/evidence-result-v0.json` (spec:
  `spec/evidence-result-v0.md`)
- **Copied from commit:** `b109a9e4b29703ed16637e994ce42eae0bcfc212` (agent-action-capsule
  branch `feat/result-coverage-report`, PR #157, unmerged: main at `7662a003`, which admits
  `x-` extension members on the document and on a claim, spec section 8a, plus the PROPOSED
  `coverage_report` section, spec section 7.1, with lower-case epistemic types).
  Feature-branch copy; re-copy from agent-action-capsule's main once #157 merges.
- **Copied on:** 2026-10-16
- **sha256:** `cf46d273e0220a6a6e9cfa4e41b331b6a6a155d053ca81cc33f3c004dfa734a9`
  (pinned by `tests/test_report_result_extension_slot.py`)
- **Form:** byte-for-byte; the file carries no local additions. Check with
  `git -C <agent-action-capsule checkout> cat-file blob b109a9e4b29703ed16637e994ce42eae0bcfc212:schemas/evidence-result-v0.json | cmp - capsule_engine/schemas/vendor/evidence-result-v0.json`.
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
