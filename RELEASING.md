# Releasing

## The Python package

capsule-engine's Python package is installed from source and is not published to a package
registry (see `README.md`, "Install").

## The Go module

`go/` is the module `github.com/action-state-group/capsule-engine/go`. A Go release is the
pushed tag itself, with no workflow in between:

1. Every check is green on the commit on `main`, including `conformance`, which holds the Go
   guard to the Python reference.
2. `go/CHANGELOG.md` has the version's entry. Its pack and definition lists come from the diff of
   `go/packs/*/PROVENANCE.json` and `portedDefinitions` since the last tag.
3. An admin pushes `go/vX.Y.Z` at that commit.

**Version bumps, while on v0:**

- **Minor:** a new pack version, a new ported definition, or an API change.
- **Patch:** a fix that brings Go into agreement with the Python reference at the same
  (id, digest), or any other fix that leaves the vectors' expected results unchanged.
- **Neither:** a change in what an already-ported (id, digest) means. That needs a new
  definition version with a new digest, or a retirement in
  `capsule_engine/guards/wickets/retired.py`, on both sides.

**Safeguards:**

- Creating, moving and deleting `go/v*` tags is admin-only, by a tag ruleset.
- A bad release can only be withdrawn with a `retract` directive in a later version.
