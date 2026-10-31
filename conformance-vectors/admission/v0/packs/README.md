# Test admission packs

Each `.json` here is the `pack-definition.json` that evidencebook-skills'
`scripts/pack_compile.py compile` writes for the `.yaml` beside it. The
evaluators load only the `.json`; the `.yaml` is kept for people to read and to
recompile from. After editing a `.yaml`, recompile it there and copy the
`pack-definition.json` here under the same name, then run
`go test ./admission -update` (from `go/`) to rewrite the block digests in
`../cases.json`.
