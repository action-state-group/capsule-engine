# Design note: the Go guard moves into this repo as a `go/` module

Status: v3, for review, 2026-10-22. Ethan's agent (session engine-go-210). Reviewers: Codex, a
`claude -p --permission-mode plan` stand-in for Kimi (Kimi is out of quota), and Sherlock2 (this
note moves code ownership between ASG repositories).

Issue: capsule-engine#210. How ack: capsule-engine#210 issuecomment-6352734012.

Code read: capsule-engine a489e49, actionstate-muse 8c9acf3 and 783fb94, company-internal-all
63489eb.

**v2.** This version takes round 1 from Codex, the `claude -p` stand-in and Sherlock2
(buzz://message?channel=0d760ea3-529d-4dec-b309-24184967f8e0&id=0480f78e31a7e43bde7b6331cd6ceccac5462256fabb510b3433c278eb27a9b9).
What changed:

- **Shared vectors (§5).** Cases now come in kinds, with one runner per kind on each side, and
  each kind replays the input its existing tests replay. The reason string and the sealed
  constraint fields are compared too. Seeding re-runs the existing generators at HEAD, and the
  frozen old-commit sets stay as Go tests. Known differences pin both sides' values, and a
  coverage gate is added.
- **Publication (§11, §12).** Publication cites Steven's 2026-08-22 ruling. The moved testdata
  gets a public-readiness gate and a neutrality rule (§12).
- **Tests and packs (§9, §1, §3).** §9 names the Muse tests and scripts that read moved paths.
  The role-to-pack map and the generated pack registry are now caller-side (§1, §3), and the
  fold and taxonomy sources are named (§3).
- **CI and versioning (§6, §7, §8).** Retirement is enforced (§6). The CI job names are fixed
  (§7). §8 allows a Go fix toward the Python reference.
- **Sequencing and review (§10).** §10 names the source commit, carries the double-maintenance
  rule into Muse, and asks Sherlock2 to review PR A.
- **Local-only boundary (§13).** New: the AGENTS.md local-only boundary for Go.

**v3.** This version takes round 2 from Codex and the `claude -p` stand-in. What changed:

- **Coverage gate (§7).** The gate has an exemption list with a reason for each entry, starting
  with `verify_before_dispatch/1.0.0`, and a Go error can be pinned.
- **Go runners (§2, §5).** The runners sit in `package guard`, so they read the unexported reason
  fields with no API change. Each kind's input keeps the side inputs its tests pass today
  (§5).
- **Pack defaults (§1, §3).** The un-versioned pack aliases and the built-in catalog default
  become caller-side, like `partyPacks`.
- **Switch-over (§9).** §9 adds the `Join`-built readers and makes the full test run the check.
- **Sets kept back (§12).** A set held back by §12 is resolved one by one before the
  switch-over, instead of falling back unconditionally.
- **vocabgate (§3, §9).** vocabgate walks the pinned module's `packs.All`, never a copy (Codex
  round 3, `claude -p` round 4).

## 0. Problem, and what this note does not do

The guard has two implementations today. The Python one is here, and it is the reference. The Go
one is in the private product repo, at `actionstate-muse/internal/guard`, with the helpers
`internal/pack` and `internal/canon` and the embedded pack copies in `packs/`. That is about 12k
lines of code, 308 tests and 15 MB of vectors.

So every wicket change ships twice. It lands here first, and is then ported by hand into Muse and
checked there by parity tests that replay decisions this engine wrote at some pinned commit. The
seller 0.1.5 batch needed eight such ports (actionstate-muse #242-#247, #231, #241). Bugs also
land on one side only (actionstate-muse#215; capsule-engine#200 / actionstate-muse#228). And the
two sides drift in between: after #209 (a489e49), Python's s04 reason reads "no order or approval
of yours covers this promise", while Muse at 8c9acf3 still read "no task authority order or
approval of yours covers this promise" until #265 ported it.

The goal is that a wicket lands once, in one PR here, with its Python and Go code together. One
set of vectors decides both, and CI fails the PR if the two disagree on any behavior the vectors
reach. §7's coverage gate makes sure they reach every ported definition.

This note does not:

- change any decision, reason or evidence. The move is port-only, and later cleanups go in their
  own PRs;
- unify the Python and Go code, generate one from the other, or drop either. Python stays the
  reference, and Go stays the implementation that capsulectl plugins compile in;
- change a pack, a wicket or fold definition, or a digest;
- touch actionstate-muse before rc21 (Muse 0.0.15) is cut.

## 1. Boundary: what moves and what stays

**The ruling.** The licensing doc (company-internal-all
`business-model/licensing-and-packaging-2026-08-12.md`) records Steven's ruling of 2026-08-22,
Amendment F.1.2: capsule-engine holds the "guard and closed checks", under Apache-2.0, whole.
"Closed checks" means closed-form checks, not closed source. J.2 puts pack policy modules beside
their packs here, and J.6/J.7 list the guard among this repo's contents.

**The test.** Muse's README says a second platform should need only a second plugin and a
second normalizer. So whatever a second platform's plugin would need unchanged is generic, and it
moves. Whatever names Muse, or exists for one Muse verb or one Muse release decision, stays.

| Muse path | Where it goes | Why |
|---|---|---|
| `internal/canon` (JCS values, `json.go`) | `go/canon` | a canonical JSON port, used by every digest |
| `internal/pack` (loader, digests, fold definitions, pack source) | `go/pack` | the pack runtime: the Go half of `capsule_engine/packs`, `guards/wickets/loader.py` and `folds/` |
| `internal/pack` `partyPacks` / `ForPartyRole` / `KnownPartyRole` (which pack version serves which party role), and `packs`' un-versioned aliases (`Everyday`, `EverydayDir`, `Seller`, ...) with `catalogFor`'s built-in catalog default | stay in Muse, as caller-supplied values | which pack a product serves, and which catalog a bare `--pack` resolves against, are that product's release decisions (Muse #248 switches them for rc21) |
| `packs/` (embedded byte-for-byte pack copies, `engine/action_taxonomy.json`) | `go/packs`, generated (§3) | copies of this repo's own catalog |
| `internal/guard`: the engine, gate, checks, fold, record, history, taxonomy and every wicket check (dedupe, caps and operator limits, seen_before, price_floor, offer_expiry, release, single_commitment, promise and statements, task_authority, change checks, party_role, deal_id, commitment) | `go/guard` | a port of `guards/engine.py`, `guards/checks/*` and `folds/engine.py`. Each file's header already names its Python source |
| `internal/guard`: `check_input.go` (external-check-input/v0), `sale_bundle.go`, `sale_history.go`, `standalone_report.go`, `bundle_replay.go`, `seller_records.go` | `go/guard` | they read neutral formats (agent-action-capsule and capsulectl), mirroring `report/replay.py`, `report/live_history.py` and `report/sale_bundle.py` |
| `internal/guard` `report.go`, the per-rule verdict report | `go/guard`, with the schema id left to the caller | it explains a guard decision rule by rule, which any plugin needs. Keeping it in Muse would mean exporting about 30 engine internals (`outOfScopeReason` reads every wicket config). Only the `actionstate-check/v1` name and its doc are Muse's |
| `internal/guard` tests and `testdata/` (44 vector sets, 47 Python generators) | `go/guard`, after the §12 gate | the parity evidence goes where the code goes. A set that fails §12 stays in Muse (§9) |
| `internal/plugin` (including `limits_say.go`, approval cards and narration), `internal/limits`, `internal/rules`, `internal/rulesresult`, `internal/presentation`, `internal/checkup`, `internal/vocabgate`, the normalizer and the disposition resolver | stay in Muse | Muse verbs, Muse wording, Muse state |

**Caller-side parameters.** Four Muse-shaped details inside the moving files become parameters
the caller passes. Muse passes today's values, so Muse's behavior does not change:

- `standalone_report.go`'s `copyAudiences` (`counterparty`, `keep`: "the audiences Muse reads")
  becomes a parameter of the call that reads a copy's report.
- `report.go`'s `CheckSchema = "actionstate-check/v1"` leaves the module. The struct keeps its
  `schema` member, which the caller fills, and Muse sets `actionstate-check/v1`. JSON field
  order, verdicts and reason strings are unchanged, so Muse's `check_json_test.go` bytes do not
  change.
- `partyPacks` becomes a caller-supplied role-to-loader map, wherever the guard resolves a role
  today (`action.go`, `party_role.go`). The module keeps the loaders (`pack.LoadSeller015`, ...)
  and has no default map. `UnknownPartyRoleError`'s text ("not one this plugin knows") moves to
  Muse with the map.
- `catalogFor` (`source.go`) resolves a source that holds only a pack.yaml against
  `packs.Everyday` / `packs.EverydayDir`, which is everyday 0.3.7 in Muse's hand-written
  `embed.go` today. In the module, `LoadFrom` / `LoadPath` take that fallback catalog as an
  argument. `go/packs` exports only versioned names (`Everyday037`, `Seller015`, ...), plus
  `All`, the whole tree as one `embed.FS`, and no un-versioned alias. Muse keeps its aliases in its own code and passes the same catalog, so a
  bare `--pack` resolves exactly as it does today.

**Comments, strings and the check.** Comment text that names Muse or a Muse verb is rewritten at
the move. No identifier or string literal that reaches output changes. One such string stays as
it is: `NotYetRun`, "not yet run by this plugin". It is engine output today, and §10 step 7 may
reword it later.

Every `internal/guard` and `internal/pack` file was read at 8c9acf3 for imports and for the names
"Muse", "actionstate" and "plugin". The non-test importers of the four packages, outside the
packages themselves, are `internal/plugin`, `limits`, `rules`, `rulesresult`, `presentation`,
`checkup` and `vocabgate`. All of them stay, and after the switch-over they import the module.

## 2. Module path and layout

The model is agent-action-capsule, checkpointed-local-log and capsule-emit: one Go module in
`go/`, tagged `go/vX.Y.Z`, beside the Python package.

```
go/
  go.mod                module github.com/action-state-group/capsule-engine/go   (go 1.27.0)
  README.md
  CHANGELOG.md
  canon/                from internal/canon
  pack/                 from internal/pack, without partyPacks
  packs/                generated pack tree, embed.go and registry (§3)
  guard/                from internal/guard
  guard/testdata/       the moved vector sets that pass §12
  internal/gen/         `go run ./internal/gen` writes packs/ from the catalog
conformance-vectors/
  guard/v0/             shared vectors (§5), read by tests/ and by go/guard's conformance_test.go
.github/workflows/conformance.yml
```

All four of `canon`, `pack`, `packs` and `guard` are exported, not `internal/`, because Muse
imports each of them outside the guard. Their only non-standard dependency is
`gopkg.in/yaml.v3`. Muse's other requires (`jsonschema`, `evidencebook`) are not imported by
these packages. The module is public, so Muse needs no `GOPRIVATE` entry for it.

One moved test reads `../../packs/...` today. In `go/guard` that resolves outside the module, so
PR A rewrites those paths to `../packs/...`. That is the only test edit PR A makes besides the
§12 rewrites. The Python package and its wheel do not change: nothing under `go/` or
`conformance-vectors/` ships in the wheel.

## 3. Packs inside the module

Go `embed` cannot read outside the module directory, so the module cannot embed the catalog
directly. Instead, `go/packs/` is a generated tree in Muse's current layout: one
`<pack>-<version>/` directory per pack version (pack.yaml, the wicket and fold definitions it
cites, PROVENANCE.json), plus `engine/action_taxonomy.json`. `go run ./internal/gen` writes it,
together with `packs/embed.go` (one `embed.FS` per version, and `All` over the whole tree,
`engine/PROVENANCE.json` included) and the pack registry that `go/pack` reads (the `LoadXxx`
functions and `source.go`'s pack list, which are hand-written in Muse today).

The generator has four sources and nothing else:

- `capsule_engine/packs/catalog/<pack>/pack.yaml`;
- `capsule_engine/guards/wickets/catalog_defs/`;
- `capsule_engine/folds/catalog_defs/` (spend.weekly, counterparty.seen_before,
  disclosure.\*);
- `capsule_engine/guards/action_taxonomy*.json`.

Two kinds of pack version go into the tree:

- **The current version** of each pack the Go guard serves (everyday, seller) is written from
  the catalog at HEAD.
- **Frozen versions** (everyday 0.3.4-0.3.7, seller 0.1.0-0.1.4, plus whichever version is
  current when the next one lands) keep their committed pack.yaml. Each PROVENANCE.json names
  the commit the pack.yaml came from. Their definition files must still equal `catalog_defs`,
  which keeps every version. They move over byte for byte from Muse in PR A.

CI (§7, `packs` job) checks:

- the tree, `embed.go` and the registry regenerate with no difference;
- every wicket, fold and taxonomy file under `go/packs` is byte-identical to its source;
- every engine digest in a PROVENANCE.json equals what the Python engine computes for that
  directory today.

A new pack version therefore needs no hand vendoring. The PR that cuts it runs the generator, and
the pack is in the next Go tag. Choosing which version a product serves stays with the product
(§1).

## 4. API

The move keeps Muse's exported API (`NewEngine`, the `Check*` methods, `DecodeAction`,
`DecodeCheckInput`, `ReadLedger`, `RecordFor`, `Report`, `ReadSaleBundle`, `pack.*`, `canon.*`,
and so on). Only the import path changes, plus the three caller-side parameters in §1. There is
no rename and no new abstraction. The module stays on v0. A cleanup of the surface, such as
unexporting what no caller uses, is a separate PR made after Muse has switched, when the full
caller list is known.

## 5. Shared conformance vectors

Today the Muse vectors are Python decisions frozen at many engine commits, written by 47
generators in 44 formats, and only Go reads them. Here, both sides must read one set at HEAD.

**One runner per input kind.** The existing tests do not all decide from one shape of input, and
forcing them into one would change what they test. For example, `fixtures_test.go` feeds each
decision back into the next step's ledger, and `CheckSale` reads a replay ledger, the sale's full
history and the release window apart. So a case names its `kind`, and each kind has one public
entry point on each side. The entry point owns the engine configuration, so the runner hard-codes
none of it:

| kind | input | Go entry point | Python entry point (new, in the library, not in tests) |
|---|---|---|---|
| `check-input` | one external-check-input/v0 envelope | `DecodeCheckInput`, then `CheckWithHistory`, with `WithPartyRole` as Go does today | `capsule_engine.report.live_history.decide_check_input(pack_dir, envelope)` |
| `action-ledger` | steps, each an action with its optional task-authority and approval records, run in order, with each decision appended to the next step's ledger | `CheckWithTaskAuthority` / `CheckWithRecords`, as `fixtures_test.go` calls them today | `capsule_engine.guards.engine.decide_steps(pack_dir, steps)` |
| `sale-bundle` | a sale bundle, its `SaleVerification` (the sale, record and thread verification states the caller supplies) and the action replayed against it | `ReadSaleBundle(raw, v)`, then `CheckSale` / `CheckReplay` | `capsule_engine.report.sale_bundle.decide_sale_bundle(pack_dir, bundle, verification, action)` |

Each kind's input carries every side input its existing tests pass, so a converted case still
reaches the same authorization and verification-failure branches.

The new Python functions configure the engine the way the Go guard runs: observe mode, the
runnable checks, the ask sets, the record taxonomy, and the task-authority, floor and
representing openings. Today that setup lives in test helpers (`tests/test_live_history.py`
`_live_engine` / `_decide`). PR B moves it into these functions, and the existing tests then call
them. That is a test-only refactor with no behavior change.

**Known adapter differences.** Where the two adapters read an input differently, the case
records it, rather than either side changing in a port-only PR. For example, Go's
`DecodeCheckInput` reads the envelope's top-level `party_role` and marks a conflict with the
record as not evaluable (`party_role.go`), while Python's `action_for_check_input` does not read
it. Such cases go under the known-differences rule below.

**Case format (`conformance-vectors/guard/v0/<set>/<case>.json`).**

```json
{
  "case": "seller-015/s04-approved-claim",
  "kind": "check-input",
  "pack": "go/packs/seller-0.1.5",
  "pack_digest": "<engine definition digest>",
  "input": { "...": "the kind's input" },
  "expected": {
    "outcome": "allow | deny | escalate",
    "constraints": [
      {"id": "promise_never", "result": "pass", "method": "...", "reason": "<exact string>",
       "severity": "blocking", "blocking": true, "check_type": "policy",
       "evidence_digest": "<JCS sha256>"}
    ]
  }
}
```

**What "identical decisions and reasons" means.** The outcome, the ordered constraint ids, and
for each constraint: `result`, `method`, the exact `reason` string, the sealed `severity`,
`blocking` and `check_type`, and the JCS-SHA256 of its evidence. Python keeps `reason` apart from
`evidence` (`ConstraintOutcome`), and so does Go (`failNote` / `scopeNote`), so the reason is
compared on its own. A case may also carry the evidence itself, for readable diffs.

`failNote`, `scopeNote` and the other per-constraint fields are unexported. So the Go runner is a
test in `package guard` (`go/guard/conformance_test.go`), as `promise_11_test.go`'s `goReason`
is today, and the API does not change.

When a case's Go entry point refuses with an error instead of deciding, such as `ErrCitedMandate`
for an action that cites a mandate, the case pins the Go side as `{"error": "<sentinel name>"}`
in `KNOWN-DIFFERENCES.json` beside Python's decision.

**Python writes, Go checks.** Only Python writes `expected`:
`python -m tools.conformance_vectors --write` runs each case through its kind's Python entry
point. CI runs the writer in check mode and the Go runner over the same files. There are three
possible failures:

- Python at HEAD no longer reproduces a committed case: a Python behavior change, which must come
  with the re-written vector in the same PR;
- Go disagrees with a committed case: divergence;
- a pack digest is wrong: pack drift.

**Seed.** The seed adds no new behavior:

1. The engine's own fixtures, `packs/catalog/{everyday,seller}/fixtures` (gold_labels,
   decision_table, mini_ledger), as `action-ledger` cases.
2. The current packs' sets: every Muse set for everyday 0.3.8 or seller 0.1.5 whose generator
   still runs at HEAD. Each is re-run at HEAD through the new entry points and written in the v0
   format. The PR lists each set, its kind and its case count.
3. Everything else stays in `go/guard/testdata` as Go tests, frozen at the commit that wrote it:
   the sets for older frozen packs, and the unit-level sets (fold totals, equivalence-key
   digests, scalar ports, `canon`). Those tests keep guarding Go's behavior on old pinned packs,
   as they do in Muse today. A set joins v0 when a change touches its behavior, not before.

**Known differences.** `conformance-vectors/guard/v0/KNOWN-DIFFERENCES.json` lists every v0 case
where Go and Python differ today, for example the incomplete-history window that
`g3_engine_test.go`'s `TestKnownDifferenceTheEngineCountsAnIncompleteHistory` pins, and the
adapter differences above. Each entry gives the case id, the issue that tracks it, the fields
that differ, and **both** sides' expected values for those fields. CI fails if either side
changes from its pinned value, including a case that stops differing. A newly diverging case is
never added to the list in the PR that causes it. A difference on a frozen pack that turns out to
be a Python regression is filed as a bug in PR B.

## 6. Which definitions the Go guard implements

`portedDefinitions` (engine.go) is the exact (id, digest) list of wickets and folds that the Go
code implements, and a pack citing anything else is refused. That stays. CI (`packs` job) adds:

- `NewEngine` succeeds on every generated pack, and `NotYetRunRules` is empty for each current
  pack the Go guard serves. A pack that cites a definition version Go does not implement fails
  here, whether that version is new or merely unported;
- every (id, digest) in `portedDefinitions` is present in `guards/wickets/catalog_defs` or
  `folds/catalog_defs`, and none is in `retired.py`. Go refuses any pair not in
  `portedDefinitions`, so this keeps Go refusing every retired pair, as Python does. None of the
  retired pairs is in `portedDefinitions` at 8c9acf3.

## 7. Cross-language CI

One new workflow, `conformance.yml`, runs on every pull request and every push to main. It has
no path filter, because a Python-only change can diverge from Go as easily as a Go change can.

1. **`go`**: in `go/` with `GOWORK=off`, `go vet ./...`, `gofmt -l` (must be empty), and
   `go test ./...`. This runs the moved parity tests and the v0 runners.
2. **`vectors`**:
   - install the Python package and run `python -m tools.conformance_vectors --check`, so Python
     reproduces every committed `expected`;
   - run the Go v0 runners over the same files;
   - check `KNOWN-DIFFERENCES.json` pin by pin;
   - run the coverage gate. For each current pack, every wicket (id, digest) it cites has at
     least one v0 case whose result for it is not n/a, and every fold it cites is read by at
     least one case. The exceptions are listed in `conformance-vectors/guard/v0/COVERAGE-EXEMPT.json`,
     each with a reason and an issue. The first entry is `verify_before_dispatch/1.0.0`: Go has
     no capsule verifier, so `checkVerifyBeforeDispatch` always returns n/a and a cited mandate
     is refused with `ErrCitedMandate`. Its cases pin exactly that. An exemption is never
     added in the PR that would otherwise fail the gate, unless the pair is new and Go refuses it
     by design.

   A failure prints both sides' outcome, constraint table and evidence.
3. **`packs`**: `go run ./internal/gen --check`, plus the byte and digest checks of §3 and the
   checks of §6.
4. **`conformance`**: needs 1-3, and fails if any of them failed or was skipped. This is the one
   check branch protection requires. An admin sets that.

The coverage gate is what turns "a wicket lands once" into a rule. A PR that changes a check must
leave a v0 case that exercises it, or `vectors` fails. The existing `ci`, `dco`, `neutrality` and
`vendor-drift` jobs stay as they are, and `neutrality` covers `go/` and `conformance-vectors/`
automatically.

## 8. Versioning

- **Tags.** `go/vX.Y.Z`, pushed by an admin. PR A adds `RELEASING.md` here, with the same rule as
  agent-action-capsule's: creating `go/v*` tags is admin-only, and a bad tag is withdrawn only
  with a `retract` in a later version. An admin adds the tag ruleset. The first tag is
  `go/v0.1.0`, the module as moved.
- **What a tag pins.** A tag's commit fixes the embedded pack set (each
  `go/packs/*/PROVENANCE.json` names the pack id and engine digest) and `portedDefinitions`, the
  (id, digest) pairs. `go/CHANGELOG.md` lists, per tag, the packs and definition pairs added.
  These lists come from the diff of those files, never from memory.
- **Bumps, while on v0:**
  - minor: a new pack version, a new ported definition, or an API change;
  - patch: a fix that brings Go into agreement with the Python reference at the same (id, digest),
    such as a closed known difference or a found divergence. The CHANGELOG names the vectors that
    changed. A patch is also any other fix that leaves every v0 `expected` unchanged;
  - neither: a change of meaning for an already-ported (id, digest). That is a new definition
    version with a new digest, or a retirement in `retired.py`, on both sides, as for Python
    today.
- **Python.** The wheel is versioned on its own. A Go tag and a Python build from the same commit
  agree on every v0 vector, and CI holds that on every merge to main. That is the mapping; there
  is no shared version number.
- **Muse.** Muse pins one `go/vX.Y.Z` in its go.mod. A Muse release names that tag and the pack
  digests it serves, where today it names the vendored PROVENANCE commits.

## 9. Muse switch-over

After rc21 ships, and once `go/v0.1.0` is tagged from a commit whose `go/` matches Muse's guard at
the switch-over's parent commit (§10), one actionstate-muse PR does the following:

- adds `require github.com/action-state-group/capsule-engine/go v0.1.0`;
- rewrites the imports of `internal/{guard,pack,canon}` and `packs` to the module paths;
- moves `partyPacks` (with `ForPartyRole`, `KnownPartyRole` and `UnknownPartyRoleError`) and
  the un-versioned pack aliases into `internal/plugin`, and passes them, the fallback catalog,
  `copyAudiences` and the schema id `actionstate-check/v1`;
- deletes `internal/guard`, `internal/pack`, `internal/canon` and `packs/`. A vector set kept back
  by §12 has been resolved by then (§12);
- copies into Muse-owned testdata (`internal/plugin/testdata`, `internal/rules/testdata`) the
  fixtures that tests and scripts outside the deleted
  directories read by path, and edits only those paths. At 8c9acf3 these readers are:
  - 19 test files in `internal/plugin`, among them `check_test.go`, `sale_replay_test.go`,
    `fyi_act_test.go`, `g3_vectors_test.go` (which also writes in its update mode) and
    `pack_flag_test.go`;
  - in `internal/rules`: `helpers_test.go`, `platform_test.go`, `ready_test.go` and
    `rules27_test.go`. The last two build `../../packs/everyday-0.3.7` with `filepath.Join`;
  - `internal/vocabgate/page_claims_test.go`, which walks `<repo>/packs` to scan the pack text
    the pages actually show. It must not be given a frozen copy, which would go stale at the
    next pin bump. It walks the pinned module's `packs.All` instead, which holds every file
    of the tree it walks today, `engine/PROVENANCE.json` included. That is its one listed
    adaptation;
  - six e2e scripts: `buyer-exits-e2e.sh`, `clean-machine-test.sh`, `history-e2e.sh`,
    `receipt-e2e.sh`, `seller-sale-e2e.sh` and `tester-install-test.sh`.

  A grep can miss paths built from separate `Join` arguments. So the check is the full
  `go test ./...` and every e2e script, run after the deletion. The PR also records vocabgate's
  surface count before and after, and the two must be equal;
- adds a CI check that fails a PR touching any Go file under the old paths, naming capsule-engine
  `go/` as the owner;
- keeps every other test outside the deleted directories passing unchanged, including
  `internal/plugin`'s check, sale, limits, report and parity tests, `internal/rules`,
  `rulesresult`, `presentation` and `checkup`, and the e2e scripts.

**The evidence of "no behavior change".** The PR description includes a mechanical diff of Muse's
deleted directories at the parent commit against the module at `go/v0.1.0`. Import paths are
normalized, and what remains is only the documented differences: the §1 parameters, the §2 path
edits, the §12 rewrites and adaptations, the generated `embed.go` and registry, and the comment
rewording. Test
edits outside the deleted directories are fixture path edits, plus the listed vocabgate adaptation.

After the switch, a wicket change is one PR here (Python, Go and vectors), then a tag, then a
one-line pin bump in Muse.

## 10. Sequencing

1. This note converges (Codex, the `claude -p` stand-in and Sherlock2).
2. **PR A, the move** (here).
   - Source: Muse main at the latest commit whose `packs/` equals what the generator writes from
     this repo's HEAD. That is 783fb94 or later, after #265 re-vendored seller 0.1.5 at a489e49.
     PR A fails if the two disagree.
   - Contents: `go/` with every moved test and testdata set as is, plus the §1, §2 and §12 edits;
     `go/packs` and its generator; `RELEASING.md`; and the `go`, `packs` and `conformance` CI
     jobs.
   - Gates: the moved Go tests pass, a checksum list shows every moved fixture byte-identical to
     Muse's at the source commit (rewritten sidecars and generators are listed apart), and the
     §12 sweep passes.
   - Review: Codex, the `claude -p` stand-in and Sherlock2, because PR A adds a public README and
     public testdata.
3. **PR B, shared vectors** (here): the three Python entry points, `conformance-vectors/guard/v0`,
   the writer and checker, the Go runners, `KNOWN-DIFFERENCES.json`, the coverage gate and the
   `vectors` job.
4. **Catch-up.** Muse guard commits between PR A's source commit and the rc21 cut are ported here
   in one catch-up PR, with the same checksum and test evidence, before the tag. Of the rc21 work
   in flight, #265 and #261 are merged, and #261 does not touch the guard. Issue #248 (switching
   the default packs) edits `partyPacks` and the un-versioned aliases in Muse's `packs/embed.go`.
   Both stay in Muse under §1, so its port here is only whichever versioned pack it adds, written
   by the generator. Issue #260 does not touch the guard.

   From PR A's merge until the switch-over, a guard change made in Muse also lands here. The
   coordinator routes it, and the window stays short, because the catch-up and the switch-over
   follow the rc21 cut directly. Muse's own CI check from §9 lands with the switch-over, the
   first Muse PR allowed after the cut.
5. **Tag `go/v0.1.0`** (admin).
6. **Muse switch-over PR** (§9), once the coordinator confirms rc21 is cut.
7. **Later, each in its own PR:** move more frozen sets into v0 as changes touch them, clean up
   the API, and reword `NotYetRun`.

Each PR merges on green checks on its final head, with Codex and the `claude -p` stand-in
converged (plus Sherlock2 where step 2 says so, or wherever a PR adds public text or touches a
product rule) and a review-record comment.

## 11. One-way doors

- **Publication.** The generic guard code and its moved vectors become public Apache-2.0 here.
  Steven ruled this on 2026-08-22 (licensing doc, Amendment F.1.2), and the move needs no new ask.
  Ethan confirmed on 2026-10-22 that no separate flag is needed. What goes public is bounded by
  §12.
- **Module path and tags.** The module path `github.com/action-state-group/capsule-engine/go` and
  the `go/v` tag scheme are fixed once tagged. The API is v0 and can still change.
- **No contract changes.** No public format, pack digest, schema or spec text changes.

## 12. Public readiness of the moved testdata

Muse is private and this repo is public. The `real-*` sets were made by real capsulectl runs, and
their sidecars name internal workspace paths (`/Users/.../dev/asg/_work/...`,
`_work/priv-v0010-acceptance/...`), internal handoff files and internal ruling names. PR A
therefore gates each moved set, and lists the result set by set in the PR:

1. **No real personal or account data.** The deal data is synthetic or from a throwaway profile:
   no emails, phones, street addresses or personal names outside an allowlist (example.com,
   "Example Shop", the demo fixtures), and no real order, payment or listing ids. Local-only
   values (`deal_id`, `item_ref`, `payee-fp:hmac-sha256-profile-key:` targets) are present only
   in synthetic sets.
2. **Sidecars, READMEs and generators rewritten** to drop internal paths, lane and agent names,
   handoff file names and ruling ids. Commits and hashes stay. These files are listed apart from
   the byte-identical fixtures.
3. **`action-state-ops/_ops/security_sweep.py`** with the terms list, run over `go/` and
   `conformance-vectors/`, plus the repo's `neutrality` job.

A sealed fixture byte (one a decision reads, such as a capsule's `developer` member) cannot be
rewritten without changing digests and expected decisions. A set that fails 1 or 3 inside sealed
bytes does not move in PR A. Before the switch-over, each such set is resolved one by one, in
one of two ways, and PR A's list records which:

- **regenerated synthetically** in its own PR here, and then moved; or
- **kept in Muse** as an external test package (`internal/guardparity`) that runs against the
  imported module. A test kept this way must reach the module only through exported API. Those
  tests read unexported members today (for example `real_seller_test.go` reads
  `a.statesNoAct`, and the promise tests read `c.failsClosed` and `c.failNote`), so the
  adaptation is listed per test, and it changes assertions only by reading the same values
  through `Report` or the decision's exported fields.

A set that fits neither blocks the switch-over until it is regenerated.

## 13. The local-only payload boundary, for Go

AGENTS.md describes the share boundary for Python export paths. PR A adds a Go paragraph, and this
note holds to it:

- The module seals no decision capsule and exports none. It decides, and the caller seals.
  `RecordFor` builds an in-memory record for the ledger the next check reads.
- The profile-keyed payee target (`payee-fp:hmac-sha256-profile-key:`, built in `record.go`, the
  Go twin of `LOCAL_ONLY_TARGET_PREFIX`) is matched locally and never appears in a reason or a
  report value. `payee_fp_profile_test.go` moves with the code and pins the targets, and PR A adds
  an assertion that no reason or `Report` value carries the prefix.
- A Go path that writes report or bundle output for another party needs the same share-boundary
  review as a Python one. That includes the `counterparty` copy reports that
  `standalone_report.go` reads, should a writer ever be added. The module has no such writer
  today.
