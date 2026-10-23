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
3. An admin pushes `go/vX.Y.Z` at that commit. The version is fetchable as soon as the tag
   exists, and proxy.golang.org and sum.golang.org keep it as first fetched.
4. Confirm the release from an empty directory and module cache, through proxy.golang.org only,
   with every setting that would skip it or sum.golang.org cleared:

   ```bash
   cd "$(mktemp -d)" && GOENV=off GOFLAGS= GOPROXY=https://proxy.golang.org GOSUMDB=sum.golang.org GOPRIVATE= GONOPROXY= GONOSUMDB= GOMODCACHE="$PWD/mod" go mod download -json github.com/action-state-group/capsule-engine/go@vX.Y.Z
   ```

   It must succeed, which verifies the module against sum.golang.org, and its `Origin.Hash`
   must equal `git rev-parse go/vX.Y.Z^{commit}`. (For an annotated tag, `git ls-remote` shows
   the tag object first, not the commit.)

**Version bumps, while on v0:**

- **Minor:** a new pack version, a new ported definition, or an API change.
- **Patch:** a fix that brings Go into agreement with the Python reference at the same
  (id, digest), or any other fix that leaves the vectors' expected results unchanged.
- **Neither:** a change in what an already-ported (id, digest) means. That needs a new
  definition version with a new digest, or a retirement in
  `capsule_engine/guards/wickets/retired.py`, on both sides.

**Safeguards:**

- **Tags.** As in agent-action-capsule, capsule-emit and checkpointed-local-log, the
  `release tags` ruleset lets only admins move or delete any tag, and the `go release tags`
  ruleset lets only admins create `go/v*` tags.
- **Withdrawal.** A bad release can only be withdrawn with a `retract` directive in a later version.
