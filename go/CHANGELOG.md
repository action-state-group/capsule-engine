# Changelog: the Go guard module

Each entry's pack and definition lists come from the diff of `packs/*/PROVENANCE.json`
and `portedDefinitions` (`guard/engine.go`).

## Unreleased

- Added (capsule-engine#267, evidencebook-skills#79): the admission predicate
  `card.band_history_or_stake`, the per-band ladder. A deal passes when it is within the earned band
  plus `step_decades`, or when a stake on a pinned rail covers it. The earned band is the highest
  decade in the deal's unit with at least `k` corroborated, net-settled subjects and an adverse share
  within `max_adverse_permille`. A row that does not pass limits to the tier its earned history holds.
  `Card.Bands` (new) carries the card's per-band counts. Also added: `TrustRoots.Rails`, adapter
  declarations pinned by `{id, digest}` (AAC#406), and `Stake.Rail`.
- Changed (capsule-engine#267): `stake.covers_deal` counts a stake only on a rail the pack pins in
  `trust_roots.rails`, and any other stake is `not_read` (AAC#406, `NOT_CHECKED`). A pack compiled
  before rails pins none, so it keeps its bytes and digest, but each stake it reads is now `not_read`
  where a covering one used to pass. The change fails closed.
- Added (capsule-engine#265): the package `admission`, the admission evaluator. `Load` and
  `LoadPinned` read an admission pack's `pack-definition.json` (admission pack profile v0,
  evidencebook-skills#51, compiled there from `pack-source.yaml`): only bytes that are exactly
  the JCS of their own parse, decoded strictly. They refuse a pack that breaks the admission
  rules: NO only from platform, own-history and operator rows, a judged row only caps, a
  missing input never defaults to GO, and no field a score could ride in. `Evaluate` gives a
  `Decision` (GO, ASK, LIMIT or NO, the tier, the band, each row with its count and
  denominator, the derived asks), and `Decision.Block` is the `admission_decision` block a
  caller seals. The band `(unit, decade)` is computed exactly from the amount's digits.

- Added (capsule-engine#263): the `required_fields` check (`action_semantics_v0`). An action whose
  verb names one of the pack's action types (`pack.Pack.ActionSemantics`, new) is refused, n/a and
  fails closed, when it lacks a field that type requires. An action whose verb names none gets no
  constraint, so existing decisions keep their bytes. `Action` gains `ModelID` and `Provider`, the
  last normalized fields it did not carry; `NewEngine` refuses a pack requiring a field `Action`
  does not carry.
- Added (capsule-engine#263): the embedded pack `asg/inference/0.1.0` (`packs.Inference010`,
  `pack.LoadInference010`). It cites `rate_cap/1.0.0`, `counterparty_list/1.0.0` and
  `dedupe/1.0.0`, which are now ported. `rate_cap` compares a peer's advertised input and output
  rates (the new `Action` fields `InputMsatPerMillion` and `OutputMsatPerMillion`, in msat per
  million tokens) with two caps. A rate over its cap fails. A missing rate is n/a and fails
  closed, so the action is refused. `counterparty_list` is a deny or allow list. Its
  `disposition: ask` joins the failures that ask an approver.
- Added: `DecodeProfile`, `Profile` and `ProfilePack` read a policy-profile/v0.
  `(*Engine).WithProfile` lays the profile's `rate_cap` caps and counterparty list over the
  pack's wickets. It refuses another pack's values, a check the pack doesn't cite, a rate cap the
  wicket doesn't declare, and caps limits: callers give those through `WithOperatorLimits`.
  `ListEntry` and `ProfileFormat` are exported.

- Changed, breaking (capsule-engine#255, ledger removal step 3): the checks read earlier records
  through a `History` (`Find`, `Get`, `Size`) instead of a `[]Record` ledger. `Engine.Check`,
  `CheckWithTaskAuthority`, `CheckReplay`, `CheckWithRecords` and `CheckSale` take a `History`; a
  nil one holds no records. Each check asks for the records that can bear on it by equality on
  indexed fields (`IndexPaths`), and dedupe also by its window's start; its rule over them is
  unchanged. `MemoryHistory` holds records in memory. `NewStoreHistory` reads a CLL entry log beside
  capsule-emit's artifact store: it declares `IndexPaths` on the store's namespace and returns the
  records the log holds, in log order. A `History` read error is returned by the check, never a
  decision.
- Changed: decisions do not change, with two exceptions. Over `NewStoreHistory`, dedupe does not find a
  record whose timestamp the store cannot read as a time (more than nine fraction digits, or a comma
  before them). Over any history but a check input's (`CheckWithHistory`, which caps still reads
  whole), caps reads only the records under its fold key, as the Python engine does, so an accepted
  copy of an act under another operator or developer no longer keeps that act from counting.
- Changed, breaking: the check-input history struct `History` is renamed `InputHistory`.
- Added: `History`, `Query`, `IndexPaths` and the `Field*` constants, `MemoryHistory`,
  `NewStoreHistory` with `StoreHistory` and `StoreLog`, and `DecodeCapsule`, which reads one stored
  capsule as the checks read it.
- Removed, breaking: `ReadLedger`.
- Added: dependencies on capsule-emit's Go module and checkpointed-local-log's Go module, pinned
  to unreleased revisions until their next releases.

## 0.2.0 — 2026-10-24

A new pack version (a minor on v0, RELEASING.md): everyday 0.3.9 and seller 0.1.6, and the report's
reasons for the action_class_gate rules. The packs and definitions of 0.1.3 are unchanged beyond
the additions below.

- Changed (capsule-engine#234, ruling A on #214 hold A): under a fold that counts executed acts
  (spend.weekly/3.1.0), a check input's history lets a cancel or refund free the headroom of the
  partial charge it cites, as the Python engine does (`historyFreed`). It lowers that charge's
  counted amount by the amount it returns, never below its `spend_minor`; it must state the money
  moved in, cite the charge's capsule id under `supersedes`, carry its operator and developer,
  and be sealed between the charge and the check. Any other cancel or refund frees nothing. No
  exported API, pack or definition changes; frozen folds are unchanged.
- Changed (capsule-engine#182): `Report` words an `action_class_gate` rule's reason without a
  selector or an action-class name, as the Python reference does. Rules 11 and 12 read "Asks: first
  time sharing your {class} with {party}", filled from the role and classes `recipient_role`
  recorded; every other gate rule says "This rule does not apply to this action.", "Asks: this kind
  of action needs your approval first." or "The rules do not recognise this kind of action, so it is
  held.". Verdicts, outcomes, evidence digests and sealed reasons do not change.
- Added (capsule-engine#182): packs `asg/everyday/0.3.9` (`LoadEveryday039`, definition digest
  `acb8a0f7073b872e0a1cc367997ace49dd69fd4e63fe60996c328d8b324ac4a7`) and `asg/seller/0.1.6`
  (`LoadSeller016`, `5e112a7f5136e2193adb177605dfad10b7b0753a2de93ed72e446695b7acc56a`): wording
  only (rule 3's statement and the one platform definition in r03 and s01). Every wicket and fold is
  0.3.8's and 0.1.5's. The shared conformance vectors move to them; 0.3.8 and 0.1.5 stay embedded.

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
