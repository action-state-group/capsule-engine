# Changelog: the Go guard module

Each entry's pack and definition lists come from the diff of `packs/*/PROVENANCE.json`
and `portedDefinitions` (`guard/engine.go`).

## Unreleased

## 0.1.0 — 2026-10-23

The first release of the module `github.com/action-state-group/capsule-engine/go`: the
Go guard, moved into this repository unchanged apart from the caller-side parameters in
`README.md` ("What the caller decides").

The module carries its own `LICENSE` (Apache-2.0, the repository's), since a Go module zip
holds only the files under `go/`.

- Packs: asg/everyday/0.3.4-0.3.8 and asg/seller/0.1.0-0.1.5, at the digests
  in their PROVENANCE.json.
- Definitions: the 48 (id, digest) pairs in `portedDefinitions`.
- Caught up with the guard as the consuming product had it through its
  rc21 commit, 7a32191. The tests now take asg/everyday/0.3.8 and
  asg/seller/0.1.5 as the current packs, with the everyday 0.3.8 fixtures
  and their parity run at 0c93a58. There is no guard code change.
- Fixed, against the Python reference (capsule-engine#214, groups 3 and 4):
  a purchase whose caps window the history cannot decide is no longer
  allowed. With nothing else failing, the engine escalates when the class
  has an approver and refuses otherwise. `Report` still labels it
  `not_evaluable`, as the engine's `GuardDecision.verdict` does; when the
  class has no approver (`external_commitment.other`), the refusal sets
  `CheckReport.Refused`.
