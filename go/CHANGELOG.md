# Changelog: the Go guard module

Each entry's pack and definition lists come from the diff of `packs/*/PROVENANCE.json`
and `portedDefinitions` (`guard/engine.go`).

## Unreleased

- Added (evidencebook-skills#280): the admission predicate `card.disputes_against` (param `max`), the disputes others raised about the counterparty that were decided against it, out of those decided, read from its card; and the row field `on_unread: "record"`, allowed only on that check's `LIMIT` row with no `ask`, which records a verified card with no dispute tally (`reason: no_dispute_tally`) instead of limiting, Sherlock2's interim until evidencebook-skills#281. `Disputes` gains `Decided{Against, Of}` (nil when the caller did not read them, which reads `not_read`, never a pass); `StandardHistoryCounts` and `StandardHistory3Counts` fill it from `disputes.received.by_outcome` (`against`, and `for + against + split + no_finding`), leaving the `raised` side out (AAC standard-history-v0 §5.4). The command's `rows` carry each row's `reason`. The rust crate decides the same shared vectors (`packs/disputes-against`, `packs/disputes-against-strict`, the reader vectors and four loader refusals). No existing decision block changes.

## 0.7.0 — 2026-11-23

A minor on v0 (RELEASING.md): an API addition (`guard.Engine.WithPersonProfile`), a command flag (`guard check --limits`) and a behavior change in `go/referee`'s `VerifyResult` (#319, below); no `go/packs` pack version or ported definition changes since v0.6.0. Cut for the evidencebook-skills badges demo (evidencebook-skills#220), which runs `capsule-engine guard check` with a person's limits. Merged since v0.6.0 that touch `go/`: #319, #320.

- Added (capsule-engine#314, #320): `capsule-engine guard check --limits FILE`, where FILE is a policy-profile/v0 of limits the host says the person set. The chosen pack's caps are applied as person-set limits (`guard.LimitSourceProfile`, finding reason " you set"; a failed one escalates); rate_cap and counterparty_list go through `WithProfile`; entries for other packs are ignored. Library form: `guard.Engine.WithPersonProfile(Profile)`, which reads caps by canonical class and refuses a key or class the pack declares no default for, as Python's `resolve.py` does. The values are limits already in force: the host applies any cooling-off and decides which file is the person's activated profile. A passing result reads the same with or without `--limits`, so the host, not the result, is the record that a person's limits were checked. The result carries no profile digest, because `external-check-result/v0` is closed. This replaces the 0.6.0 note that `guard check` applies the pack's own limits only.

- Changed (capsule-engine#318): `go/referee`'s `VerifyResult` holds the Result document to AAC's Evidence Result v0 schema through `agent-action-capsule/go/evidenceresult.Check` (v0.21.0), not the older revision Python vendors; the embedded copy of that schema and the Go port of Python's cross-element checks are deleted. Newly refused: a claim with `evidence: []`, and an evidence digest with a trailing newline. Newly accepted: the AAC-only members (`applies_from`, `coverage_declaration`, the `out_of_scope` verdict and bucket) when well formed. A Result with a `close` or `calibration` claim is refused as `result_invalid`, because `VerifyResult` carries no cited records. The Python judge's sealed Results are all accepted (six lab-fault scenarios, committed as `conformance-vectors/referee/v0/judge-results.json` and decided by a Go test and by `referee_vectors.py --check`); Python's own check keeps the older revision, so the two receivers now differ on those inputs, pinned by `go_expected` on two shared referee vectors (`scripts/referee_vectors.py`, `KNOWN_GO_DIFFERENCES`).

## 0.6.0 — 2026-11-21

A minor on v0 (RELEASING.md): an API addition (package `go/referee`) and a new command (`capsule-engine guard check`); no `go/packs` pack version or ported definition changes. Cut for evidencebook#624, whose `book referee receive` verb calls `go/referee`. Merged since v0.5.0 that touch `go/`: #313, #316.

- Added (capsule-engine#315, #316): package `go/referee`, a receiving party's check of a referee's sealed Result, ported from Python `capsule_engine.referee.verify_referee_result`. `VerifyResult` checks that the Result capsule verifies, is signed by the referee key both halves' terms name, names exactly the two halves given, and is the Result document it commits to, which verifies as an Evidence Result v0 document against the schema the Python check uses (`https://agentactioncapsule.org/schemas/evidence-result-v0.json` as vendored in `python/capsule_engine/schemas/vendor/`, sha256 `cf46d273e0220a6a6e9cfa4e41b331b6a6a155d053ca81cc33f3c004dfa734a9`, embedded and held byte-equal by a test). `agent-action-capsule/go/evidenceresult` checks a later revision and decides some Results differently, so it is not used; which revision a receiver should hold is capsule-engine#318. `BindOpenings` checks that each half is authentic and each opening recomputes its half's `action_digest`, and that the halves are not signed by one key. Refusals are `*Error` with the nine Python `RefereeError` codes the receive path can raise (`half_unverified`, `opening_mismatch`, `halves_unpaired`, `result_unverified`, `not_named_referee`, `not_a_result`, `other_deal`, `result_mismatch`, `result_invalid`); input on which Python raises a non-`RefereeError` exception (a half with no `capsule_id`, a float in the Result, terms that are not an object) is `not_a_result`, as evidencebook's Goose node maps it. Both Python and Go decide `conformance-vectors/referee/v0` (`scripts/referee_vectors.py`). The `_check_half` terms and operator checks, `_check_pair`'s answer link, `_check_shared`, and sealing are not ported. Makes `agent-action-capsule/go` a direct dependency.
- Added (capsule-engine#313): `capsule-engine guard check [--pack ID]`, a deal profile's rules checker for the embedded packs: one `external-check-input/v0` in, one `external-check-result/v0` out (the guard's per-rule report as findings, caps split per limit, reasons shortened to one clause, `Asks: ` sentences kept whole). The packs and evidencebook's public input and result schemas are embedded. It applies the pack's own limits only: a limit a person sets is not applied by this command, so a host that lets a person set limits applies them itself. `guard/testdata/emit/golden/` now holds this mapping's expected bytes, asserted by a test; four goldens no test asserted are replaced (the old files read `not_evaluable` where the guard, unchanged here, decides `escalate`, and one read a raw reason where the pack's `Asks: ` sentence is kept). Adds the dependency github.com/santhosh-tekuri/jsonschema/v6 (command only). `go/guard` is unchanged.

## 0.5.0 — 2026-11-21

A minor on v0 (RELEASING.md): API additions (`Decision.Admit`, `AdmitRefs`, `Decision.AdmitsDeal`, `Inputs.Pin`, `Inputs.Streams`, `StandardHistory3Counts` and the rest below), new admission predicates and the x-deal-v1 reader; no `go/packs` pack version or ported definition changes. Cut for evidencebook-deal's `deal agree` path, whose hosts run `capsule-engine admission evaluate --admit` (capsule-engine#303). Merged since v0.4.0 that touch `go/`: #300, #302, #305, #306, #310, #311; #307 and #308 change the Python package only.

- Added (capsule-engine#303): `Decision.Admit(AdmitRefs)` writes the decision as the body of an x-deal-v1 `admit` record for the caller to seal (outcome, tier, every row that applied to the deal's tier, pack digest, and the caller's ask and answer digests, card digest and nonce), refusing a decision without a tier (`ErrNoTier`); `Decision.AdmitsDeal` is set for GO and for LIMIT at a tier that holds the deal's band. `capsule-engine admission evaluate --admit FILE` prints both, drawing the nonce from `crypto/rand`. The rust crate decides the same shared vectors (`admit.json`). No decision block changes.
- Changed (capsule-engine#309): a checked record whose deal profile cannot be read (two profile blocks, an unknown version, or a v1 block that does not name itself) is read from its capsule header alone, as the Python replay reads it, so the engine decides deny, as Python does, instead of deciding on the body it cannot trust (escalate). The four `x-deal-v1-vectors.json:inputs/v1-check-*` known differences are removed. The decision is the engine's; a caller that refuses on `Unevaluable()` is unchanged, and the report adds no deal-conflict row for it.
- Added (capsule-engine#304): the admission predicates `card.witness_label` (records the verifier's label, `witnessed`, `unwitnessed` or `unknown_witness`, on its row), `card.log_key_accepted`, `card.not_behind_pin` and `card.no_dropped_stream`, with `Inputs.Pin`, `Inputs.Streams`, `Card.LogKeyAccepted`, `Card.Checkpoints` and `Receipts.Presented`. The rust crate decides the same shared vectors (`packs/pins`). No existing decision block changes.
- Added (capsule-engine#301): the guard reads x-deal-v1 records (evidencebook v0.18.1) beside x-deal-v0. `profileBlockOf` finds the one profile block; a v1 `perform` or `settle` is read as an act step and `detail_change` as `change`; the journal and journey overlay records that keep v0's bodies (`check`, `claim`, `message` and the rest) read as their v0 namesakes. A v1 `settle` in a history counts at the amount of its capsule's `settlement` member (a payer leg is money out, a payee leg money in, `body.reverses` the act reversed). A settlement amount above 2^53-1 is unreadable (the Python replay holds the same bound). The companion-profile reader (`dealBlock`) reads either version's block, as Python's does; the sale-thread readers stay on x-deal-v0, as Python's do. Fails closed: a payer `settle` with no readable settlement member, and a history act whose record names two profile blocks, an unknown profile version, or a v1 block that does not name itself, is refused at decode; a check record in that state is marked not evaluable. `x-deal-v0` records are read as before.
- Added (capsule-engine#299): the own-settled lift (admission pack profile v0 section 4.1). Go and Rust read an optional row field `lift` `{tier, unless_fail}` on an `own.settled` row whose `on_fail` is `record` (a lifting row decides nothing itself; the loader refuses `lift` on any other row): for a deal at or below the lift tier (never above the pack's top tier) whose lifting row passes, with no `unless_fail` row failing and every card row up to that tier `not_read`, those card rows count as passed (the counterparty served no card), and the lifting row reports effect `lift`. A pack without `lift` decides as before. The shared vectors add admission-muse-buyer@1's fixtures and the lift lint refusals.
- Added (capsule-engine#298): `StandardHistory3Counts` reads the ladder's inputs from a `standard_history/3` card (agent-action-capsule spec/standard-history-v0.md §11, a107d4af). `StandardHistoryCounts` is unchanged for `/1` and refuses a `/3` card by its derivation; `/3` refuses any other token the same way.

## 0.4.0 — 2026-11-17

A minor on v0 (RELEASING.md): API additions (`StoreHistory.Log`/`Artifacts`, `CoverageAsk`, `Card.Stapled`) and a new admission predicate; no pack version or ported definition changes. Cut for the 2026-11-19 Goose ARD demo, which runs `capsule-engine admission evaluate`. Merged since v0.3.0 that touch `go/`: #281, #285, #290, #291; #283, #287, #289, #293 and #295 change the Python package or repository files only.

- Changed (capsule-engine#290): the indirect agent-action-capsule go dependency moves from v0.7.0 to v0.21.0.
- Added (capsule-engine#269): `StoreHistory.Log()` and `StoreHistory.Artifacts()` return the CLL entry log and artifact store the history reads, for read access only (they are the raw stores; an append or put through them skips the history's put-then-append order).
- Added (capsule-engine#284): `InferenceServiceHistoryCountsFor` and `CoverageAsk`, the requester's
  coverage ask for an inference-service-history/0 answer: an `expected_pin`, or a `min_freshness` by
  size (records) or by time. An anchor that does not meet the ask, and an ask that is not exactly one
  of the three, are refused as `anchor_match`. `InferenceServiceHistoryCounts` is unchanged and
  asks by pin. `Card.Controller` is documented as the caller's, already verified against a controller
  binding.

- Added (capsule-engine#277, D-31): the admission predicate `card.not_stapled`, and `Card.Stapled`, which the caller sets for a card that arrived with the holder's own message and was not asked for. The row passes for a verified card that was asked for, fails for a staple, and is not read without a verified card. A pack keeps a staple to the lowest tier with a `from: T1` LIMIT row that carries an `ask`: ASK above T0, or LIMIT at T0 when the ask budget is spent. The Rust evaluator adds `Card::stapled` the same way.

## 0.3.0 — 2026-11-02

A minor on v0 (RELEASING.md): a new pack version, new ported definitions and API changes, some
breaking (the history, `pack.yaml` and `ReadLedger` entries below). New pack version:
`asg/inference/0.1.0` (`packs.Inference010`); the other embedded packs keep their digests. New
ported definitions: `rate_cap/1.0.0` (`a2628f67...13d93`) and `counterparty_list/1.0.0`
(`d9516293...e18f65`). New command: `capsule-engine admission evaluate` (capsule-engine#278), so
evidencebook#332 can pin a go/ tag. Merged since v0.2.0: #256, #263, #264, #265, #266, #267,
#268, #273, #274, #275, #276, #279, #280.

- Added (capsule-engine#276): the node baseline pack `asg/admission_node_baseline/1.0.0`
  (vectors in `conformance-vectors/admission/v0/admission-node-baseline-1.0.0`, source in
  evidencebook-skills), and two predicates. `card.witness_receipts` (param `min`) reads the new
  `Card.Receipts`, the witness receipts the caller counted on the card's checkpoints out of the
  checkpoints. `card.referee_verdicts` (params `window`, `min_periods`) reads the new
  `Served.Verdicts`, the closed periods' `referee_verdicts` cell of one class, `corroborated`
  (dimension `{"verdict": "corroborated"}`, vector-only: ISH SH-12, SH-14), as the served periods
  row reads served totals; a period counts when that cell is published above zero, and the four
  classes are never added to one another. `InferenceServiceHistoryCounts` now fills
  `Verdicts` when the answer declares the unit; an answer that declares only that unit sets
  `VerdictsOnly`. The three counts are never added to one another, and a count the card does not
  carry is not read, so the pack's `missing` decides. Decisions on existing cards keep their bytes.
- Changed, breaking (capsule-engine#252): one pack format. Every embedded pack version now ships
  its definition as `pack-source.yaml` (`scope: {kind: live}`, `pack_version`, and the `check` each
  cited wicket implements), and no longer as `pack.yaml`: code that reads `pack.yaml` out of an
  embedded FS (`packs.Everyday038` and the rest) gets not-exist. Load a pack with `LoadFrom`,
  `LoadPath` or the `LoadXxx` functions, which read `pack-source.yaml`, or pass its bytes to the new
  `LoadSource`; `Load` refuses them. The source-only fields are not in the digest, so every pack
  keeps its digest. A directory holding both `pack-source.yaml` and `pack.yaml` is refused.
  PROVENANCE.json lists `pack-source.yaml`, with the `pack.yaml` sha256 it was converted from.
- Deprecated (capsule-engine#252): `Load` and reading a `pack.yaml` directory through `LoadFrom`
  and `LoadPath`, for packs authored outside this module; removed in v0.4.0.

- Added (capsule-engine#273): `InferenceServiceHistoryCounts` reads a Mesh node's
  `inference-service-history/0` Standard answer as `Card.Served`. The answer is the node's own
  count of the exchanges it served in each closed period. The function takes the definition
  document, the artifact, the checkpoints (time and records covered) the caller read from the
  embedded `history_card/1`, and the requester's `expected_pin`. The periods start at the first
  checkpoint that commits a record (ISH §6.3). It checks each History property it can
  compute from those bytes: `definition_form`, `definition_binding`, `artifact_form`, the coverage
  declaration, `units`, `labels`, `period_alignment`, `period_anchor`, `period_floor`, and that the
  answer's anchor is the pin (`anchor_match`). A refusal is an `*InferenceServiceHistoryError` naming the property
  that fails, so the card is `CardUnverifiable`; an unknown profile or a Detailed definition is
  refused the same way. `capsule-engine admission evaluate` (#278) reads `card.reason` and `card.served` in its input. `Card.Reason` (new) carries that property into the `card.verified` row,
  which records only a name from `InferenceServiceHistoryProperties`, never the detail.
  Also added: the predicate `card.self_reported_served_periods` (params `window`, `min_periods`). It
  counts the closed periods whose served total is published. A suppressed total, or a period the
  answer does not list, is not counted, and is never a fail on its own. The row never adds counts
  across periods or models. Its block entry carries `label: "self_reported"` and `not_counted` by
  reason. A decision on such a card records `coverage` with population `NOT_CHECKED`. Decisions on
  other cards keep their bytes. Every count stays the node's own statement, checked property by
  property; nothing here scores or ranks a node.
- Added (capsule-engine#278): the command `capsule-engine admission evaluate`, for callers that are
  not Go (the Python evidence facade, evidencebook#332), and `admission.DecodeInputs`, the JSON form
  of `Inputs` that the conformance vectors and the command share. The command reads a pack and the
  inputs as JSON and prints the decision as one JSON object, with its decision block and that
  block's SHA-256. It decides the shared vectors as `Evaluate` does. See README.
- Added (evidencebook-skills#87): `StandardHistoryCounts` reads a verified `standard_history/1`
  card's bands and received disputes as `Card.Bands` and `Card.Disputes`. A band's subjects are its
  decided deals (`held` plus `did_not_hold`), and its adverse count is `did_not_hold`; a null-decade
  band is skipped. The card's received disputes are read as `Disputes.Open` out of `Disputes.Of`.
  Also added: the predicate `card.open_disputes` (param `max`), which shows the open-dispute count
  beside the ladder and never decides NO.
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
