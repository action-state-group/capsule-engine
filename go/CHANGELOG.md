# Changelog: the Go guard module

Each entry's pack and definition lists come from the diff of `packs/*/PROVENANCE.json`
and `portedDefinitions` (`guard/engine.go`).

## Unreleased

## 0.1.1 — 2026-10-23

Both changes are to wording and labels. No outcome, constraint or exported API changes, and
the packs and definitions are those of 0.1.0.

- Changed (capsule-engine#210, switch-over gate 2): a record or history act whose disclosed
  body is not the one its capsule seals is refused with the reason first and the id and
  digests last, so a caller that shows only the first few hundred bytes (capsule-cli shows the
  first 300 of a checker's stderr) still shows a refusal. `DecodeCheckInput`,
  `DecodeCheckInputRecord`, `DecodeRecord` and `ReadSaleBundle` now return "the record's body is not the one its capsule seals:
  the disclosed agent_input digests to X, not the header's agent_input_digest Y", in place of
  "record: the disclosed agent_input digests to X, not the header's agent_input_digest Y: the
  body is not the one the capsule seals". `DecodeHistoryAct` returns "a history act's body is
  not the one its capsule seals (act ID): ..." in place of "history act ID: record: ...". A
  header with no agent_input_digest reads "... is not bound to its capsule: the capsule
  header has no model_attestation.compute_attestation.agent_input_digest". Every other
  refusal of a history act also leads with its reason and names the act after it, as
  "a history act's capsule header has no operator (act ID)".
  A record of a sale bundle's deal reads "the body of a record of the deal is not the one its
  capsule seals (record ID): ..." in place of "record ID of the deal: record: ...".
- Changed (capsule-engine#217): `Report` labels a held caps window with an approver, where
  nothing failed, with the escalation: `CheckReport.Decision` is `escalate` (it asks for
  approval), no longer `not_evaluable`. This matches the Python engine's
  `GuardDecision.verdict` at the same commit. With no approver for the class the window is
  still refused, labelled `not_evaluable` with `CheckReport.Refused` set, as in 0.1.0; a
  record in no deal (`deal_id` conflict) is still labelled `not_evaluable`.

## 0.1.0 — 2026-10-23

The first release of the module `github.com/action-state-group/capsule-engine/go`: the
Go guard, moved into this repository with the fix below and the caller-side parameters in
`README.md` ("What the caller decides").

The module carries its own `LICENSE` (Apache-2.0, the repository's), since a Go module zip
holds only the files under `go/`.

- Packs: asg/everyday/0.3.4-0.3.8 and asg/seller/0.1.0-0.1.5, at the digests
  in their PROVENANCE.json.
- Definitions: the 48 (id, digest) pairs in `portedDefinitions`.
- Caught up with the guard as the consuming product had it through
  7a32191, its last guard change before rc21. The tests now take asg/everyday/0.3.8 and
  asg/seller/0.1.5 as the current packs, with the everyday 0.3.8 fixtures
  and their parity run at 0c93a58. There is no guard code change.
- Fixed, against the Python reference (capsule-engine#214, groups 3 and 4):
  a purchase whose caps window the history cannot decide is no longer
  allowed. With nothing else failing, the engine escalates when the class
  has an approver and refuses otherwise. `Report` still labels it
  `not_evaluable`, as the engine's `GuardDecision.verdict` does; when the
  class has no approver (`external_commitment.other`), the refusal sets
  `CheckReport.Refused`.
