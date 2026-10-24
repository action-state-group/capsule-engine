# Changelog: the Go guard module

Each entry's pack and definition lists come from the diff of `packs/*/PROVENANCE.json`
and `portedDefinitions` (`guard/engine.go`).

## Unreleased

- Changed (capsule-engine#234, ruling A on #214 hold A): under a fold that counts executed acts
  (spend.weekly/3.1.0), a check input's history lets a cancel or refund free the headroom of the
  partial charge it cites, as the Python engine does (`historyFreed`). It lowers that charge's
  counted amount by the amount it returns, never below its `spend_minor`; it must state the money
  moved in, cite the charge's capsule id under `supersedes`, carry its operator and developer,
  and be sealed between the charge and the check. Any other cancel or refund frees nothing. No
  exported API, pack or definition changes; frozen folds are unchanged.

## 0.1.3 — 2026-10-23

Fixes toward the Python engine, the reference (capsule-engine#214). No exported API changes,
and the packs and definitions are those of 0.1.2. Outcomes move only where a fix below says
so.

- Fixed (group 1): when dedupe matches the same act in another deal, its evidence's
  `matched_amount_minor` is the matched history act's spend, the amount dedupe keyed it on
  (`keyAmount`), as the engine reports it. It was the act's stated `amount_minor`. Only the
  evidence digest changes.
- Fixed (group 2): a check held because the record names another action taxonomy carries the
  engine's evidence, `{constraint_id, in_scope, missing_field, taxonomy_mismatch:
  {record_taxonomy_version, engine_taxonomy_version}}`. The checks held are now the engine's:
  every check but dedupe, verify_before_dispatch, single_commitment, promise_never,
  release_on_acceptance, credential_pattern and plan_containment. So the seller packs'
  `price_floor` and `offer_expiry` are now held, and `promise_never` is no longer held: it
  runs and decides. A record at another taxonomy can change outcome: one whose only failure
  was `price_floor` or `offer_expiry` is now refused (`deny`, `RefusedTaxonomy`) where it
  failed that check before, and one that fails `promise_never` is now denied on that failure.
  Their report rules move the same way (`not_evaluable` for the two newly held, and
  `promise_never`'s own verdict). A record at the engine's taxonomy is unchanged.
- Fixed (hold A): the caps window no longer holds when a record in it carries a
  `spend_minor` that differs from its `amount_minor`. That hold (caps `not_evaluable`, so the
  check escalated with an approver and was refused without one) covered Go's own reading of
  the stated amount. The window now sums a history act as the engine does: under a fold that
  counts executed acts (spend.weekly/3.1.0, everyday 0.3.6 to 0.3.8) at its stated
  `amount_minor`, and under any other fold (spend.weekly/3.0.0 and 1.0.0: everyday 0.3.4 and
  0.3.5, seller 0.1.0 and 0.1.1) at its `spend_minor`. Outcome change: a check with such an
  act in its window is now decided on the total, so it passes (allow) or fails the window
  limit, where it was held before. Two cases stay stricter than the engine, which leaves
  spend out of the total there, and are pinned in `KNOWN-DIFFERENCES.json`: an accepted act
  with no `spend_minor` is counted at its stated amount (0 when that is negative) under any
  fold, and under spend.weekly/3.1.0 an act whose spend is above its stated amount is counted
  at its spend.
- Fixed: a history cancel or refund whose capsule chains `supersedes` to a charge no longer
  takes that charge back out of the window. The engine's records of a history act carry no
  chain, so it reads every such reversal as unlinked; Go read the capsule's chain and linked
  it. Outcome change: the window total no longer drops by the reversal, so a check that
  passed on the lowered total can now fail the window limit (escalate, or refused with no
  approver). A ledger capsule's own chain is read as before.
- Fixed: the caps window reads times to the microsecond, as the engine's Python datetimes
  do. An act within the same microsecond as either end of the window was counted by one and
  not the other when its timestamp carried more than six fractional digits. No capsulectl or
  capsule-emit release writes more than six, so no capsulectl check changes.

## 0.1.2 — 2026-10-23

An additive API change. No outcome, constraint, label or written report changes, and the
packs and definitions are those of 0.1.1.

- Added: `CheckReport.RefusedFor`, the cause of a refusal with no rule failed, set whenever
  `CheckReport.Refused` is. It is one of `RefusedHeldCaps` (caps could not be evaluated over
  the history and the class has no approver, #216), `RefusedNotEvaluated` (a check that
  fails closed could not be evaluated, #137), `RefusedTaxonomy` (the record was sealed at
  another action taxonomy than the rules') and `RefusedDealConflict` (the record is in no
  single deal, #127, with caps held and no approver; a would-allow in no deal with nothing
  held is not_evaluable and not refused). Like `Refused`, it is not part of the written
  report. A caller can now show the real reason for a refusal without inferring it from the
  rules.

## 0.1.1 — 2026-10-23

Both changes are to wording and labels. No outcome, constraint or exported API changes, and
the packs and definitions are those of 0.1.0.

- Changed (capsule-engine#210, switch-over gate 2): a record or history act whose disclosed
  body is not the one its capsule seals is refused with the reason first and the id and
  digests last, so a caller that shows only the first few hundred bytes (capsule-cli shows the
  first 300 of a checker's stderr) still shows a refusal. `DecodeCheckInput`,
  `DecodeCheckInputRecord`, `DecodeRecord` and `SaleBundle.Check` now return "the record's
  body is not the one its capsule seals: the disclosed agent_input digests to X, not the
  header's agent_input_digest Y", in place of
  "record: the disclosed agent_input digests to X, not the header's agent_input_digest Y: the
  body is not the one the capsule seals". `DecodeHistoryAct` returns "a history act's body is
  not the one its capsule seals (act ID): ..." in place of "history act ID: record: ...". A
  header with no agent_input_digest reads "... is not bound to its capsule: the capsule
  header has no model_attestation.compute_attestation.agent_input_digest". Every other
  refusal of a history act also leads with its reason and names the act after it, as
  "a history act's capsule header has no operator (act ID)".
  Another record of the same deal in a bundle (any bundle input with `records` and a
  deal, not only a sale bundle) reads "the body of a record of the deal is not the one its
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
