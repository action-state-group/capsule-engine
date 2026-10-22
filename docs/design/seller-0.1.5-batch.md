# Design note: the seller 0.1.5 / everyday 0.3.8 batch

Status: draft v1, 2026-10-22, Ethan's agent (session local_f0ad46ac). Reviewers: Codex, a
`claude -p` stand-in for Kimi, Sherlock2 (product rules and wording).

Code read: capsule-engine 8e2f887, capsule-cli 3acdeee (rc19), actionstate-muse 69c759d.

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
port, and Muse vendors the packs only once they are released. The ports are a follow-up lane
once the 0.1.5 / 0.3.8 digests are fixed (actionstate-muse#215, #216, and new issues for the
rest); this batch lands engine and capsule-cli only.

### Order and PRs

| PR | Repo | Items | Depends on |
|---|---|---|---|
| E1 | capsule-engine | #181 class, #179, #186: `seller.recipient_role/1.2.0`, `seller.release_on_acceptance/1.2.0`, seller 0.1.5 opened | none |
| C1 | capsule-cli | #181: emit `pickup_area` | E1's class name |
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
  Every class must be in `release_classes`, now the data classes of `DISCLOSED_CLASSES`
  (`name, phone, email, address, pickup_location, other_contact, pickup_area`; the credential,
  card and id classes are left out, so they still refuse). A share naming no known class fails
  as today.
- `pre_acceptance_classes: [pickup_area]` (#186): a share whose classes are all in it passes to
  a buyer before acceptance (the public area, A2). Anything else needs the acceptance.
- Binds the acceptance the way s11 already does on the live path
  (`single_commitment.py` L202-206): the sale's acceptance is the same record as today, the
  first accepted, non-dry-run `agreement.accept` with the share's `item_ref`
  (`first_sealed_acceptance`), and the share passes when it is in that acceptance's own deal
  (`deal_id` equal, both set). A sale's thread is one buyer's, so "same deal" is "the buyer who
  accepted". The cited-mandate path stays for a caller that cites (cite, same target, verify),
  as in s11.
- No re-verification on the same-deal path, as s11 has none. The history entries are the
  producer's own sealed records on the user's device, the same trust s11 places in them.
- `except_recipient_roles: [platform]` as 1.1.0.

This does not change which record counts as the acceptance, so it needs no ruling (Sherlock2,
A1). It needs no capsulectl or `external-check-input/v0` change: capsulectl already sends the
commit act in `history` with `item_ref` and `deal_id`.

**#181's class.** `pickup_area` joins `DISCLOSED_CLASSES` (engine `guards/action.py`), and
`seller.recipient_role/1.2.0` adds it to `role_classes.platform: [email, pickup_area]`. It is
not an address class anywhere. capsule-cli (C1) adds it to `disclosureClasses`, both schema
copies, PROFILE's class list and `check_profile.py`, the wording key `class.pickup_area`,
`spokenClassOrder` and the skill text. The engine and Muse accept the class before capsulectl
emits it, because one unknown member drops a share's whole class list.

**To verify while building.** The live-history fixtures carry commit acts with no disposition
(`a-commit-again-1900.json`), which reads as an unknown acceptance. Current capsulectl seals
approved commit acts with a disposition (`deal.go` `basisDisposition`); E1 regenerates a seller
live-history fixture from rc19 and tests the share after acceptance on it. And history carries
only acts with an amount (`rules_checker.go` L658): the seller's commit act states its price,
which the fixture confirms.

**Cases.** pickup_area before acceptance passes; pickup_location, phone, email, name and
address before acceptance refuse; each after acceptance in the accepting thread passes; each to
another thread's buyer after acceptance refuses (pickup_area passes there too: it is public);
a share naming an unknown class refuses; a platform share is left to s01.

## 2. #180 and #189: s04 and s10 decide, and the user's own authenticity claim passes

**Bridge (#180).** `_bridge_deal_check` maps `body.representing.class` to
`Action.representation_class` on a represent check. A claim record already reaches the
statement path. Muse's `record.go` gets the same mapping.

**s04's ordered classes (#180).** The engine reads `body.authorized_representation_classes`;
capsulectl seals `body.ordered_representations` in `task-authority/v0`. The sealed name stays.
`promise_requires_approval/1.1.0` adds a config member `authorized_path`
(`[body, ordered_representations]`); 1.0.0 keeps today's constant. The engine's live path must
also pass the envelope's `task_authority_record` to `GuardEngine.check` (today only the
platform e2e script does); the live-history harness and Muse do it. In an x-deal-v0 deal there
is no task-authority record, so only a sealed approval clears s04 there, as now.

**s10's exception (#189, A7).** The ruling: the user's claim may pass, as theirs, marked
unverified; never Muse's own.

- capsule-cli (C2): a represent check may carry `representing.attributed_to: "user"` (closed
  set of one). capsulectl accepts it only when the words say the claim is the user's and
  unverified, by a fixed marker the skill and card use (proposed: the text must contain
  "unverified", case-insensitive). The card reads the words as they will be said.
- Who stands behind the attribution reuses rule 12 instead of a new binding. If the sale's task
  authority orders the class (`ordered_representations` includes `authenticity`), the user
  ordered it. Otherwise capsulectl's own representation rule asks
  (`representation_outside_authority`), and only the user's own approval, in their words,
  authorizes the claim (`authorizeClaim`, approver `user`). An attribution the user never made
  is held at that ask, and a `hold` refuses it.
- Engine (E2): `promise_never/1.1.0` adds `attributed_never: [authenticity]`: a statement in
  that set passes when `Action.statement_attributed_to == "user"`, mapped from
  `representing.attributed_to`. Every other never-class statement fails as 1.0.0.

**Question for Sherlock2 (Q1).** The issue's third case says an attributed claim the user never
made "refuses". Under this design it asks the user, and refuses on their hold, unless the user
ordered the class. Is an ask acceptable there, or must it refuse outright? Refusing outright
needs a record of the user's words to bind (a user `message` ref), which is agent-labelled too
(`from: user`), so it is no stronger than the approval.

**Cases.** unordered warranty asks (s04); ordered warranty passes; the agent's own authenticity
refuses (s10); "Seller says it is authentic (unverified)" attributed, with authenticity ordered,
passes; attributed and not ordered asks, and the user's hold refuses; attributed without the
marker is refused by capsulectl.

## 3. capsule-cli#364 and #193: the proposal an offer answers

**Today.** s06 (`offer_expiry/1.0.1`) needs `proposal_at` on `marketplace.offer`,
`agreement.accept` and `marketplace.sale`. capsulectl sends it only on a seller's commit resting
on an accepted offer, so every offer asks. Nothing records which buyer message an offer answers:
a `message` record has no refs, and an offer check's only ref is `supersedes`.

**Design.**

- capsule-cli (C3): `deal check` takes `answering: <message step id>` on an offer (and on the
  user's acceptance of a buyer's offer). capsulectl checks it names a `message` with
  `from: counterparty` in the same deal, sealed before, and seals a `source` ref from the check
  to it. The checker gets `proposal_at`: that message's sealed `at`.
- A first offer on a thread that has no counterparty message sealed before it gets
  `no_proposal: true` in the envelope instead. An offer naming no `answering` on a thread that
  has buyer messages gets neither, and still fails closed.
- Engine (E3): `offer_expiry/1.1.0` reads `Action.no_proposal` (from the envelope, and derived
  the same way on replay from the bundle's messages): `true` is `n/a`; a missing
  `proposal_at` otherwise fails `expiry_unverified` as today. 1.1.0, not 1.0.2, so it doesn't
  collide with PR #152's 1.0.2 (a semantics-only change). If #152 merges first, 1.1.0 carries
  its ASK_UNVERIFIED semantics too.
- Replay: `_accepted_offer_at` gains the offer's derivation from its `source` ref.

**Cases.** an offer answering a fresh buyer message passes s06; one answering a message over
24 hours old asks; a first offer on a thread with no buyer message is n/a; an offer on a
thread with messages that names none asks; an envelope whose `proposal_at` was dropped asks.

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

**Question for Sherlock2 (Q2).** A seller's commit that states no rail: today s07 is `n/a` on a
missing rail. Checking the rail at commit (U2) suggests a commit with no rail asks. Proposed:
it asks, and the deal skill tells the agent to state the rail on a seller's commit.

**Cases.** cash + in_person at commit passes; cash + shipped asks; an unlisted rail asks at
commit; venmo and zelle pass; no rail at commit asks (if Q2 says so).

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

**Design (#190, A8).** s05 already runs on every offer, acceptance and sale; the listing just
has to count. When a thread opens on a sale whose listing was posted (`post` act sealed), and
again when a post is acted while threads are open, capsulectl seals into each such thread one
agent `claim` per class in `posting.classes`: `source_kind: agent`, `text_commitment` equal to
the posting's, and `authority_basis: [listing_post]` with an `authorized_by` ref to the post's
approval on the sale log. Those claims reach s05 through the existing `deal_claims`, live and
on replay, with no engine, Muse or `external-check-input/v0` change.

**Cases.** a post with no approval is refused at the act; a post check always asks; a post
after the user's "post it" to the shown card passes; edited text asks again; an offer on a
thread whose listing stated condition passes s05; condition stated in chat passes; neither
asks.

## 6. #188: a repeat of the same detail to the same party passes (everyday)

- capsule-cli (C6): a share check carries the rule input `repeat_disclosures`: the classes of
  `disclosing` already told to this same party, from capsulectl's telling memory
  (`dealRecipient.Repeat`). Absent when there is no memory.
- Engine (E5): `action_class_gate/1.2.0` adds `except_repeated: true` to
  `personal_disclosure`: a share whose every disclosed class is in `repeat_disclosures` is not
  matched. A missing or malformed input is not a repeat, so it still asks. everyday 0.3.8 cites
  1.2.0 for r11, r12 and r15, and r11/r12's statements say a repeat passes and a new party or
  class asks.
- The seller side has no r11: capsulectl's first telling asks there, and a repeat never unlocks
  what s13 refuses (Sherlock2). A seller e2e fixture (phone to the accepted buyer asks, then
  passes) lands with the Muse port.

**Cases.** email to merchant M asks; again passes; phone to M asks; email to N asks.

## 7. #191: free shipping the agent adds asks (test only)

An agent's free-shipping pick is `terms.conditions.shipping`, and `materialChanges2` counts
`terms.conditions` as one key, so r09 already asks. C7 pins the three cases through the stub
checker harness (`deal_rule_inputs_test.go` `ruleInputs`). If it shows a gap, the fix is
capsulectl-internal.

## 8. One-way doors

Doors close when published (a pack or capsulectl release), which the coordinator schedules.

| | Door | Items | State |
|---|---|---|---|
| D1 | `pickup_area` disclosure class in x-deal-v0, outside the address classes | #181, #186 | ruled yes (Steven, 10-22) |
| D2 | x-deal-v0: an offer/acceptance check may carry a `source` ref to the buyer `message` it answers; `external-check-input/v0`: `proposal_at` on offer and acceptance checks, and a new `no_proposal: true` member | #364, #193 | to flag |
| D3 | x-deal-v0: a `post` check action (`communication.publish`) with a sealed `posting {text_commitment, classes}`; a sale's log admits check/verdict/approval/action for `post` | #187 | to flag |
| D4 | x-deal-v0: a thread `claim` whose `authority_basis` is `listing_post`, citing the sale's post approval | #190 | to flag |
| D5 | x-deal-v0: `representing.attributed_to: "user"` with a required "unverified" marker | #189 | to flag |
| D6 | x-deal-v0: `terms.handover: in_person \| shipped` | #192 (#184 reuses it) | to flag |
| D7 | x-deal-v0 rule input `repeat_disclosures` | #188 | to flag |
| D8 | New wicket versions: `seller.recipient_role/1.2.0`, `seller.release_on_acceptance/1.2.0`, `promise_requires_approval/1.1.0`, `promise_never/1.1.0`, `offer_expiry/1.1.0`, `seller.destination_rail/1.1.0`, `action_class_gate/1.2.0`; pack digests seller 0.1.5 and everyday 0.3.8 | all | to flag; each implements a ruled or confirmed what |

Not a door: #179 needs no `external-check-input/v0` member (option b); #191 is a test.

## 9. What is not in this batch

- The Muse ports and vendoring (follow-up lane after the release digests).
- #184's payment-received rule (it only reuses D6).
- The authorization-record input for s04 (capsule-cli#252).
