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
| Tag rulesets | `release tags` (all tags: update and deletion are admin-only) and `go release tags` (`refs/tags/go/v*`: creation is admin-only) | the same two, created on 2026-10-22 (rulesets 25692594 and 25692595) |
| Branch ruleset | capsule-emit and checkpointed-local-log: `dco` on the default branch (no deletion, no force push, `dco` required). agent-action-capsule: none | none, and `main` has no branch protection |
| Attestations | PyPI: PEP 740 attestations, which the pinned `gh-action-pypi-publish` uploads by default with trusted publishing. npm: its own provenance. Go and crates.io: none | none |
| Post-publish checks | Before upload only: npm `gitHead` or already-published, and crates.io availability. After it, "confirm the run and the new version on the registry". No proxy.golang.org, sum.golang.org or PyPI byte-compare | none |
| Action pinning | full SHA with the version in a trailing comment, in every workflow. `permissions: contents: read` in most, though not all. CI concurrency `ci-${{ github.workflow }}-${{ github.ref }}` only in the Go and TS CI of capsule-emit and checkpointed-local-log | version tags (`@v4`) apart from `dco.yml`. `ci.yml` and `vendor-drift.yml` have no permissions block. No concurrency |

## What capsule-engine publishes

- **The Go module** `github.com/action-state-group/capsule-engine/go`: a pushed `go/v*` tag, as
  in the siblings. This is what `go/v0.1.0` releases.
- **The Python package** `capsule-engine` (pyproject `0.0.1`) is not on PyPI, and the name is
  unclaimed there. Whether to publish it is a product and packaging decision, not part of this
  work. This note does not decide it, and the PR below does not change it.

## What aligning means here

Matching the siblings for a repo that releases only Go is short, because the siblings have no
Go release automation either:

1. **Tag rulesets, as in the siblings.** `release tags` and `go release tags`, with the same
   rules and the same bypass (repository admin), are a repository setting an admin applies. They
   now exist. The first tag needed them, because without them any writer could push
   `go/v0.1.0`, and the proxy and sumdb keep whatever was pushed first.
   A `dco` branch ruleset, as two of the siblings have, is a further admin setting. It is not a
   release prerequisite: step 1 of RELEASING.md checks by hand that every check is green on the
   tagged commit.
2. **Workflow hygiene, in a PR.** Pin every action to a full SHA with a version comment, at the
   versions the siblings use. Give every workflow `permissions: contents: read` (`ci.yml` and
   `vendor-drift.yml` have none today), and give every push-and-PR workflow the concurrency group
   that the siblings' Go and TS CI use.
3. **RELEASING.md in the siblings' shape.** Name both rulesets under Safeguards, say the version
   is fetchable as soon as the tag exists, and add the check after tagging (below).

**If Python publishing is approved later,** the Python lane is the siblings': copy
`.github/release.py` byte for byte, add `.github/releases.json` with a single `python` entry
(`python/pyproject.toml`, `python/v`, `publish-python.yml`), copy `tag-release.yml`, and copy
`prepare-release.yml` with its package choice cut to `python`. Take `publish-python.yml` from
agent-action-capsule (its separate build job runs `twine check`) with its `python/` working
directory, `python/dist/` artifact path and `python/**` path filter as they are. Before the first upload, an admin creates the `pypi` environment (tag
rule `python/v*`) and registers the PyPI trusted publisher.

## What this does not add

Go attestations, an automated "main is green" gate before a Go tag, and post-publish jobs
against proxy.golang.org and sum.golang.org or a PyPI byte-compare: none of the siblings has
them. Adding them here alone would make capsule-engine the odd one out, which is what aligning
is meant to stop. If they are wanted, they belong in all four repos together.

One manual step goes beyond the siblings, whose Go sections say only that an admin pushes the
tag. After tagging, RELEASING.md has the admin fetch the version through proxy.golang.org and
verify it against sum.golang.org, from an empty module cache with every setting that could
bypass either one cleared, and compare its `Origin.Hash` with the tagged commit. It costs one
command and shows that what the proxy and sumdb recorded is the intended commit. It is proposed for the siblings'
RELEASING.md too.
