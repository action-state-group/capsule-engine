# Release CI aligned with the sibling repos

Status: proposal (capsule-engine#210 follow-up, before the first `go/v*` tag).

capsule-engine's release process should match agent-action-capsule, capsule-emit and
checkpointed-local-log. This note maps each piece of their release CI (read at their `main`
on 2026-10-22: `17f0c9f`, `4e94acc`, `32ce9f3`) onto this repo. It also says what this repo
publishes and what the PR that follows it changes.

## What the sibling repos have

All three use the same setup, and `.github/release.py` is a byte-identical copy in each:

| Piece | Siblings | capsule-engine today |
|---|---|---|
| Release PR | `prepare-release.yml` (dispatch: package and bump) pushes `release/<pkg>-v<ver>` with a signed-off bump. A person opens the PR and adds the CHANGELOG entry. Merging it is the approval. | none |
| Tag | `tag-release.yml` runs on push to `main`. `release.py changed` finds each manifest whose version moved, then it runs `release.py check`, `gh release create`, and dispatches the publisher | none |
| Tag prefixes | `.github/releases.json`: `python/v`, `ts/v`, `crates/<crate>-v`. The registry version stays plain | none (`go/v*` only, in RELEASING.md) |
| Python publish | `publish-python.yml`: PyPI trusted publishing (`pypa/gh-action-pypi-publish`, `id-token: write`) from environment `pypi`, which accepts only `python/v*` tags | none. The package installs from source |
| npm / crates.io | OIDC trusted publishing from `npm` and `crates-io` | not applicable |
| Go | Tag only. An admin pushes `go/vX.Y.Z`. No workflow, and no automated check that `main` CI is green | same, by RELEASING.md |
| Environments | `pypi`, `npm`, `crates-io` with tag-pattern branch policies. No required reviewers | none |
| Tag rulesets | `release tags` (all tags: update and deletion are admin-only) and `go release tags` (`refs/tags/go/v*`: creation is admin-only) | **none**, although RELEASING.md says one exists |
| Attestations | none (npm adds its own provenance) | none |
| Post-publish checks | Before upload only: npm `gitHead` or already-published, and crates.io availability. After it, "confirm the run and the new version on the registry". No proxy.golang.org, sum.golang.org or PyPI byte-compare | none |
| Action pinning | full SHA with the version in a trailing comment. `permissions: contents: read` at the top. CI concurrency `ci-${{ github.workflow }}-${{ github.ref }}` | version tags (`@v4`), no permissions blocks, no concurrency |

## What capsule-engine publishes

- **The Go module** `github.com/action-state-group/capsule-engine/go`: a pushed `go/v*` tag, as
  in the siblings. This is what `go/v0.1.0` releases.
- **The Python package** `capsule-engine` (pyproject `0.0.1`) is not on PyPI, and the name is
  unclaimed there. Whether to publish it is a product and packaging decision, not part of this
  work. This note does not decide it, and the PR below does not change it.

## What aligning means here

Matching the siblings for a repo that releases only Go is short, because the siblings have no
Go release automation either:

1. **Tag rulesets, as in the siblings.** Create `release tags` and `go release tags` with the
   same rules and the same bypass (repository admin). This is a repository setting that only an
   admin can apply, so it is not in the PR. It is the one piece the first tag waits on, because
   without it any writer can push `go/v0.1.0`, and the proxy and sumdb keep whatever was pushed.
2. **Workflow hygiene, in a PR.** Pin every action to a full SHA with a version comment, at the
   versions the siblings use. Give every workflow `permissions: contents: read` (`ci.yml` and
   `vendor-drift.yml` have none today), and give every push-and-PR workflow the siblings' CI
   concurrency group.
3. **RELEASING.md in the siblings' shape.** Name both rulesets under Safeguards, say the version
   is fetchable as soon as the tag exists, and add the check after tagging (below).

**If Python publishing is approved later,** the Python lane is a copy, not a design: copy
`.github/release.py` byte for byte, add `.github/releases.json` with a single
`python` entry (`pyproject.toml`, `python/v`, `publish-python.yml`), copy `prepare-release.yml`
and `tag-release.yml`, and take `publish-python.yml` from agent-action-capsule (its separate build
job runs `twine check`). Before the first upload, an admin creates the `pypi` environment (tag
rule `python/v*`) and registers the PyPI trusted publisher.

## What this does not add

Build attestations, an automated "main is green" gate before a Go tag, and post-publish
checks against proxy.golang.org and sum.golang.org or a PyPI byte-compare: none of the siblings
has them. Adding them here alone would make capsule-engine the odd one out, which is what
aligning is meant to stop. If they are wanted, they belong in all four repos together, so
they are left out here.

The first Go release still checks by hand what the siblings check by hand. Before tagging,
`conformance` and every other check must be green on the tagged commit. After tagging,
`go mod download -json github.com/action-state-group/capsule-engine/go@v0.1.0`, with the default
`GOPROXY` and `GOSUMDB`, must fetch the version from proxy.golang.org and verify it against
sum.golang.org, and its `Origin` must name the tagged commit. RELEASING.md gives the command.
