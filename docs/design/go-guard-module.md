# Design note: the Go guard moves into this repo as a `go/` module

Status: v1, draft for review, 2026-10-22. Ethan's agent (session engine-go-210). Reviewers: Codex, a
`claude -p --permission-mode plan` stand-in for Kimi (Kimi is out of quota), and Sherlock2 (the note
moves ownership of code between ASG repositories).

Issue: capsule-engine#210. How ack: capsule-engine#210 issuecomment-6352734012.

Code read: capsule-engine a489e49, actionstate-muse 8c9acf3, company-internal-all 63489eb.

## 0. Problem, and what this note does not do

The guard has two implementations today. The Python one is here, and it is the reference. The Go
one sits in the private product repo, at `actionstate-muse/internal/guard`, with the helpers
`internal/pack` and `internal/canon` and the embedded pack copies in `packs/`. That is about
12k lines of code, 308 tests and 15 MB of vectors. Every wicket change therefore ships twice: it
lands here first, and then it is ported by hand into Muse and checked there by parity tests that
replay decisions this engine wrote at some pinned commit. The seller 0.1.5 batch needed eight
such ports (actionstate-muse #242-#247, #231, #241). Bugs also land on one side only
(actionstate-muse#215; capsule-engine#200 / actionstate-muse#228).

The goal is that a wicket lands once, in one PR here, with its Python and Go code together. One
set of vectors decides both, and CI fails the PR if the two disagree.

This note does not:

- change any decision, reason or evidence. The move is port-only, and later cleanups go in their
  own PRs;
- unify the Python and Go code, generate one from the other, or drop either. Python stays the
  reference, and Go stays the implementation that capsulectl plugins compile in;
- change a pack, a wicket definition or a digest;
- touch actionstate-muse before rc21 (Muse 0.0.15) is cut.

## 1. Boundary: what moves and what stays

The licensing doc (company-internal-all `business-model/licensing-and-packaging-2026-08-12.md`)
gives capsule-engine the "guard (closed checks: the check ENGINE and the state-reading checks)".
J.2 puts pack policy modules beside their packs here, and J.6/J.7 list the guard among this
repo's contents. Muse's README sets the test: a second platform should need only a second plugin
and a second normalizer. Anything that a second platform's plugin would need unchanged is
therefore generic, and it moves. Anything that names Muse or exists for one Muse verb stays.

| Muse path | Where it goes | Why |
|---|---|---|
| `internal/canon` (JCS values, `json.go`) | `go/canon` | a canonical JSON port, used by every digest |
| `internal/pack` (loader, digests, fold definitions, pack source, role selection) | `go/pack` | the pack runtime, the Go half of `capsule_engine/packs` and `guards/wickets/loader.py` |
| `packs/` (embedded byte-for-byte pack copies, `engine/action_taxonomy.json`) | `go/packs`, generated (§3) | copies of this repo's own catalog |
| `internal/guard` engine, gate, checks, fold, record, history, taxonomy and every wicket check (dedupe, caps and operator limits, seen_before, price_floor, offer_expiry, release, single_commitment, promise and statements, task_authority, change checks, party_role, deal_id, commitment) | `go/guard` | a port of `guards/engine.py`, `guards/checks/*` and `folds/engine.py`; each file's header already names its Python source |
| `internal/guard` `check_input.go` (external-check-input/v0), `sale_bundle.go`, `sale_history.go`, `standalone_report.go`, `bundle_replay.go`, `seller_records.go` | `go/guard` | they read neutral formats (agent-action-capsule and capsulectl), mirroring `report/replay.py`, `report/live_history.py` and `report/sale_bundle.py` |
| `internal/guard` `report.go`, the per-rule verdict report | `go/guard`, with the schema id left to the caller (§4) | it explains a guard decision rule by rule, which any plugin needs. Only the `actionstate-check/v1` name and its doc are Muse's |
| `internal/guard` tests and `testdata/` (44 vector sets and the 47 Python generators beside them) | `go/guard` and `go/guard/testdata` as they are, then into shared vectors (§5) | the parity evidence goes where the code goes |
| `internal/plugin` (including `limits_say.go`, approval cards, narration), `internal/limits`, `internal/rules`, `internal/rulesresult`, `internal/presentation`, `internal/checkup`, `internal/vocabgate`, the normalizer and the disposition resolver | stay in Muse | Muse verbs, Muse wording, Muse state |

Two Muse-shaped details inside moving files are changed at the move, as API with no behavior
change:

- `standalone_report.go`'s `copyAudiences` (`counterparty`, `keep`: "the audiences Muse reads")
  becomes a parameter of the call that reads a copy's report. Muse passes the same two values,
  so Muse reads exactly what it reads today.
- `report.go`'s `CheckSchema = "actionstate-check/v1"` leaves the module. The struct keeps its
  `schema` member, which the caller fills in, and Muse sets it to `actionstate-check/v1`. The
  JSON field order, the verdicts and the reason strings do not change, so Muse's
  `check_json_test.go` bytes do not change.

The comment text is rewritten wherever it names Muse or a Muse verb, because this repo's
neutrality gate scans for reserved vocabulary. No identifier or string literal that reaches
output changes. One string stays as it is in the move: `NotYetRun`, "not yet run by this
plugin". It is engine output today, and changing it would change Muse's output. §6 keeps the
mechanism, and a later wording PR may reword it.

How the split was checked: every `internal/guard` file was read for imports and for the names
"Muse", "actionstate" and "plugin" at 8c9acf3. The non-test importers of the four packages
outside them are `internal/plugin`, `limits`, `rules`, `rulesresult`, `presentation`, `checkup`
and `vocabgate`. All of these stay, and after the switch-over they import the module.

## 2. Module path and layout

The model is agent-action-capsule, checkpointed-local-log and capsule-emit: one Go module in
`go/`, tagged `go/vX.Y.Z`, beside the Python package.

```
go/
  go.mod                module github.com/action-state-group/capsule-engine/go   (go 1.27.0)
  README.md
  canon/                from internal/canon
  pack/                 from internal/pack
  packs/                generated pack tree + embed.go (§3)
  guard/                from internal/guard
  guard/testdata/       the moved vector sets, as they are
  internal/gen/         `go run ./internal/gen` writes packs/ from the catalog
conformance-vectors/
  guard/v0/             shared vectors (§5): read by tests/ and by go/guard
.github/workflows/conformance.yml
```

All four packages are exported (not `internal/`), because Muse imports each of them outside the
guard. Their dependencies are the standard library and `gopkg.in/yaml.v3` (Muse's `jsonschema`
and `evidencebook` requires are not imported by these packages). The module is public, so Muse
needs no `GOPRIVATE` entry for it.

The Python package (`capsule_engine/`) and its wheel do not change. Nothing under `go/` or
`conformance-vectors/` ships in the wheel.

## 3. Packs inside the module

Go `embed` cannot read outside the module directory, so the module cannot embed
`capsule_engine/packs/catalog` directly. Instead, `go/packs/` is a generated tree with Muse's
current layout: one `<pack>-<version>/` directory per pack version (pack.yaml, the wicket and
fold definitions it cites, and PROVENANCE.json), plus `engine/action_taxonomy.json`. It is
written by `go run ./internal/gen`, with the catalog and `guards/wickets/catalog_defs` as the
only sources:

- The **current** version of each pack the Go guard serves (everyday, seller) is written from the
  catalog at HEAD.
- **Frozen** versions (everyday 0.3.4-0.3.7, seller 0.1.0-0.1.4, plus whatever is current when
  the next version lands) stay as committed bytes. Each one's PROVENANCE.json names the commit
  its pack.yaml came from, as Muse's sidecars do today. They are moved over byte for byte from
  Muse at the move.
- CI (§7) regenerates the tree and fails on any difference. It also checks that every
  definition file under `go/packs` is byte-identical to its namesake in `catalog_defs`, and that
  every engine digest in a PROVENANCE.json equals what the Python engine computes for that
  directory today.

The result is that a new pack version needs no hand vendoring. The PR that cuts it runs the
generator, and the pack is in the next Go tag.

## 4. API

The move keeps Muse's exported API (`NewEngine`, `Check*`, `DecodeAction`, `DecodeCheckInput`,
`ReadLedger`, `RecordFor`, `Report`, `ReadSaleBundle`, `pack.*`, `canon.*`, ...). Only the import
path changes, plus the two parameters in §1. There is no rename and no new abstraction. The
module stays on v0, so a later minor version may still change the API. A cleanup of the
surface, such as unexporting what no caller uses, is a separate PR made after Muse has switched,
when the full caller list is known.

## 5. Shared conformance vectors

Today the Muse vectors are Python decisions frozen at many engine commits, written by 47
different generators in 44 formats, and only Go reads them. Here, both sides must read one set.

**Format (`conformance-vectors/guard/v0/<set>/<case>.json`).** A case is built on the input that
both implementations already accept: an external-check-input/v0 envelope (Go
`DecodeCheckInput`; Python `report.replay.action_for_check_input` with
`report.live_history.history_ledger`).

```json
{
  "case": "seller-015/s04-approved-claim",
  "pack": "go/packs/seller-0.1.5",
  "pack_digest": "<engine definition digest>",
  "input": { "...": "one external-check-input/v0 envelope" },
  "expected": {
    "outcome": "allow | deny | escalate",
    "constraints": [
      {"id": "promise_never", "result": "pass", "method": "...", "evidence_digest": "<JCS sha256>"}
    ]
  }
}
```

"Identical decisions and reasons" means the outcome, the ordered constraint ids, each result and
method, and the JCS-SHA256 of each constraint's evidence, which holds its reason, are all equal.
A case may also carry the evidence itself, for readable diffs. The digest is what is compared.

**Who writes `expected`.** Only Python writes it: `python -m tools.conformance_vectors --write`
runs the Python engine over each case's pack and input. Go never writes vectors. CI runs the
writer in check mode and the Go runner over the same files, so there are three possible
failures:

- Python at HEAD no longer reproduces a committed case: a Python behavior change, which must
  come with a re-written vector in the same PR;
- Go disagrees with a committed case: divergence;
- the pack digest is wrong: pack drift.

**Seed.** Built from what exists, with no new behavior:

1. The engine's own fixtures: `packs/catalog/{everyday,seller}/fixtures` (gold_labels,
   decision_table, mini_ledger), which Muse already vendors as `everyday-fixtures`.
2. Every Muse vector set whose input already is, or converts mechanically into, an
   external-check-input/v0 envelope. That covers the `*-vectors` sets the plugin's check path
   replays: live history, sale replay, the seller s1/s2/0.1.1-0.1.4 sets, the 0.1.5 sets and the
   real-bundle sets.
3. The sets that test a unit below the envelope (fold totals, equivalence-key digests, scalar
   ports, `canon`) stay in `go/guard/testdata` as Go tests with their generators. They join v0
   only if a Python entry point at that level is worth exposing, and otherwise they stay
   Go-only unit vectors. The implementation PR lists each set and where it went.

**Old engine commits.** Many Muse vectors were written at an older engine commit. The converter
first reruns them through the Python engine at HEAD. Where HEAD writes the same `expected`, the
case joins v0. Where HEAD differs, that is a finding, not a skip: the case goes into
`conformance-vectors/guard/v0/KNOWN-DIFFERENCES.md` with the case id, the commit it was written
at, the field that differs and the PR or issue that explains it. Muse already pins one such
difference (`TestKnownDifferenceTheEngineCountsAnIncompleteHistory`). The CI job reads this list
and fails if a listed case stops differing, as the Muse test does today, so the list cannot go
stale. If any listed difference is a Python regression on a frozen pack, it is filed as a bug.

## 6. Which definitions the Go guard implements

`portedDefinitions` (engine.go) is the exact (id, digest) list the Go code implements, and a pack
citing anything else is refused. That stays. Two CI checks are added on top:

- each current pack the Go guard serves (everyday, seller) has no rule in `NotYetRunRules`. A PR
  that adds a wicket version to such a pack without the Go code fails;
- every (id, digest) in `portedDefinitions` is either in `catalog_defs` or listed in
  `retired.py`.

## 7. Cross-language CI

There is one new workflow, `conformance.yml`. It runs on every pull request and on every push to
main, with no path filter, because a Python-only change can diverge from Go just as a Go change
can. Its jobs:

1. `go`: `go vet ./...`, `gofmt -l` (empty), and `go test ./...` in `go/` with `GOWORK=off`,
   which includes the moved Muse parity tests and the v0 runner.
2. `vectors`: install the Python package, `python -m tools.conformance_vectors --check` (Python
   reproduces every committed `expected`), then the Go v0 runner over the same files. The job
   fails on any case where the two differ, and it prints both sides' outcome, constraint table
   and evidence.
3. `packs`: `go run ./internal/gen --check`, the byte-identity check against `catalog_defs`, the
   engine-digest check from §3, and the two checks from §6.

The existing `ci`, `dco`, `neutrality` and `vendor-drift` jobs stay as they are. The neutrality
gate covers `go/` automatically. Branch protection should require `conformance`. An admin sets
that.

## 8. Versioning

- **Tags.** `go/vX.Y.Z`, pushed by an admin, as in agent-action-capsule's RELEASING.md. A bad
  tag is withdrawn only with a `retract` in a later version. The first tag is `go/v0.1.0`, the
  module as moved.
- **What a tag pins.** A tag's commit fixes the embedded pack set (each `go/packs/*/PROVENANCE.json`
  names the pack id and engine digest) and the `portedDefinitions` list, which is (wicket id,
  digest). `go/CHANGELOG.md` lists, per tag, the pack ids and digests added and the definition
  pairs added. Those lists come from the diff of those two files, never from memory.
- **Bumps, while on v0.** A minor bump for a new pack version, a new ported definition or an API
  change. A patch for a fix that leaves every v0 vector's `expected` unchanged. A change in the
  verdicts of an already-ported (id, digest) is never a version bump. It is a new definition
  version with a new digest, or a retirement in `retired.py`, as for Python today.
- **Python.** The wheel is versioned on its own. A Go tag and a Python build from the same commit
  agree on every v0 vector, because CI holds that on every merge to main. That is the whole
  mapping. There is no shared version number.
- **Muse.** Muse pins one `go/vX.Y.Z` in its go.mod. A Muse release names that tag and the pack
  digests it ships, where today it names the vendored PROVENANCE commits.

## 9. Muse switch-over

After rc21 ships, and after `go/v0.1.0` is tagged from a commit whose `go/` matches Muse's guard
at the rc21 commit (the sequencing in §10 makes sure of this), one actionstate-muse PR:

- adds `require github.com/action-state-group/capsule-engine/go v0.1.0`;
- rewrites the imports of `internal/{guard,pack,canon}` and `packs` to the module paths;
- deletes `internal/guard`, `internal/pack`, `internal/canon` and `packs/` (code, tests and
  testdata, which now live here), and the vendoring notes that described them;
- passes the two §1 parameters (`copyAudiences`; schema id `actionstate-check/v1`) from
  `internal/plugin`;
- keeps every test outside the deleted directories passing unchanged, including
  `internal/plugin`'s check, sale, limits, report and parity tests, `internal/rules`,
  `rulesresult`, `presentation` and `checkup`, and the e2e scripts in `scripts/`.

The PR description includes a mechanical diff: Muse's deleted directories at its parent commit
against the module at `go/v0.1.0`. Import paths are normalized, and the only remaining
differences are the documented ones (the §1 parameters and the comment rewording). That diff is
the evidence of "no behavior change".

After the switch, a wicket change is one PR here (Python + Go + vectors), then a tag, then a
one-line pin bump in Muse.

## 10. Sequencing

1. This note converges (Codex, the `claude -p` stand-in, Sherlock2).
2. **PR A, the move** (here). It adds `go/` from Muse at a named commit, with every test and
   testdata set as is, plus the §1 edits, `go/packs` with its generator, and the `go` and
   `packs` CI jobs. Its gate: the moved Go tests pass. A checksum list shows each moved testdata
   file is byte-identical to Muse's at the source commit.
3. **PR B, shared vectors** (here). It adds `conformance-vectors/guard/v0`, the converter, the
   Python writer and checker, the Go v0 runner, `KNOWN-DIFFERENCES.md` and the `vectors` CI job.
4. **Catch-up.** Muse guard commits that land between PR A's source commit and the rc21 cut
   (rc21 work in flight: actionstate-muse #265, #248, #261, #260) are ported here in one
   catch-up PR, with the same checksum and test evidence, before the tag. From PR A's merge
   until the switch-over, any guard change made in Muse has to be made here too. The
   coordinator routes such changes. The window is kept short: the catch-up and the switch-over
   follow the rc21 cut directly.
5. **Tag `go/v0.1.0`** (admin).
6. **Muse switch-over PR** (§9), after the coordinator confirms rc21 is cut.
7. Later, each in its own PR: move the remaining unit vector sets where useful, clean up the API,
   and reword `NotYetRun`.

Each PR merges on green checks on its final head, Codex and the `claude -p` stand-in converged,
and a review-record comment. PRs A and B are port-only and add no public text beyond the module
README, so they do not need Sherlock2, unless a reviewer finds a product rule or public wording
in them.

## 11. One-way doors

- **Publication.** The generic guard code becomes public Apache-2.0 in this repo. Ethan ruled on
  2026-10-22 that this needs no separate Steven flag.
- **Module path** `github.com/action-state-group/capsule-engine/go` and the `go/v` tag scheme.
  The path is fixed once tagged. The API is v0 and can still change.
- **No contract changes.** No public format, pack digest, schema or spec text changes.

## 12. Open questions for reviewers

1. Is `report.go`'s per-rule verdict report generic, so that it moves (this note's position), or
   Muse's? If it is Muse's, it can only stay there by exporting about 30 engine internals that it
   reads (`outOfScopeReason` reads every wicket config). That is the larger and riskier change.
2. Should the unit-level vector sets (§5 item 3) get Python entry points so that they join v0
   now, or stay Go-only until a change touches them?
3. Is `KNOWN-DIFFERENCES.md`, which fails when a listed case stops differing, the right holding
   pen for old-commit vectors that HEAD no longer reproduces? The alternative is refusing PR B
   until every one is resolved.
