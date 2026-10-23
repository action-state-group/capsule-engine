# Releasing

## The Python package

The package in `python/` releases to PyPI as `capsule-engine`, the same way as in
agent-action-capsule, capsule-emit and checkpointed-local-log. A release is a merged version
bump: you choose the bump, review it as a pull request, and merging it tags and publishes. The
package is listed in [`.github/releases.json`](.github/releases.json), which
[`.github/release.py`](.github/release.py) (an identical copy of the siblings') reads.

| Package | Manifest | Tag | Publisher | Environment |
|---|---|---|---|---|
| `python` | `python/pyproject.toml` | `python/v<version>` | `publish-python.yml` | `pypi` (tag rule `python/v*`) |

The prefix is only on the Git tag. The PyPI version stays plain (`0.1.0`).

1. **Prepare.** Run [Release · Prepare](https://github.com/action-state-group/capsule-engine/actions/workflows/prepare-release.yml)
   on `main`, choosing `patch`, `minor` or `major`. It raises the version, commits the bump
   with a sign-off, and pushes `release/python-v<version>`.
2. **Review.** Open the pull request from the link in the run summary. Open it yourself: a pull
   request opened by the workflow would not start CI. Add the version's entry to
   `python/CHANGELOG.md` on the same branch if it is not in yet.
3. **Merge.** [Release · Tag](https://github.com/action-state-group/capsule-engine/actions/workflows/tag-release.yml)
   runs on the merge. When the version changed, it creates the tag and GitHub Release on the
   merge commit and dispatches `publish-python.yml` on that tag.
4. **Publish.** The publisher builds the sdist and wheel and runs `twine check`, checks that
   the tag is `python/v` plus the manifest version and that the commit is on `main`, then
   uploads through OIDC trusted publishing from the `pypi` environment, with PEP 740
   attestations.
5. **Verify.** Its `verify` job then compares the sha256 of every file PyPI serves for the
   version with the files the build made, and fails on any difference. This step goes beyond
   the siblings.

Bumping the version by hand in any pull request releases it the same way when it merges.

If a publish fails after the tag exists:

- **A transient or configuration failure** (a PyPI outage, the trusted publisher or the
  environment): fix it and re-run the failed publisher run.
- **A defect in the code or the workflow:** a re-run uses the tagged commit again, so a fix
  merged to `main` does not reach it. Release the fix as a new version.
- **Release · Tag failed before dispatching** (no publisher run exists): re-run the Release ·
  Tag run. It accepts a tag and GitHub Release it already created on the same commit and
  dispatches the publisher again.

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
  `python/capsule_engine/guards/wickets/retired.py`, on both sides.

## Safeguards

- **Guard.** `release.py check` refuses a tag from another package, a version that differs
  from the manifest, and a commit that is not on `main`.
- **Environment.** `pypi` accepts deployments only from `python/v*` tags, so a job started from a
  branch or another tag cannot upload.
- **Tags.** As in agent-action-capsule, capsule-emit and checkpointed-local-log, the
  `release tags` ruleset lets only admins move or delete any tag, and the `go release tags`
  ruleset lets only admins create `go/v*` tags.
- **Withdrawal.** A bad Go release can only be withdrawn with a `retract` directive in a later
  version. A PyPI version can be yanked but never re-uploaded, so a fix is a new version.
