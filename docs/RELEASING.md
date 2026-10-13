# Releasing capsule-engine

## Status

No release has been cut. `git tag` is empty and the version is `0.0.1`.
Install is from source ([README, Install](../README.md#install)).

What exists today:

- **Code identity: shipped.** Every guard decision carries a code identity,
  captured when the check runs: the commit (clean git checkout), or a
  SHA-256 over the verdict-producing source on disk (uncommitted edits, or an
  installed wheel). Its assurance is `self-attested`.
  [README, Releases and code identity](../README.md#releases-and-code-identity)
  has the shape.
- **Build provenance attestation: written, not yet run.**
  `.github/workflows/release.yml` runs only on a `v*` tag push. Until the
  first tag, there is no release, no attestation and no Sigstore bundle, and
  the code identity's assurance stays `self-attested`.

## Cutting a release

1. Set the version in `pyproject.toml` and `capsule_engine/__init__.py` to
   the new version, in a reviewed pull request.
2. After it merges, a maintainer tags the merge commit with `v<version>`
   (a hyphenated tag, e.g. `v0.1.0-rc1`, is published as a pre-release) and
   pushes the tag. The workflow refuses a tag that does not match
   `pyproject.toml`.
3. The workflow:
   - stamps the tagged commit into `capsule_engine/_build_commit`;
   - builds the wheel and sdist;
   - installs the wheel outside the checkout and checks the code identity it
     reports: the stamped commit as `build_commit`, and the same source-tree
     digest as the checkout;
   - writes `SHA256SUMS` over the wheel and sdist;
   - attests build provenance for both (`actions/attest-build-provenance`,
     pinned by commit), copies the attestation bundle to
     `capsule-engine-<tag>.sigstore.json` and adds its SHA-256 to
     `SHA256SUMS`;
   - creates the GitHub release with those files. Nothing goes to PyPI.

## Checking a release's files

```
sha256sum -c SHA256SUMS
gh attestation verify capsule_engine-<version>-py3-none-any.whl \
  --bundle capsule-engine-<tag>.sigstore.json \
  --repo action-state-group/capsule-engine \
  --signer-workflow action-state-group/capsule-engine/.github/workflows/release.yml
```

With `--bundle`, the check needs no GitHub account. It ties the file's bytes
to the workflow run at the tagged commit. It says nothing about which code a
running engine reports in a decision: that identity remains `self-attested`.
