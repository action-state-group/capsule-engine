# Design note: the seller 0.1.5 / everyday 0.3.8 batch

Status: draft v3, 2026-10-22, Ethan's agent (session local_f0ad46ac). Reviewers: Codex, a
`claude -p` stand-in for Kimi, Sherlock2 (product rules and wording).

Code read: capsule-engine 8e2f887, capsule-cli 3acdeee (rc19), actionstate-muse 69c759d.

**v2.** Takes Codex's round 1 (3 blockers, 6 should-fix) and Sherlock2's answers to the six
what-points
(buzz://message?channel=0d760ea3-529d-4dec-b309-24184967f8e0&id=0ca2b984901fe877198f925af89d7401ea232b080e4404f225a3834a6a5ad86c).
Changed: §1 binds the deal and the recipient, keeps `home_address`, reads the sale history on
replay, and guards `pickup_area` against an address; §2 makes every attributed claim ask, with a
fixed "(unverified)" ending, and binds the task authority on replay; §3 seals `no_proposal`
instead of deriving it; §4 takes Q2; §5 adds the rules-page line, the re-post rule and the
listing claim's verification chain; §6 measures a new class and a new party apart; §0 states
the release gate.

**v3.** Takes the `claude -p` stand-in's round 1 (2 blockers, 13 should-fix): the listing claims
are salted per thread (§5); the inputs s13 1.2.0 needs, its cases and dropping the cited path
(§1); a list of 0.1.5's `action_semantics` and statement changes (§0); how each new Action field
is sealed (§0); the bridge's effect on 0.1.4 (§2); a fixed attribution template (§2); commit
cases and the typed `source` ref (§3); no rules checker on a sale-level post (§5); r15-r18 pinned
under the new gate (§6); and the Muse ports as release blockers (§0).

Items: capsule-engine #179, #180, #181, #186, #187, #188, #189, #190, #191, #192, #193 and
capsule-cli#364. Each has a How ack, and Sherlock2 confirmed the what
(buzz://message?channel=0d760ea3-529d-4dec-b309-24184967f8e0&id=5b7d03a0409cbea1240a832db50b82976f68207840c54db00caf75f75809362d).
Steven ruled #186-#192 (2026-10-22 03:27 UTC) and #181 (2026-10-22).

## 0. Shape of the batch

Users migrate once: one seller pack 0.1.5 and one everyday pack 0.3.8. No pack or capsulectl
release is cut by this work; the coordinator plans it (rc20 or rc21). Until then the catalog
packs on `main` carry the unreleased 0.1.5 / 0.3.8, and the first PR that touches each pack
freezes the released one under `tests/fixtures/packs/` as #173 did.

Muse (actionstate-muse) is the rules checker users run. Every engine change below needs its Go
port, and Muse vendors the packs only once they are released. This lane lands engine and
capsule-cli on `main`; the Muse ports are their own lane once the 0.1.5 / 0.3.8 digests are
fixed (actionstate-muse#215, #216, and new issues for the rest).

**Release gate.** Merging to `main` changes no user's rules, because nothing is released. The
release that ships seller 0.1.5 / everyday 0.3.8 must ship together: the capsulectl rc that emits
the new members (C1-C6) and the Muse plugin carrying the ports and vendored packs. Shipping one
without the other breaks a case: capsulectl emitting `pickup_area` to a Muse that doesn't know it
drops the share's whole class list (Muse `internal/guard/action.go` L235), and the s10 bridge fix
without C2's attribution would refuse the user's own claim. The batch is done when a Muse seller
e2e on the release candidate passes every case in §1-§6. The Muse ports block the release: the
engine issues stay open until they land.

### What seller 0.1.5 changes besides the wickets

- `action_semantics`: `personal_data.share` requires `target`, `recipient_role` and
  `disclosed_classes` and takes `item_ref` and `deal_id` (no `representation_class`);
  `offer.make` and `offer.accept` take `proposal_at` or `no_proposal`; `offer.accept` (the
  seller's commit, `agreement.accept`) takes `rail` and `handover`; `statement.send` takes
  `statement_attributed_to`.
- Statements rewritten: s04 (ordered classes read from the task authority), s06 (a first offer
  answering no buyer message is not applicable), s07 (runs at commit; cash in person; a missing
  rail asks), s10 (the user's attributed claim), s13 (reads the data classes, binds the deal and
  the recipient, the pickup area before acceptance), s01 (#181, in #195).
- Wording of reasons follows #182 / capsule-cli#341.

### How each new Action field is sealed (AGENTS.md share boundary)

| Field | Sealed in `asg_payload` | Local-only |
|---|---|---|
| `statement_attributed_to` | yes: a closed token about the statement, no party data | no |
| `handover` | yes: a closed token | no |
| `no_proposal` | yes: a flag | no |
| `repeat_disclosures`, `recipient_told_before` | **no**: read from the check body and never re-sealed on the decision, like `spend_authorized_minor`. They describe past tellings to a party, possibly across deals, so they stay off any decision a buyer's copy could carry | n/a |

No field joins `LOCAL_ONLY_PAYLOAD_FIELDS`, and no export path changes.

### Order and PRs

| PR | Repo | Items | Depends on |
|---|---|---|---|
| E1 | capsule-engine | #181 class, #179, #186: `seller.recipient_role/1.2.0`, `seller.release_on_acceptance/1.2.0`, seller 0.1.5 opened | none |
| C1 | capsule-cli | #181: emit `pickup_area` | the release gate (capsule-cli owns the class name) |
| E2 | capsule-engine | #180 + #189 together: the `representing` bridge, `promise_requires_approval/1.1.0`, `promise_never/1.1.0` | E1 (pack open); Steven on door D5 |
| C2 | capsule-cli | #189: `representing.attributed_to` | door D5 |
| E3 | capsule-engine | #193 + #364's replay half: `offer_expiry/1.1.0` | door D2 |
| C3 | capsule-cli | #364: the offer's `source` ref, `proposal_at` and `no_proposal` | door D2 |
| E4 | capsule-engine | #192: `seller.destination_rail/1.1.0`, the `handover` bridge | door D6 |
| C4 | capsule-cli | #192: `terms.handover` | door D6 |
| C5 | capsule-cli | #187 + #190: the listing post on the sale log, listing statements in each thread | doors D3, D4 |
| E5 / C6 | both | #188: `repeat_disclosures`, `action_class_gate/1.2.0`, everyday 0.3.8 | door D7 |
| C7 | capsule-cli | #191: a pinning test | none |

Sherlock2's ordering condition holds by construction: the bridge fix (#180) and the s10
exception (#189) are one PR, E2, so `main` never refuses the user's own authenticity claim.

## 1. #179 and #186: s13 reads the data classes and binds the acceptance from the history

**Today.** s13 (`seller.release_on_acceptance/1.1.0`) keys on `representation_class`, a
statement class, which a share never carries, and needs `cited_mandate_capsule_id`, which no
producer sends. It then re-verifies the cited record with `ledger.verify`, which fails on the
live path: the history ledger holds synthetic records under the original `capsule_id`
(`report/live_history.py` `_act_record`), so verification reports `capsule_id_mismatch`. Muse
has no verifier and ends `not_evaluable`. So no share to a buyer ever passes.

**Decision: option (b).** `seller.release_on_acceptance/1.2.0`:

- Reads `disclosed_classes` (from the share's `disclosing`), never `representation_class`.
  Every class must be in `release_classes`, now the contact classes of `DISCLOSED_CLASSES`
  (`name, phone, email, home_address, address, pickup_location, other_contact, pickup_area`; the
  credential, verification-code, card and id classes are left out, so they still refuse). A
  share naming no known class fails as today.
- `pre_acceptance_classes: [pickup_area]` (#186): a share whose classes are all in it passes to
  a buyer before acceptance (the public area, A2). Anything else needs the acceptance.
- Binds the acceptance from the history: the sale's acceptance is the same record as today, the
  first accepted, non-dry-run `agreement.accept` with the share's `item_ref`
  (`first_sealed_acceptance`), and the share passes only when it is in that acceptance's own deal
  (`deal_id` equal, both set) **and** goes to the same recipient (`target` equal, both set). The
  deal alone is not enough: a share check may name another `who`, and its sealed counterparty
  then differs (`deal_profile.go` L249). In a thread both targets are the thread buyer's
  chain-key fingerprint (`snapshotCounterpartyIDs`), so they match exactly when the share goes
  to the buyer who accepted. A missing target fails.
- Its inputs are `disclosed_classes`, `item_ref`, `deal_id`, `target` and a known acceptance in
  the history, as s11 needs (`single_commitment.py` L181-206); not `task_authority_ref`, which
  only typed records carry. (A sale is always typed: `deal sale new` forces typed records.)
- No cited-mandate path in 1.2.0: no producer cites, and a cited share would fail s12 against
  the synthetic history anyway (`verify_before_dispatch.py` L50).
- No re-verification on the same-deal path, as s11 has none. The history entries are the
  producer's own sealed records on the user's device, the same trust s11 places in them.
- `except_recipient_roles: [platform]` as 1.1.0.

This does not change which record counts as the acceptance, so it needs no ruling (Sherlock2,
A1). It needs no capsulectl or `external-check-input/v0` change: capsulectl already sends the
commit act in `history` with `item_ref`, its deal and its check's counterparty.

**Replay.** The engine gives the replayed sale's history only to s11 today
(`engine.py` `_SALE_HISTORY_CHECK`, L499 and L779). s13 reads it too, with the same
completeness rule (an incomplete or unreadable history is no acceptance), so a replayed share
after acceptance decides as it did live.

**#181's class.** `pickup_area` joins `DISCLOSED_CLASSES` (engine `guards/action.py`), and
`seller.recipient_role/1.2.0` adds it to `role_classes.platform: [email, pickup_area]`. It is
not an address class anywhere. capsule-cli (C1) adds it to `disclosureClasses`, both schema
copies, PROFILE's class list and `check_profile.py`, the wording key `class.pickup_area`,
`spokenClassOrder` and the skill text. Consumers must know the class before capsulectl emits
it (the release gate in §0).

**The area must be an area (Sherlock2, #186).** The agent picks the class label, so an address
labelled `pickup_area` would reach every buyer. Two guards: the deal skill defines
`pickup_area` as a neighborhood, city, region or ZIP, never a street address; and capsulectl
refuses a `pickup_area` share whose value reads as a street address (a house number followed by
words, or a street-type word such as St, Ave, Rd, Blvd, Lane), naming the class
`pickup_location` instead.

**To verify while building.** The live-history fixtures carry commit acts with no disposition
(`a-commit-again-1900.json`), which reads as an unknown acceptance. Current capsulectl seals
approved commit acts with a disposition (`deal.go` `basisDisposition`); E1 regenerates a seller
live-history fixture from rc19 and tests the share after acceptance on it. And history carries
only acts with an amount (`rules_checker.go` L783): the seller's commit act states its price,
which the fixture confirms.

**Cases.** missing or conflicting `deal_id`, an incomplete history, and a commit act with no
disposition (an `agent_card` approval, `deal.go` L478) each refuse; `[pickup_area, phone]` before
acceptance refuses; a share to the accepted buyer after the sale was cut still passes
(Sherlock2 Q7 below); pickup_area before acceptance passes; pickup_location, phone, email, name,
home_address and address before acceptance refuse; each after acceptance in the accepting
thread passes; each to another thread's buyer after acceptance refuses (pickup_area passes
there too: it is public); a share in the accepting thread that names another recipient refuses;
a share naming an unknown class or a credential class refuses; a platform share is left to s01;
a replayed sale bundle decides each as live; capsulectl refuses "12 Oak St" as a pickup_area.

## 2. #180 and #189: s04 and s10 decide, and the user's own authenticity claim passes

**Bridge (#180).** `_bridge_deal_check` maps `body.representing.class` to
`Action.representation_class` on a represent check. A claim record already reaches the
statement path. Muse's `record.go` gets the same mapping.

**s04's ordered classes (#180).** The engine reads `body.authorized_representation_classes`;
capsulectl seals `body.ordered_representations` in `task-authority/v0`. The sealed name stays.
`promise_requires_approval/1.1.0` adds a config member `authorized_path`
(`[body, ordered_representations]`); 1.0.0 keeps today's constant. The engine's live path must
also pass the envelope's `task_authority_record` to `GuardEngine.check` (today only the
platform e2e script does); the live-history harness and Muse do it. Replay does the same: it
resolves the task-authority record the check's `task_authority_ref` names in the bundle, binds
it by digest, and passes it to the check (today no `_decide` branch passes one,
`replay.py` L1342). A missing or mismatched record leaves s04 asking. In an x-deal-v0 deal there
is no task-authority record, so only a sealed approval clears s04 there, as now.

**The bridge and 0.1.4.** The mapping is the bridge's, not a pack's, so once Muse ports it a
profile still pinned to seller 0.1.4 (`promise_never/1.0.0`) refuses every authenticity
statement, the user's own included, and a replay of an older record that passed one now
shows s10 refusing it. That is what 0.1.4's s10 always said; the pass was the bug. The release
moves the pin to 0.1.5 with the Muse plugin, so the window is a profile that keeps 0.1.4 on
purpose. (Sherlock2 Q8 below.)

**s10's exception (#189, A7).** The ruling: the user's claim may pass, as theirs, marked
unverified; never Muse's own.

- capsule-cli (C2): a represent check may carry `representing.attributed_to: "user"` (closed
  set of one), only with class `authenticity`. capsulectl accepts it only when the words follow
  one fixed template: they start "The seller says " and end with the parenthetical
  " (unverified)" right after the claim, e.g. "The seller says it's authentic (unverified)". A
  bare "(unverified)" doesn't show whose claim it is (Sherlock2 Q9 below confirms the opening).
- Engine (E2): when the envelope gives `representing_opening` and it verifies against the
  commitment, `promise_never/1.1.0` also checks the template; a mismatch fails. Without an
  opening it relies on capsulectl's check.
- Every attributed claim asks, whatever the task authority orders: a class the user ordered
  says what may be said, not that the user made this claim. capsulectl adds its own
  `attributed_claim` difference to the check. The card quotes the exact words and says they will
  go out as the user's claim, and only the user's own approval, in their words, authorizes the
  claim (`authorizeClaim`, approver `user`; never `agent_card` or `standing_intent`). An
  attribution the user never made is held at that ask, and their `hold` refuses it (Sherlock2
  accepted ask-then-hold for the third case; #189's case text is updated to match).
- Engine (E2): `promise_never/1.1.0` adds `attributed_never: [authenticity]`: a statement in
  that set passes when `Action.statement_attributed_to == "user"`, mapped from
  `representing.attributed_to`. Every other never-class statement fails as 1.0.0.

**Cases.** unordered warranty asks (s04); ordered warranty passes, live and on replay; a missing
or mismatched task authority asks; the agent's own authenticity refuses (s10); "Seller says it's
authentic (unverified)" attributed passes s10 and asks in capsulectl, then the user's approval
in their words authorizes the claim and an `agent_card` answer doesn't; the user's hold refuses;
attributed words without the ending, or with "unverified" elsewhere ("verified, not
unverified"), are refused by capsulectl; an attribution sealed on a non-authenticity class is
refused by capsulectl; authenticity ordered but not attributed still refuses (s10); an opening
that breaks the template fails s10; in an x-deal-v0 seller deal (no task-authority record) an
ordered warranty asks on s04 where today s04 is n/a (Sherlock2 Q10 below).

## 3. capsule-cli#364 and #193: the proposal an offer answers

**Today.** s06 (`offer_expiry/1.0.1`) needs `proposal_at` on `marketplace.offer`,
`agreement.accept` and `marketplace.sale`. capsulectl sends it only on a seller's commit resting
on an accepted offer, so every offer asks. Nothing records which buyer message an offer answers:
a `message` record has no refs, and an offer check's only ref is `supersedes`.

**Design.**

- capsule-cli (C3): `deal check` takes `answering: <message step id>` on an offer, and on a
  commit that accepts a buyer's own offer message. capsulectl checks it names a `message` with
  `from: counterparty` in the same deal, sealed before the check, and seals a `source` ref from
  the check to it (copied into the typed `proposed-action-v0` refs, `action_records.go`
  L190-196, which gives `source` a new meaning there: part of D2). The checker gets
  `proposal_at`: that message's sealed `at`. A commit resting on the agent's own accepted offer
  keeps today's `acceptedProposalAt`; `answering` there is refused.
- `no_proposal` rests on the agent noting each buyer message (`deal note --kind message`), which
  only the agent can do; the deal skill requires it before an offer.
- A first offer on a thread that has no counterparty message sealed before it carries the sealed
  rule input `no_proposal: true` instead: capsulectl decides it from its own whole log and seals
  it on the check, so a reader never derives it from what a bundle happens to show (a bundle may
  withhold a message's disclosure, `sale_bundle.py` L494). An offer naming no `answering` on a
  thread that has buyer messages gets neither, and still fails closed. capsulectl never seals
  both a `source` and `no_proposal`.
- Engine (E3): `offer_expiry/1.1.0` reads `Action.no_proposal`, bridged from the sealed check
  body: `true` with no `proposal_at` is `n/a`; `true` beside a `proposal_at` fails; a missing
  `proposal_at` otherwise fails `expiry_unverified` as today. On replay, `proposal_at` comes
  from the offer's `source` message; a withheld or unbound message leaves it unknown, so s06
  fails closed. 1.1.0, not 1.0.2, so it doesn't
  collide with PR #152's 1.0.2 (a semantics-only change). If #152 merges first, 1.1.0 carries
  its ASK_UNVERIFIED semantics too.
- Replay: `_accepted_offer_at` gains the offer's derivation from its `source` ref.

**Cases.** an offer answering a fresh buyer message passes s06; one answering a message over
24 hours old asks; a first offer on a thread with no buyer message is n/a, live and on replay;
an offer on a thread with messages that names none asks; an envelope whose `proposal_at` was
dropped asks; a replay whose `source` message is withheld asks; `no_proposal` beside a
`proposal_at` asks; capsulectl refuses `answering` naming a user's or the agent's message,
one in another deal, or one sealed after the check; a commit on the agent's accepted offer is
unchanged; a commit answering a buyer's offer message passes or asks by its age; a commit with
neither asks. Replay limit: `_accepted_offer_at` reads typed records only (`replay.py`
L888-905), so a non-sale x-deal-v0 seller offer stays unestablished on replay and asks.
PR #152 (`offer_expiry/1.0.2`, semantics only) is another lane's: if it merges first, 1.1.0
carries its ASK_UNVERIFIED semantics too.

## 4. #192: cash at in-person pickup, rail checked at commit

- capsule-cli (C4): `terms.handover`, a closed member of `$defs/terms`: `in_person | shipped`,
  stated on a seller's commit and on a sale. It is not added to the material-field count (that
  would move `material_fields_basis` for every check).
- Engine (E4): `seller.destination_rail/1.1.0`: `action_classes: [agreement.accept,
  marketplace.sale]`, `allowed_rails: [card, p2p]`, `in_person_rails: [cash]` (allowed only with
  `handover: in_person`), and `rail_aliases: {venmo: p2p, zelle: p2p, cash_app: p2p, paypal:
  p2p}`. The aliases live in the wicket's config, so a sealed `rail` token keeps its meaning.
  The bridge maps `terms.handover` to `Action.handover`.
- s07 stays ASK (Sherlock2).

- A seller's commit that states no rail asks (Sherlock2, Q2), with the reason "payment method
  not stated"; `seller.destination_rail/1.1.0` sets `missing_rail: fail` for
  `agreement.accept`. Cash with no `handover` asks too. The deal skill tells the agent to state
  the rail and the handover on a seller's commit.

**Cases.** cash + in_person at commit passes; cash + shipped asks; cash with no handover asks;
an unlisted rail asks at commit; venmo and zelle pass, at commit and on `marketplace.sale`; no
rail at commit asks and names the gap; the 1.0.0 cases on `marketplace.sale` decide as before;
capsulectl refuses `handover` on a buyer's deal (it is a seller's term).

#184 (payment received before handover) reuses `terms.handover`, nothing else here.

## 5. #187 and #190: the listing post, and the listing's statements

**Today.** A listing is sale-level, before any buyer thread, and a sale's log holds only the
sale, its task authority, thread records, cuts and the close. No check runs at sale level:
`runDeal` refuses a `sale-…` id.

**Design (#187, A3).**

- capsule-cli (C5): `deal check --deal sale-… ` with `action: post` and
  `posting: {text, classes}` (`classes`: the representation classes the listing text states,
  such as `condition` and `price`). The check seals `posting.text_commitment` and
  `posting.classes`, classed `communication.publish`. The sale's log admits `check`,
  `verdict`, `approval` and `action` records for `post` only.
- The ask is capsulectl's own and unconditional: every `post` check pauses with a
  `public_post` difference, and only the user's own approval (`approver: user`, `--said`, on
  the shown card, which quotes the exact text) authorizes the `post` act. A "post it" typed
  after seeing that card is that approval. Edited text is a new check, with a new card, and asks
  again. This does not depend on a rules checker being pinned.
- No seller pack rule. Citing `action_class_gate/1.1.0` would bring back its
  `personal_disclosure` selector, which refuses every share (why 0.1.1 dropped the gate). A
  seller-only gate would add a second mechanism for an ask that is already unconditional.
- The rules page can't be silent about it (Sherlock2): Muse's rules page shows one fixed line
  outside the pack rules, "Posting a listing always asks you first (built into capsulectl)."
  That line is Muse presentation, in the Muse lane.
- A sale-level `post` check calls no rules checker: no pack rule reads it, and `rulesInput`
  assumes a thread's opening (`events[0].Event.Open.ItemRef`). The verdict records the rules as
  `not_configured` for a post. A test pins a post with a checker pinned.

**Design (#190, A8).** s05 already runs on every offer, acceptance and sale; the listing just
has to count. When a thread opens on a sale whose listing was posted (`post` act sealed), and
again when a post is acted while threads are open, capsulectl seals into each such thread one
agent `claim` per class in `posting.classes`: `source_kind: agent`, its own `text_commitment` (the listing text committed again under the
thread step's own nonce, as `shareCheckedNonces` re-commits checked words), and
`authority_basis: [listing_post]` with `listing_post_commitment`: the post act's record digest,
committed under the thread step's nonce, as `sale_authority_commitment` commits to the sale's
task authority. No value is equal across two threads of one sale, which the sale design
requires (`action_records.go` L263-266). Those claims reach s05 through the existing
`deal_claims`.

- **A re-post replaces.** Only the latest acted post's listing claims count: a re-post with
  edited text seals new listing claims, and capsulectl's `deal_claims` leaves out every listing
  claim whose post is not the latest (Sherlock2: a condition dropped from the new text must not
  still satisfy s05). Replay applies the same rule, which is an engine change in the statement
  path (`replay.py` `statement_record`). A claim sealed at post time covers only checks after it.
- **Verification chain.** With the openings (the user's own copy, or a sale bundle carrying
  them), `check_profile.py` verifies a listing claim the way it verifies a represent claim
  (L993): `listing_post_commitment` opens to a `post` action on the thread's own sale's log (the
  sale its task authority's `sale_registration` names), authorized by a user approval; the
  claim's class is in that post's `posting.classes`; its text opens to the posting's text. A
  thread exported alone, or without the openings, reports the listing basis as not checkable,
  never as verified.
- **Contract.** `authority_basis` gains `listing_post` in x-deal-v0 and in
  `external-check-input/v0`'s description of `deal_claims` (L178), so a checker reading it knows
  the layer. That is door D4.

**Cases.** a post with no approval is refused at the act; a post check always asks; a post
after the user's "post it" to the shown card passes; edited text asks again; an offer on a
thread whose listing stated condition passes s05, live and on replay; after a re-post without
the condition it asks; condition stated in chat passes; neither asks; `check_profile.py`
rejects a listing claim citing another sale's post, an unacted or unapproved post, or a class
or text the post didn't state; no value is equal across two threads' listing claims.

## 6. #188: a repeat of the same detail to the same party passes (everyday)

- capsule-cli (C6): a share check carries two rule inputs from capsulectl's telling memory,
  which already measures both apart (`deal_rules.go` L1440-1472): `repeat_disclosures`, the
  classes of `disclosing` already told to this same party (`dealRecipient.Repeat`), and
  `recipient_told_before: true` when this party was told anything before (`!FirstTime`). Both
  are absent when there is no memory.
- Engine (E5): `action_class_gate/1.2.0` splits `personal_disclosure` into two selectors with
  the same match plus one exception each: `personal_disclosure_new_class` (r11) is not matched
  when every disclosed class is in `repeat_disclosures`; `personal_disclosure_new_party` (r12)
  is not matched when `recipient_told_before` is true. A missing or malformed input is no
  exception, so it still asks. everyday 0.3.8 cites 1.2.0, and r11/r12's statements say what each
  now measures. A pack binds one gate version for all its gate rules, so r15-r18 move to 1.2.0
  too, with the same selectors; a test pins that their outcomes don't change.
- The seller side has no r11: capsulectl's first telling asks there, and a repeat never unlocks
  what s13 refuses (Sherlock2). A seller e2e fixture (phone to the accepted buyer asks, then
  passes) lands with the Muse port.

**Cases.** email to merchant M asks (r11 and r12); again passes; phone to M asks on r11 alone;
email to N asks (r11 and r12); a missing input asks on both. Each case asserts which rules fail,
not only the outcome.

## 7. #191: free shipping the agent adds asks (test only)

An agent's free-shipping pick is `terms.conditions.shipping`, and `materialChanges2` counts
`terms.conditions` as one key, so r09 already asks. C7 (capsule-cli#379) pins the three cases
through the stub checker harness and probes both gaps from the How ack: a condition first carried
on an approved share counts 1 at that share (so r09 asks there), and free shipping that is only
a pick still counts. No code change. One thing it found for Sherlock2 (Q11 below): when r09
asks at a share, the share card doesn't name the added condition, so the user approves a card
that doesn't show it, and the condition is then agreed.

## 7a. Open what-questions for Sherlock2 (round 2)

- Q7 (§1): a share to the accepted buyer after the sale is cut or the buyer backs out still
  passes, since the acceptance still counts. Intended?
- Q8 (§2): a profile kept on 0.1.4 refuses the user's own authenticity claim once the Muse
  bridge ships. Accept that window (the release moves the pin), or hold the mapping to 0.1.5?
- Q9 (§2): the attribution template "The seller says … (unverified)". Right words?
- Q10 (§2): in a non-sale x-deal-v0 seller deal, an ordered warranty will ask on s04 (today n/a).
  Accept?
- Q11 (§7): when an agent-added condition rides on a share and r09 asks there, should the share
  card name the condition? (A capsulectl card change, not in this batch unless you say so.)

## 8. One-way doors

Doors close when published (a pack or capsulectl release), which the coordinator schedules.

| | Door | Items | State |
|---|---|---|---|
| D1 | `pickup_area` disclosure class in x-deal-v0, outside the address classes | #181, #186 | ruled yes (Steven, 10-22) |
| D2 | x-deal-v0 and typed `proposed-action-v0`: an offer/commit check may carry a `source` ref to the buyer `message` it answers, and the sealed rule input `no_proposal: true`; `external-check-input/v0`: `proposal_at` on offer and commit checks answering a message | #364, #193 | to flag |
| D3 | x-deal-v0: a `post` check action (`communication.publish`) with a sealed `posting {text_commitment, classes}`; a sale's log admits check/verdict/approval/action for `post` | #187 | to flag |
| D4 | x-deal-v0 and `external-check-input/v0`: a thread `claim` whose `authority_basis` is `listing_post`, with a per-thread `listing_post_commitment` to the sale's post; only the latest post's listing claims count | #190 | to flag |
| D5 | x-deal-v0: `representing.attributed_to: "user"` on authenticity, words in the template "The seller says … (unverified)"; every attributed claim asks the user | #189 | to flag |
| D6 | x-deal-v0: `terms.handover: in_person \| shipped` | #192 (#184 reuses it) | to flag |
| D7 | x-deal-v0 rule inputs `repeat_disclosures` and `recipient_told_before` | #188 | to flag |
| D8 | New wicket versions: `seller.recipient_role/1.2.0`, `seller.release_on_acceptance/1.2.0`, `promise_requires_approval/1.1.0`, `promise_never/1.1.0`, `offer_expiry/1.1.0`, `seller.destination_rail/1.1.0`, `action_class_gate/1.2.0`; pack digests seller 0.1.5 and everyday 0.3.8 | all | to flag; each implements a ruled or confirmed what |

Not a door: #179 needs no `external-check-input/v0` member (option b); #191 is a test.

## 9. What is not in this batch

- The Muse ports and vendoring (follow-up lane after the release digests).
- #184's payment-received rule (it only reuses D6).
- The authorization-record input for s04 (capsule-cli#252).
