# Changelog: the Go guard module

Each entry's pack and definition lists come from the diff of `packs/*/PROVENANCE.json`
and `portedDefinitions` (`guard/engine.go`).

## Unreleased

The Go guard, moved into this repository unchanged apart from the caller-side
parameters in `README.md` ("What the caller decides").

- Packs: asg/everyday/0.3.4-0.3.8 and asg/seller/0.1.0-0.1.5, at the digests
  in their PROVENANCE.json.
- Definitions: the 48 (id, digest) pairs in `portedDefinitions`.
- Fixed, against the Python reference (capsule-engine#214, groups 3 and 4):
  a purchase whose caps window the history cannot decide is no longer
  allowed. With nothing else failing, the engine escalates when the class
  has an approver and refuses otherwise. `Report` still labels it
  `not_evaluable`, as the engine's `GuardDecision.verdict` does; when the
  class has no approver (`external_commitment.other`), the refusal sets
  `CheckReport.Refused`.
