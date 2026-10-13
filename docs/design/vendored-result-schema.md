# The vendored Evidence Result schema and its unmerged section

**Status: proposal for the maintainer to read and decide on. It changes no
code, no schema and no test.**

Measured on 2026-10-13 at capsule-engine
`fd43d4329ea31d69e9bdd0a79f23f013c3615219` and agent-action-capsule `main`
at `a726b9f5442fe507372ec3205bfbcb079b9c15e9`.

## What is the case today

capsule-engine builds Evidence Results in `capsule_engine/report/result.py`
and validates every one against
`capsule_engine/schemas/vendor/evidence-result-v0.json` (`SCHEMA_PATH`,
`validate_against_schema`). The schema itself is owned by
agent-action-capsule (`schemas/evidence-result-v0.json`, specified in
`spec/evidence-result-v0.md`).

| | sha256 | source |
|---|---|---|
| Vendored copy in this repo | `de727b05a9a380b24b14e2330c6c11b2bcf15483eb737758c5b1265a4d080ab5` | agent-action-capsule `8fdf5b9bfdce069ac83568b6e1ad71f72e7904e6` |
| Owner's `main` | `fe75c71940338b8795641e404e6ae54d63a90c39498d75f2996a70cd6068f3c8` | agent-action-capsule `a726b9f`, and the tagged release `v0.7.0` |

The vendored copy is byte-identical to the file at `8fdf5b9`, as
`capsule_engine/schemas/vendor/README.md` says. But `8fdf5b9` is the head of
the branch `feat/result-coverage-report`, which is agent-action-capsule pull
request #157. That pull request is still open (last updated 2026-10-01, and
it no longer merges cleanly), and `8fdf5b9` is not on agent-action-capsule's
`main`. Someone who wants the exact schema a capsule-engine Result was
validated against cannot get it from a release; they have to find an
unmerged branch.

How the two files differ, compared structurally:

- The vendored copy adds one optional property to the top-level Result,
  `coverage_report`, and eleven definitions that only it uses
  (`AssuranceMode`, `Connector`, `CoverageGap`, `CoverageReport`,
  `CoverageStatus`, `CoverageSummary`, `EpistemicType`, `Independence`,
  `Remedy`, `RequirementCoverage`, `SourceCoverage`), all marked PROPOSED.
- Every other definition has the same structure in both files. The
  remaining differences are description text only.
- The owner's Result object sets `additionalProperties: false`. So a Result
  that carries `coverage_report` is valid against the vendored copy and
  **invalid** against the owner's published schema. A Result without it is
  checked against the same structure either way.

The closed vocabularies a reader relies on most (`tier`: `recomputed`,
`judged`; `grade`: `self-attested`, `witnessed`, `countersigned`) are the
same in both.

## Option A: repin to the owner's main once the section merges

Keep the vendored copy as it is until pull request #157 merges, then copy
the schema again from agent-action-capsule's `main` (or the first tag that
contains it), byte for byte, and update the pin in the vendor README. A
variant is to re-copy from `v0.7.0` now and stop emitting `coverage_report`
until the section merges.

- **After the merge:** `coverage_report` is part of the published schema.
  Claims keep the grade their own evidence earns, and the coverage section
  can be checked by anyone against a released schema.
- **Until the merge:** nothing changes, and nothing tells a reader that the
  validating schema is unpublished. Anything stated in `coverage_report`
  rests on a proposal, not on the specification. If #157 changes before it
  merges, Results emitted in between were validated against a shape that
  never shipped.
- **Cost:** none now. It depends on a merge that is not scheduled.

## Option B: label the vendored copy non-normative, and guard the core in CI

1. State in the vendor README and in `result.py`'s docstring that the
   vendored copy is a **non-normative** superset of the published schema:
   the published schema plus the proposed `coverage_report` section.
2. Add a CI test that takes the Results the test suite already builds,
   removes `coverage_report`, and validates what is left against the
   owner's published schema. That schema would be pinned by sha256
   (`fe75c719...`, which is `main` at `a726b9f` and tag `v0.7.0`). Any
   drift in the shared core then fails CI instead of passing silently, and
   moving that pin becomes a deliberate, reviewed change.

- **Core of a Result:** checked against the published schema on every CI
  run. Its claims carry the grade their evidence earns, exactly as under A
  after the merge.
- **`coverage_report`:** explicitly the engine's own statement. A
  conclusion drawn only from it (for example a requirement's independence
  count or its listed gaps) should be read as `self-attested` by the engine,
  and must not be presented as conforming to `evidence-result-v0`.
- **Cost:** a small README and docstring change and one test. B is
  compatible with A: when #157 merges, repin as in A, drop the label, and
  let the guard validate the whole document.

## What a statement built on the unmerged section may carry

| | Before #157 merges | After #157 merges |
|---|---|---|
| A | Not stated anywhere; in practice no more than `self-attested`, and not checkable against a release. | The claim's own grade; the section is normative. |
| B | Labelled: `self-attested` by the engine, outside the published schema. The core is checked in CI. | Same as A. |

A grade never rises because a schema accepted a document. Schema validity
says the shape is right; the grade says who stands behind the evidence.
What the unmerged section takes away is the ability of an outside reader to
check the shape against something published.

## Recommendation

Do B now, and A when pull request #157 merges. B costs one small change,
tells readers the truth today, and turns silent drift in the shared core
into a CI failure. A on its own leaves the gap open for as long as #157
stays open, with no date.

This note does not implement B.
