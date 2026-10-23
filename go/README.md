# capsule-engine Go guard

`github.com/action-state-group/capsule-engine/go` is the Go implementation of
this repository's guard. The Python package (`../capsule_engine`) is the
reference; these packages decide the same actions the same way, and
capsulectl plugins compile them in.

| Package | What it is |
|---|---|
| `canon` | JCS canonical JSON values, used by every digest |
| `pack` | the pack runtime: loading a pack, its wicket and fold definitions and their digests, pinned at the digests the engine computed |
| `packs` | the embedded pack versions the Go guard serves (everyday, seller), generated from `../capsule_engine/packs/catalog` |
| `guard` | the engine: every ported wicket check, the action-class gate, folds, deal records and history, external-check-input/v0 envelopes, sale bundles, and the per-rule report |

`portedDefinitions` (`guard/engine.go`) is the exact list of wicket and fold
definitions, by id and digest, that this code implements. A pack citing any
other definition is refused (`ErrNotPorted`), so a changed definition is never
run under an old port.

## What the caller decides

The module holds no product's choices. The caller passes:

- which pack version to check each party role against (the module has
  `pack.LoadEveryday038`, `pack.LoadSeller015`, ..., and no default);
- the catalog a pack.yaml-only source resolves against (`pack.LoadPath(dir,
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

## Running it

```sh
GOWORK=off gofmt -l .
GOWORK=off go vet ./...
GOWORK=off go test ./...
```

From the repository root, `python scripts/generate_go_packs.py --check`
checks the embedded tree against the catalog, and
`python scripts/conformance_vectors.py --check` holds the Python engine to the
shared vectors that `guard/conformance_test.go` holds this module to. CI runs both on every pull
request (`.github/workflows/conformance.yml`).

## Versions

Tags are `go/vX.Y.Z` (see `../RELEASING.md`). A tag pins the embedded pack
set and `portedDefinitions`; `CHANGELOG.md` lists what each tag adds. The
design is `../docs/design/go-guard-module.md`.
