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
capsule-engine admission evaluate --pack pack-definition.json [--pack-id ID --digest SHA256] [--input FILE] < input.json
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
diff. CI runs both on every pull
request (`.github/workflows/conformance.yml`).

## Versions

Tags are `go/vX.Y.Z` (see `../RELEASING.md`). A tag pins the embedded pack
set and `portedDefinitions`; `CHANGELOG.md` lists what each tag adds. The
design is `../docs/design/go-guard-module.md`.
