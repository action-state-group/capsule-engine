# capsule-engine Go guard

`github.com/action-state-group/capsule-engine/go` is the Go implementation of
this repository's guard. The Python package (`../python/capsule_engine`) is the
reference; these packages decide the same actions the same way, and
capsulectl plugins compile them in.

| Package | What it is |
|---|---|
| `canon` | JCS canonical JSON values, used by every digest |
| `pack` | the pack runtime: loading a pack, its wicket and fold definitions and their digests, pinned at the digests the engine computed |
| `packs` | the embedded pack versions the Go guard serves (everyday, seller, inference), generated from `../python/capsule_engine/packs/catalog` |
| `guard` | the engine: every ported wicket check, the action-class gate, folds, deal records and history, external-check-input/v0 envelopes, sale bundles, and the per-rule report |

`portedDefinitions` (`guard/engine.go`) is the exact list of wicket and fold
definitions, by id and digest, that this code implements. A pack citing any
other definition is refused (`ErrNotPorted`), so a changed definition is never
run under an old port.

## What the caller decides

The module holds no product's choices. The caller passes:

- which pack version to check each party role against (the module has
  `pack.LoadEveryday038`, `pack.LoadSeller015`, ..., and no default);
- the catalog a source holding only its `pack-source.yaml` resolves against (`pack.LoadPath(dir,
  pack.Catalog{...})`);
- the copy audiences it reads a carried thread's report for
  (`guard.SaleVerification.CopyAudiences`; nil reads the engine's);
- the schema id it writes a `guard.CheckReport` under (`Report` leaves it
  empty).

## The embedded packs

`packs/` is generated: `python scripts/generate_go_packs.py` writes it from
the catalog, together with `packs/embed.go`, `pack/registry_gen.go`,
`packs/engine/` (the action taxonomy and its PROVENANCE.json) and
`guard/testdata/engine-definitions.json`. Do not edit them by hand. A pack
version directory, once written, is frozen, so an older pinned pack keeps
loading at its digest. When the catalog cuts a new version of a served pack,
run the generator in the same change.

## Admission from outside Go

`cmd/capsule-engine` runs the admission evaluator for a caller that is not Go:

```bash
go install github.com/action-state-group/capsule-engine/go/cmd/capsule-engine@vX.Y.Z
capsule-engine admission evaluate --pack pack-definition.json [--pack-id ID --digest SHA256] [--input FILE] [--admit FILE] < input.json
```

`--pack` is a compiled admission `pack-definition.json`; with `--pack-id` and `--digest` it must be
exactly that pack. The input is JSON read from `--input` or stdin, in the form of a vector's `input`
(`conformance-vectors/admission/v0/cases.json`, `admission.DecodeInputs`): unknown members and
times that are not RFC 3339 are refused. Stdout is one JSON object: `verdict`, `source`, `tier`
(null when none), `band`, `rows`, `asks`, `pack` (`id`, `digest`), `block` (the admission_decision
block as JCS text) and `block_sha256`. Exit status 0 means a decision was made, whatever its verdict;
2 means none could be (usage, unreadable or refused pack, pin mismatch, unusable input), with nothing
on stdout and one line on stderr. The decision is about one counterparty and is local-only; the
command prints it to the caller's stdout and exports nothing. `cmd/capsule-engine` tests it over
every shared vector.

With `--admit FILE`, stdout also holds `admit`, the decision as the body of an x-deal-v1 `admit`
record for the caller to seal in its own journal (evidencebook `dealprofile/x-deal-v1/PROFILE.md`,
`admit`; capsule-engine#303), and `admits_deal`. The file holds what only the caller has:
`{"asks": [hex], "answers": [hex], "card_digest": hex}`, the SHA-256 digests of its own `ask` and
`answer` deal records of this admission and of the JCS bytes of the card document it read; unknown
or missing members are refused. The body's `rows` are every row that applied to the deal's tier, whatever
its result (a row not read included), as the pack names them, and its `nonce` is drawn fresh from `crypto/rand` on every run. `admit` is null for a decision
without a tier (a NO), which is not sealed. `admits_deal` says whether an agreement may cite the
admit: true for GO and for LIMIT at a tier that holds the deal's band, false otherwise. The library
call is `Decision.Admit(AdmitRefs)` (Rust `Decision::admit`), decided the same in both languages over
`conformance-vectors/admission/v0/admit.json`.

## A rules checker for a deal profile

`capsule-engine guard check [--pack ID]` reads one `external-check-input/v0` on stdin and prints one
`external-check-result/v0`: the record and its history checked with this module's guard, one finding
per rule, caps as one finding per limit (`r05-spending-limits#per_purchase`, `#7d`). It is a deal
profile's rules checker (`capsulectl profile update --rules-checker FILE`, pinned by sha256). The
packs and the two schemas are embedded, so the binary stands alone; the schemas are evidencebook's
public copies (`cmd/capsule-engine/schemas/PROVENANCE.json`). The pack is `--pack`, else the
input's `rules_pin`, else its `party_role` (a buyer, or no `party_role`: `asg/everyday/0.3.10`; a seller:
`asg/seller/0.1.5`). Those defaults are fixed in `partyPacks` (`cmd/capsule-engine/guard_check.go`) and move only by a change there, so a deal that must run another embedded version names it with `--pack` or a `rules_pin`. A `party_role` with no pack leaves the action unevaluable: no pack runs, and the `not_evaluable` result carries the buyer pack's id and digest because a result must name one (the goldens hold the same). Exit 0 means a result was printed; exit 2 means none could be, with one line
on stderr that names no value from the input. It applies the pack's own limits only: a limit a person sets is not applied by this command, so a host that lets a person set limits applies them itself (the `" you set"` reason wording is dead here). The result names the pack it ran (`ruleset_id`, `definition_digest`), so a default chosen by role is always visible. The output is held
byte for byte to `guard/testdata/emit/golden/` by `TestGuardCheckGoldens`. It is a subcommand of
`capsule-engine` rather than the `capsulectl-engine` plugin's `engine check`, which checks an
`engine-ruleset/v0` and runs no pack.

## Running it

```sh
GOWORK=off gofmt -l .
GOWORK=off go vet ./...
GOWORK=off go test ./...
```

From the repository root, `python scripts/generate_go_packs.py --check`
checks the embedded tree against the catalog, and
`python scripts/conformance_vectors.py --check` holds the Python engine to the
shared vectors that `guard/conformance_test.go` holds this module to.
`guard/report_golden_test.go` holds the per-rule report's wording to
`guard/testdata/report-goldens.json`. After a deliberate wording change, run
`go test ./guard -run TestReportGoldens -update-report-goldens` and review the
diff. CI runs `go test ./...` (which includes `guard/conformance_test.go` and
`guard/report_golden_test.go`) and `generate_go_packs.py --check` on every push
to `main` and every pull request that touches `go/`, `python/`, `rust/`,
`scripts/`, the vectors or the workflow (`.github/workflows/conformance.yml`),
and `conformance_vectors.py --check` nightly, on manual runs, and on pushes to
`main` that touch its inputs (`.github/workflows/slow-suites.yml`).
`scripts/check.sh` runs all four before a push.

## Versions

Tags are `go/vX.Y.Z` (see `../RELEASING.md`). A tag pins the embedded pack
set and `portedDefinitions`; `CHANGELOG.md` lists what each tag adds. The
design is `../docs/design/go-guard-module.md`.
