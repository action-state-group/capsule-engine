# Design note: the seller 0.1.5 / everyday 0.3.8 batch

Status: draft v8, 2026-10-22, Ethan's agent (session local_f0ad46ac). Reviewers: Codex, a
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

**v4.** Takes Codex's round 2 and Sherlock2's round-2 answers
(buzz://message?channel=0d760ea3-529d-4dec-b309-24184967f8e0&id=89ccdbd2370d9885e5edd15f6b7a247f31218d27168a62a966bd4407d8894992).
#190 no longer seals listing claims in each thread: each offer or commit on a sale's thread
carries the sealed rule input `listing_classes`, the classes the listing in force states at that
step, so a re-post replaces by construction and replay reads what was sealed (§5). A listing
can't carry the agent's own authenticity claim (§5). s13 compares the thread-keyed buyer
fingerprint, never the profile-keyed target (§1), and a seller's cancel makes a later share ask
(§1, Q7). `recipient_told_before` comes from the disclosure memory (§6). The commit path keeps
the seller-offer-then-acceptance flow (§3), and `no_proposal` beside any `source` fails (§3).
#191's consent gap is capsule-cli#380, a release blocker in this batch (§7). Q7-Q11 answered.
Also from the `claude -p` round 2: the area guard sits where values exist, the disclosure note
(§1); `proposal_at` is when a message was noted (§3); the post card names its classes (§5);
r01 and r04 move gates too (§6); the rule-input sealing rationale (§0).

**v5.** Takes Codex's round 3 and Sherlock2's round 3
(buzz://message?channel=0d760ea3-529d-4dec-b309-24184967f8e0&id=6a0a8f63eca213d2cabcb558839c74a9d8d043ab1a4d43cf6042d8e6a7019813):
the buyer fingerprint's path through the history and the 31-day window on replay (§1); #380's
change to what "agreed" means is a door, D9 (§7, §8); the listing's cross-log ordering (§5); D2
covers offers only, and a buyer message may carry the platform's own send time (§3, D2); the
refusal texts Sherlock2 asked for (§1, §5); a skill line and an e2e case for a buyer who backs
out in chat (§1).

**v6.** Takes the `claude -p` round 3: the fingerprint match rule (§1); the typed
`proposed-action-v0` members in D4, D5 and D7 (§8); a listing per platform, so two live listings
don't stand in for each other (§5, Q16); a word guard on the listing text, so a mislabel can't
carry an authenticity claim (§5); the area guard's 5-digit and Unicode holes (§1, in
capsule-cli#382); and nits.

**v7.** Takes Codex's round 4: the buyer match is `sameParty`'s own rule, a per-person identifier
on both sides (§1); replay parity is narrowed to what a sale bundle can show (§1); D9 seals the
version that selects the adoption rule (§7, §8); summaries aligned (§0, §2, §3). (The `claude -p`
stand-in's quota ran out briefly; Ethan restored it and it ran round 4 on v7.1.)

**v8.** Takes the `claude -p` round 4: the buyer match compares identity kinds only, never a name
or payee (§1); D3 and D9 name the typed record schemas (§8); nits.


Items: capsule-engine #179, #180, #181, #186, #187, #188, #189, #190, #191, #192, #193 and
capsule-cli#364, plus capsule-cli#379 (#191's test) and capsule-cli#380 (its consent fix). Each has a How ack, and Sherlock2 confirmed the what
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
  `offer.make` takes `proposal_at` or `no_proposal`; `offer.accept` (the seller's commit,
  `agreement.accept`) takes `proposal_at` (the accepted seller offer's time), `rail` and
  `handover`; `statement.send` takes
  `statement_attributed_to`; `offer.make` and `offer.accept` take `listing_classes` (no deal
  record carries `marketplace.sale`, `deal_action_class.go` L30).
- Statements rewritten: s04 (ordered classes read from the task authority), s05 (the listing in
  force counts as stated), s06 (a first offer
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
| `listing_classes` | **no**: read from the check body, never re-sealed on the decision. Class names only, equal across a sale's threads like `recipient_role`, never an identifier | n/a |
| `repeat_disclosures`, `recipient_told_before` | **no**: read from the check body, never re-sealed on the decision, like `spend_authorized_minor` | n/a |
| `thread_counterparty` (§1) | **no**: internal history state beside `act_payload`, never in it | n/a |

The four rule inputs (`repeat_disclosures`, `recipient_told_before`, `listing_classes`,
`no_proposal`) are sealed in the check body, which is in the deal's own exports, as every rule
input is (PROFILE §8). That is acceptable because each is class names or a flag, never an
identifier, and the two telling inputs are only about the party the check names, who was the
one told. Keeping them off the decision payload only avoids a second copy.

No field joins `LOCAL_ONLY_PAYLOAD_FIELDS`, and no export path changes.

### Order and PRs

| PR | Repo | Items | Depends on |
|---|---|---|---|
| E1a | capsule-engine | #181: `pickup_area`, `seller.recipient_role/1.2.0`, seller 0.1.5 opened (capsule-engine#195) | D1 ruled; its D8 wicket implements that ruling |
| E1b | capsule-engine | #179, #186: `seller.release_on_acceptance/1.2.0`, s13's `action_semantics` | D8 |
| C1 | capsule-cli | #181: emit `pickup_area` | the release gate (capsule-cli owns the class name) |
| E2 | capsule-engine | #180 + #189 together: the `representing` bridge, `promise_requires_approval/1.1.0`, `promise_never/1.1.0` | E1a (pack open); Steven on door D5 |
| C2 | capsule-cli | #189: `representing.attributed_to` | door D5 |
| E3 | capsule-engine | #193 + #364's replay half: `offer_expiry/1.1.0` | door D2 |
| C3 | capsule-cli | #364: the offer's `source` ref, `proposal_at` and `no_proposal` | door D2 |
| E4 | capsule-engine | #192: `seller.destination_rail/1.1.0`, the `handover` bridge | door D6 |
| C4 | capsule-cli | #192: `terms.handover` | door D6 |
| C5 | capsule-cli | #187 + #190: the listing post on the sale log, `listing_classes` on each thread's offer and commit | doors D3, D4 |
| E6 | capsule-engine | #190: `required_disclosure/1.2.0` reads `listing_classes` | E1a; door D4 |
| E5 / C6 | both | #188: `repeat_disclosures`, `action_class_gate/1.2.0`, everyday 0.3.8 | door D7 |
| C7 | capsule-cli | #191: a pinning test (capsule-cli#379) | none |
| C8 | capsule-cli | capsule-cli#380: a card names every term a check changes; an approval adopts only what its card named | door D9; blocks the release |

Sherlock2's ordering condition holds at the release: the bridge fix (#180) and the s10
exception (#189) are one PR, E2, and the release gate (below) ships E2 with C2's attribution and
the Muse port, so no released version refuses the user's own authenticity claim. (Between E2
and C2 on `main` it would, which no user runs.)

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
  (`deal_id` equal, both set) **and** goes to the same recipient (the same thread-keyed buyer
  fingerprint, below). The deal alone is not enough: a share check may name another `who`, and its sealed counterparty
  then differs (`deal_profile.go` L249). The comparison is on the thread-keyed counterparty
  fingerprint each check seals (`counterparty_ids` under `hmac-sha256-chain-key`, carried on the
  Action beside the target), never on `target`: a check naming a payee gets a profile-keyed
  companion that replaces the target (`deal_ids.go` L284, `replay.py` L500 and L739), so an
  explicit `who` on one side and the thread default on the other would differ for the same
  buyer. The fingerprint's path: the bridge keeps the check's sealed counterparty
  (`counterparty_ids` + `counterparty_fp_alg`, `replay.py` L498-522) on the Action; the history
  record built for a live check (`live_history.py` `_act_record`) and the replayed sale history
  carry it in their `live_history` block beside `act_payload`, as `thread_counterparty`
  (`{fp_alg, ids}`); and `sale_acceptance` returns it. It is internal history state only: it
  never enters `act_payload` (`capsule.py` L248), so no decision seals it and no export changes.
  The identifier is the whole sealed block, `{fp_alg, ids}`, under `hmac-sha256-chain-key`
  (typed) or `hmac-sha256-deal-key` (x-deal-v0), and both sides must name the same algorithm.
  An explicit `who` seals only the kinds it names (`deal_profile.go` L254-256, L297-307) while
  the thread default seals the opening's (L262-266), so the match is `sameParty`'s per-person
  requirement (`deal_counterparty.go` L72-83) plus a rejection of any conflict: a per-person kind (email, phone, profile id, relay address)
  must match on both sides whenever either side has one; a shared marketplace domain or a name
  is never enough (L20-24); and any identity kind on both sides that differs fails. Identity
  kinds are email, phone, profile id, relay address, and domain where `sameParty` counts it;
  `name` and `payee` are fingerprinted too but are not identities ("one seller can write it many
  ways"), so they are left out of the comparison, and the thread default's `payee := name`
  (`deal_profile.go` L262-266) never refuses the right buyer. A missing
  fingerprint on either side fails. On replay the sale history is rebuilt from the bundle's
  check records, which seal the same block.
- **Only while the deal is live** (Sherlock2, Q7). A closed thread already takes no check
  (`deal.go` L1947). A seller's `cancel` act in a thread whose buyer accepted makes every later
  share in that thread ask: capsulectl adds its own `acceptance_called_off` difference ("the
  buyer's acceptance was called off"). No pack change. A buyer who backs out in chat
  ("never mind") without a close leaves the acceptance live, so the deal skill has the agent
  record the seller's `cancel` when the buyer withdraws (Sherlock2); the seller e2e has that
  case. Not a close: after an offer or commit act, `deal close` refuses `called_off` and
  `not_selected`, and a typed seller thread allows only completed, mismatch, open or
  not_selected outcomes (deal-skill-208 lane).
- Its inputs are `disclosed_classes`, `item_ref`, `deal_id`, the thread-keyed fingerprint and a
  known acceptance in the history; unlike s11 (`single_commitment.py` L178-180), not
  `task_authority_ref`. (A sale is always typed: `deal sale new` forces typed records.)
- No cited-mandate path in 1.2.0: no producer cites, and a cited share would fail s12 against
  the synthetic history anyway (`verify_before_dispatch.py` L50).
- No re-verification on the same-deal path, as s11 has none. The history entries are the
  producer's own sealed records on the user's device, the same trust s11 places in them.
- `except_recipient_roles: [platform]` as 1.1.0.

This does not change which record counts as the acceptance, so it needs no ruling (Sherlock2,
A1). It needs no capsulectl or `external-check-input/v0` change: capsulectl already sends the
commit act in `history` with `item_ref`, its deal and its check's counterparty.

**The 31-day window.** Live, capsulectl sends only the last 31 days of acts
(`rules_checker.go` L779-787), so an older acceptance reads as none and a share refuses. The
refusal says why and what to do (Sherlock2): "the buyer's acceptance is older than 31 days; ask
them to accept again". capsulectl can tell this case from no acceptance at all, because it
holds the whole thread; it adds that reason to the card. Replay reproduces the live window for
s13: it reads the sale history from the 31 days before the share. That is as close to live as a
sale bundle can get, not exact: live history is profile-wide and capped at 1,000 acts, and past
the cap it reads incomplete and the acceptance unknown (`rules_checker.go` L758-795,
`single_commitment.py` L127-128), which a bundle of one sale can't show. So a share refused live
for a truncated history may pass on replay of a complete bundle. That difference is stated and
tested, not hidden. s11's replay is unchanged.

**Replay.** The engine gives the replayed sale's history only to s11 today
(`engine.py` `_SALE_HISTORY_CHECK`, L499 and L779). s13 reads it too, with the same
completeness rule (an incomplete or unreadable history is no acceptance), so a replayed share
after acceptance decides as it did live, within the completeness limit stated above.

**#181's class.** `pickup_area` joins `DISCLOSED_CLASSES` (engine `guards/action.py`), and
`seller.recipient_role/1.2.0` adds it to `role_classes.platform: [email, pickup_area]`. It is
not an address class anywhere. capsule-cli (C1) adds it to `disclosureClasses`, both schema
copies, PROFILE's class list and `check_profile.py`, the wording key `class.pickup_area`,
`spokenClassOrder` and the skill text. Consumers must know the class before capsulectl emits
it (the release gate in §0).

**The area must be an area (Sherlock2, #186).** The agent picks the class label, so an address
labelled `pickup_area` would reach every buyer. Two guards: the deal skill defines
`pickup_area` as a neighborhood, city, region or ZIP, never a street address; and capsulectl
refuses to record a `pickup_area` disclosure whose value reads as a street address, naming the
class `pickup_location` instead. A share check carries class names only (`deal_rules.go`
L248), so the guard sits where the value exists: the disclosure note the agent seals before it
sends (`dealDisclosure.Fields`, `deal_rules.go` ~L2480; `deal.go` ~L1663), on every disclosure
of the class, first or repeat. A value reads as a street address when it holds a house number
(any run of Unicode digits, a 5-digit one included) followed within four words by a
whole-word street type (St, Ave, Rd, Blvd, Ln, Dr, Ct, Way, Pl, Hwy, Pkwy, Cir, Ter, Trl, Sq and
their long forms, with or without a period), or a numbered unit (Apt, Unit, Suite, Ste, #). A ZIP
alone is no street address because no street type follows it; a number after the street word
("St. Louis, Ward 3") doesn't count; "1600 Broadway" with no type is a stated limit (Sherlock2),
and so is a numbered street used as an area ("near 6th St", "5th Ave district"): it is refused,
which fails closed, and the skill steers the agent to a neighborhood or "downtown" instead. "Oak Lane neighborhood", "Austin, TX 78701" and "78701" pass. It is a guard against a
mislabel, not a proof: the skill's definition carries the rest.

**To verify while building.** The live-history fixtures carry commit acts with no disposition
(`a-commit-again-1900.json`), which reads as an unknown acceptance. Current capsulectl seals
approved commit acts with a disposition (`deal.go` `basisDisposition`); E1b regenerates a seller
live-history fixture from rc19 and tests the share after acceptance on it. And history carries
only acts with an amount (`rules_checker.go` L783): the seller's commit act states its price,
which the fixture confirms.

**Cases.** missing or conflicting `deal_id`, an incomplete history, and a commit act with no
disposition (an `agent_card` approval, `deal.go` L478) each refuse; `[pickup_area, phone]` before
acceptance refuses; after a seller's cancel in the thread a share asks; a commit naming the
buyer explicitly and a share using the thread default both pass for the same buyer, and a share
naming another buyer refuses; pickup_area before acceptance passes; pickup_location, phone, email, name,
home_address and address before acceptance refuse; each after acceptance in the accepting
thread passes; each to another thread's buyer after acceptance refuses (pickup_area passes
there too: it is public); a share in the accepting thread that names another recipient refuses;
a share naming an unknown class or a credential class refuses; an acceptance older than the
31-day history window reads as none, so a share refuses with the reason above, live and on
replay; a commit naming the buyer explicitly then a share using the thread default pass through
the reconstructed history, not only on a direct check; the same `profile_id` with a different
`name` passes; a commit whose `who` names a subset of the
opening's kinds that includes a per-person kind matches (a subset of only `domain` or `name`
refuses); one that shares no kind with the share refuses; `{domain, email A}` at
the commit and `{domain, phone B}` on the share refuses, live and on replay; a platform share is left to s01;
a replayed sale bundle decides each as live (within the completeness limit); capsulectl refuses to record "12 Oak St" or
"4 Elm Dr., Austin" as a pickup_area and records "Oak Lane neighborhood".

## 2. #180 and #189: s04 and s10 decide, and the user's own authenticity claim passes

**Bridge (#180).** `_bridge_deal_check` maps `body.representing.class` to
`Action.representation_class` on a represent check. A claim record already reaches the
statement path. Muse's `record.go` gets the same mapping.

**s04's ordered classes (#180).** The engine reads `body.authorized_representation_classes`;
capsulectl seals `body.ordered_representations` in `task-authority/v0`. The sealed name stays.
`promise_requires_approval/1.1.0` adds a config member `authorized_path`
(`[body, ordered_representations]`); 1.0.0 keeps today's constant. The engine has no production live path; its live-history
harness already passes `task_authority_record` (`tests/test_live_history.py` L220), and E2
extends it to read `representing_opening` too. Muse is the live path and does both. Replay does the same: it
resolves the task-authority record the check's `task_authority_ref` names in the bundle, binds
it by digest, and passes it to the check (today no `_decide` branch passes one,
`replay.py` L1342). A missing or mismatched record leaves s04 asking. In an x-deal-v0 deal there
is no task-authority record, so only a sealed approval clears s04 there, as now.

**The bridge and 0.1.4.** The mapping is the bridge's, not a pack's, so once Muse ports it a
profile still pinned to seller 0.1.4 (`promise_never/1.0.0`) refuses every authenticity
statement, the user's own included, and a replay of an older record that passed one now
shows s10 refusing it. That is what 0.1.4's s10 always said; the pass was the bug. The release
moves the pin to 0.1.5 with the Muse plugin, so the window is a profile that keeps 0.1.4 on
purpose. Sherlock2 accepted this on the condition that the 0.1.5 pin and the Muse plugin ship in
one release (Q8), which the release gate in §0 requires.

**s10's exception (#189, A7).** The ruling: the user's claim may pass, as theirs, marked
unverified; never Muse's own.

- capsule-cli (C2): a represent check may carry `representing.attributed_to: "user"` (closed
  set of one), only with class `authenticity`. capsulectl accepts it only when the words follow
  one fixed template: they start "The seller says " and end with the parenthetical
  " (unverified)" right after the claim, e.g. "The seller says it's authentic (unverified)". A
    bare "(unverified)" doesn't show whose claim it is. Sherlock2 confirmed the template (Q9): the
  role, never the person's name, and both the prefix and the suffix are required.
- The template is one sentence: no sentence-ending mark (`.`, `!`, `?`) between the prefix and
  the suffix, so "The seller says it's used. I guarantee it's authentic (unverified)" fails.
- Engine (E2): when the envelope gives `representing_opening` and it opens the commitment,
  `promise_never/1.1.0` also checks the template; a mismatch fails. Without an opening it relies
  on capsulectl's check. Opening a salted commitment reuses the code `price_floor` uses for
  `commercial_bounds_opening`.
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
or mismatched task authority asks; the agent's own authenticity refuses (s10); "The seller says it's
authentic (unverified)" attributed passes s10 and asks in capsulectl, then the user's approval
in their words authorizes the claim and an `agent_card` answer doesn't; the user's hold refuses;
attributed words without the ending, or with "unverified" elsewhere ("verified, not
unverified"), are refused by capsulectl; an attribution sealed on a non-authenticity class is
refused by capsulectl; authenticity ordered but not attributed still refuses (s10); an opening
that breaks the template fails s10; in an x-deal-v0 seller deal (no task-authority record) an ordered warranty asks on s04 where today s04 is n/a (Sherlock2 accepted, Q10), and the card
says why: "no order covers this promise".

## 3. capsule-cli#364 and #193: the proposal an offer answers

**Today.** s06 (`offer_expiry/1.0.1`) needs `proposal_at` on `marketplace.offer`,
`agreement.accept` and `marketplace.sale`. capsulectl sends it only on a seller's commit resting
on an accepted offer, so every offer asks. Nothing records which buyer message an offer answers:
a `message` record has no refs, and an offer check's only ref is `supersedes`.

**Design.**

- capsule-cli (C3): `deal check` takes `answering: <message step id>` on an offer only.
  capsulectl checks it names a `message` with `from: counterparty` in the same deal, sealed
  before the check, and seals a `source` ref from the check to it (copied into the typed
  `proposed-action-v0` refs, `action_records.go` L190-196, which gives `source` a new meaning
  there: part of D2). The checker gets `proposal_at`: that message's `sent_at` when sealed, else its sealed `at`. A commit keeps
  today's path: it is authorized only on the seller's own offer and the buyer's acceptance
  (`authorizeAct` / `offerAccepted`, `deal_rules.go` L1647, L1730), and its `proposal_at` is that
  offer's (`acceptedProposalAt`). A buyer's price in a message is answered with an offer, which
  the buyer then accepts; `answering` on a commit is refused.
- `no_proposal` rests on the agent noting each buyer message (`deal note --kind message`), which
  only the agent can do. And a message's sealed `at` is when it was noted, not when the buyer
  sent it (the body is `{from, content_commitment}`), so a 30-hour-old message noted an hour ago
  reads as fresh. Both are stated limits, and s06's statement names the second. Where it can be fixed it is
  (Sherlock2): a `message` note may carry `sent_at`, the platform's own time for the message
  when the agent can read it, sealed beside the note's `at`, and `proposal_at` is `sent_at` when
  present, else the noted `at`. The deal skill has the agent note each buyer message as it
  arrives.
- An offer (the first or a later one) on a thread that has no counterparty message sealed before it carries the sealed
  rule input `no_proposal: true` instead: capsulectl decides it from its own whole log and seals
  it on the check, so a reader never derives it from what a bundle happens to show (a bundle may
  withhold a message's disclosure, `sale_bundle.py` L494). An offer naming no `answering` on a
  thread that has buyer messages gets neither, and still fails closed. capsulectl never seals
  both a `source` and `no_proposal`.
- Engine (E3): `offer_expiry/1.1.0` reads `Action.no_proposal`, bridged from the sealed check
  body: `true` with no `proposal_at` and no `source` ref is `n/a`; `true` beside a `proposal_at`
  or beside any `source` ref, resolved or not, fails; a missing
  `proposal_at` otherwise fails `expiry_unverified` as today. On replay, `proposal_at` comes
  from the offer's `source` message; a withheld or unbound message leaves it unknown, so s06
  fails closed. 1.1.0, not 1.0.2, so it doesn't collide with PR #152's 1.0.2 (see below).
- Replay: `_accepted_offer_at` gains the offer's derivation from its `source` ref.

**Cases.** an offer answering a fresh buyer message passes s06; one answering a message over
24 hours old asks; a first offer on a thread with no buyer message is n/a, live and on replay;
an offer on a thread with messages that names none asks; an envelope whose `proposal_at` was
dropped asks; a replay whose `source` message is withheld asks; `no_proposal` beside a
`proposal_at` asks; capsulectl refuses `answering` naming a user's or the agent's message,
one in another deal, or one sealed after the check, and refuses `answering` on a commit; a
commit on the agent's accepted offer is unchanged, through the sealed commit act; `no_proposal`
beside a withheld, unbound or resolved `source` asks. Replay limit: `_accepted_offer_at` reads typed records only (`replay.py`
L888-905), so a non-sale x-deal-v0 seller offer stays unestablished on replay and asks.
PR #152 (`offer_expiry/1.0.2`, semantics only) is another lane's and stale since 10-13. Whichever
lands second carries the other: if #152 merges first, 1.1.0 states its ASK_UNVERIFIED semantics;
if 1.1.0 merges first, #152 rebases to describe 1.1.0's cases too.

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
  assumes a thread's opening (`events[0].Event.Open.ItemRef`). Its verdict carries a new rules
  status `not_applicable` (no checker runs on a post, pinned or not), not `not_configured`,
  which means none is pinned (part of D3). A test pins a post with a checker pinned.
- `posting.classes` is the agent's label. The post card names them ("This listing states the
  item's condition and price"), so the user's "post it" covers the labels as well as the text.
  Card copy goes to Sherlock2.

**Listings and authenticity.** A listing is the agent's words to every buyer, so it can't carry
the agent's own authenticity claim, which no approval clears (s10; `promise_never.py` L62).
`posting.classes` is the agent's label, so capsulectl also reads the words: it holds
`posting.text` before committing it, and refuses a post whose text says, outside the §2
template, a word from the authenticity list, whatever the labels say. The list matches stems, any
case (Sherlock2): authentic*, genuine*, legit*, counterfeit*, replica*, fake*, knockoff,
knock-off, certified, original. The refusal names the word that matched and offers both ways
out: the template, or rewording ("comes with its original box" becomes "comes with its box").
capsulectl refuses a `post` whose `posting.classes` include `authenticity` unless the text
carries it in the §2 template ("The seller says … (unverified)"), and the refusal quotes the
template so the agent can fix the text and retry (Sherlock2, Q12); and then the post's
unconditional ask is the user's approval of exactly those words. The same check the represent
path makes on attributed words applies to the template there.

**Design (#190, A8).** s05 already runs on every offer, acceptance and sale; the listing just
has to count. A post names its platform (`posting.platform`, the site's domain, as a share check
names a platform recipient), and a later post on the same platform replaces the earlier one there.
Each offer and commit check on a sale's thread carries the sealed rule input `listing_classes`:
the classes every live listing states, the intersection over the latest `post` act per platform
at the moment of the check. So two listings on two sites never stand in for each other: a buyer
from the site whose listing omits the condition is never counted as told it (Q16 below). It is
`[]` when the listings in force state no class in common, and absent when the sale has no post
act. capsulectl reads it from the sale's log, which it holds
locally. `required_disclosure/1.2.0` counts a required class as stated when it is in
`listing_classes`, beside today's two paths (an earlier statement to the target, an agent claim
in the deal). Because the value is sealed per check:

- **A re-post replaces** (Sherlock2): a later post on the same platform that drops `condition`
  makes later checks carry `listing_classes` without it, so s05 asks unless chat stated it. Checks before the
  re-post keep what they sealed. A chat claim still counts on its own.
- **Replay** reads the sealed value. Nothing is derived from what a bundle shows, and a withheld
  sale log changes nothing.
- **No linkage**: the value is class names only, never a commitment or a reference to the post,
  so nothing equal and identifying crosses a sale's threads.
- **Checkable**: in the user's own copy, `check_profile.py` recomputes it from the sale's log
  (the intersection over the latest post act per platform before the check) only when the order
  is unambiguous: a post act and a
  thread check in the same second, or a sale log that isn't complete, sit in different logs with
  no ordering reference (`sale_bundle.py` L545), so the value is reported as not independently
  established, never as a match or a mismatch. A thread exported alone reports it as a producer
  input, like the other rule inputs (PROFILE §8).
- **No `external-check-input/v0` change**: it is a member of the checked record's body. Its
  meaning is published in PROFILE §8 and the x-deal-v0 schema (door D4).

**Cases.** a post with no approval is refused at the act; a post check always asks; a post
after the user's "post it" to the shown card passes; edited text asks again; a post declaring
authenticity outside the template is refused, and one inside it asks as every post does; an
offer on a thread whose listing stated condition passes s05, live and on replay; after a
re-post without the condition a later offer asks and an earlier one keeps its decision;
condition stated in chat passes; neither asks; `listing_classes` absent (no post) adds nothing;
a re-post and a thread offer in the same second, in both orders, are reported as not
independently established; a re-post that states no class, then an offer, asks; two platforms,
one listing without the condition, then an offer, asks; text saying "genuine" labelled
`[condition]` is refused at the post; "before the check" across the sale log and the thread log
is by sealed `at`, and a tie is not established;
`check_profile.py` flags a `listing_classes` that doesn't match the intersection over the sale's
latest post act per platform; a typed post check and post act, and a typed check sealed at
`rule_inputs_version` 3, each validate against their schemas.

## 6. #188: a repeat of the same detail to the same party passes (everyday)

- capsule-cli (C6): a share check carries two rule inputs from capsulectl's telling memory,
  which already measures both apart (`deal_rules.go` L1440-1472): `repeat_disclosures`, the
  classes of `disclosing` already told to this same party (`dealRecipient.Repeat`), and
  `recipient_told_before: true` when this party was told any class before, in this deal or
  another: `toldBefore` over every class of the disclosure memory (`deal_counterparty.go`
  L220-232). Not `!FirstTime`: that counts earlier dealings, never disclosures, and leaves out
  the current deal (L149, L215). Both are absent when there is no memory.
- Engine (E5): `action_class_gate/1.2.0` splits `personal_disclosure` into two selectors with
  the same match plus one exception each: `personal_disclosure_new_class` (r11) is not matched
  when every disclosed class is in `repeat_disclosures`; `personal_disclosure_new_party` (r12)
  is not matched when `recipient_told_before` is true. A missing or malformed input is no
  exception, so it still asks. everyday 0.3.8 cites 1.2.0, and r11/r12's statements say what each
  now measures. A pack binds one gate version for all its gate rules, so r01, r04 (`non_consequential`) and
  r15-r18 move to 1.2.0 too, with the same selectors; a test pins that their outcomes don't
  change.
- The seller side has no r11: capsulectl's first telling asks there, and a repeat never unlocks
  what s13 refuses (Sherlock2). A seller e2e fixture (phone to the accepted buyer asks, then
  passes) lands with the Muse port.

**Cases.** email to merchant M asks (r11 and r12); again passes, also within the first deal
with M; phone to M asks on r11 alone; email to N asks (r11 and r12); an earlier deal with N that
told N nothing still asks on r12; a missing input asks on both. Each case asserts which rules fail,
not only the outcome.

## 7. #191: free shipping the agent adds asks (test only)

An agent's free-shipping pick is `terms.conditions.shipping`, and `materialChanges2` counts
`terms.conditions` as one key, so r09 already asks. C7 (capsule-cli#379) pins the three cases
through the stub checker harness. It also pins a real gap: a condition riding on a share check
is measured there but r09 doesn't apply to `disclosure.personal`, and approving the share
adopts it, so the pay check counts nothing and nothing ever asks. Sherlock2 ruled this a
consent defect that blocks the 0.1.5 release (Q11): capsule-cli#380 (C8) makes a card name every
term its check changes and an approval adopt only what its card named (a `standing_intent`
approval, which shows no card, adopts no changed term). #379's test flips with it.

This changes PROFILE's definition of what was agreed (§6 rule 13, §8 L758), which #191's How
ack named as a one-way door, so it is D9. Existing records keep their reading: the
new adoption rule applies to a check that seals `rule_inputs_version: 3` in its body, a new
member that names the derivation its counts were made under (today the version is local event
metadata, `deal_rules.go` L686-690, and the body seals only the counts and the basis digest,
`deal_rule_inputs.go` L207-216). A check without it re-derives and verifies as before. In a
deal that mixes versions, each approval adopts by its own check's rule. Tests: an old approval
then a new check; a new `standing_intent` approval then a pay check; both through an exported
`check_profile.py` run. Which terms a card named is recomputed from the check and the agreement in
force, because the card is rendered deterministically from them and the approval commits to that
exact card (`card_commitment`). Tests run share → approval → pay for a user approval and a
`standing_intent`, with a term change and with a recourse change.

## 7a. What-questions, answered

Sherlock2 round 1 (six points) and round 2 (Q7-Q11) are all answered and taken above:
buzz://message?channel=0d760ea3-529d-4dec-b309-24184967f8e0&id=0ca2b984901fe877198f925af89d7401ea232b080e4404f225a3834a6a5ad86c
and buzz://message?channel=0d760ea3-529d-4dec-b309-24184967f8e0&id=89ccdbd2370d9885e5edd15f6b7a247f31218d27168a62a966bd4407d8894992.
Round 3 (Q12-Q15) is answered and taken too:
buzz://message?channel=0d760ea3-529d-4dec-b309-24184967f8e0&id=6a0a8f63eca213d2cabcb558839c74a9d8d043ab1a4d43cf6042d8e6a7019813.
Q12 yes, the refusal quotes the template; Q13 yes, the post card shows the labels and "post it"
covers only those; Q14 yes, failing closed with a reason that says what to do; Q15 a stated limit,
fixed with `sent_at` where the platform shows the time.

Q16 is answered too:
buzz://message?channel=0d760ea3-529d-4dec-b309-24184967f8e0&id=35045a9a987130ccfca447232d3e79ee16911d5b4a1cb0864aaf98f96c3a050b.
The intersection is right; a listing taken down keeps counting until replaced, which fails closed
(a stale listing can only remove classes). Recording a takedown (an `unpost` act) is a follow-up
item, not in this batch; if a thread later records its platform, that platform's latest post
replaces the intersection.

## 8. One-way doors

Doors close when published (a pack or capsulectl release), which the coordinator schedules.

| | Door | Items | State |
|---|---|---|---|
| D1 | `pickup_area` disclosure class in x-deal-v0, outside the address classes | #181, #186 | ruled yes (Steven, 10-22) |
| D2 | x-deal-v0 and typed `proposed-action-v0`: an **offer** check may carry a `source` ref to the buyer `message` it answers, or the sealed rule input `no_proposal: true` (refused on any other action); a `message` note may carry `sent_at`; `external-check-input/v0`: `proposal_at` on an offer check answering a message. Commits keep the accepted seller offer's time | #364, #193 | to flag |
| D3 | x-deal-v0, and the typed records a sale uses (`record-common-v0` `check_action` and `action` enums gain `post`; `proposed-action-v0` body gains `posting`): a `post` check action (`communication.publish`) with a sealed `posting {text_commitment, classes, platform}`; a sale's log admits check/verdict/approval/action for `post`; a verdict rules status `not_applicable` | #187 | to flag |
| D4 | x-deal-v0 and typed `proposed-action-v0` rule input `listing_classes` on a sale thread's offer and commit checks: the classes every live listing states; `posting.platform` on a post | #190 | to flag |
| D5 | x-deal-v0 and typed `proposed-action-v0`: `representing.attributed_to: "user"` on authenticity, words in the template "The seller says … (unverified)"; every attributed claim asks the user | #189 | to flag |
| D6 | x-deal-v0: `terms.handover: in_person \| shipped` | #192 (#184 reuses it) | to flag |
| D7 | x-deal-v0 and typed `proposed-action-v0` rule inputs `repeat_disclosures` and `recipient_told_before` | #188 | to flag |
| D9 | PROFILE §6 rule 13 / §8: an approval agrees only to the terms its card named; an approval with no card agrees to no changed term. A new sealed check member `rule_inputs_version` (3), in the x-deal-v0 check body and the typed `proposed-action-v0` body, selects the rule; checks without it re-derive unchanged | capsule-cli#380 | to flag |
| D8 | New wicket versions: `seller.recipient_role/1.2.0`, `seller.release_on_acceptance/1.2.0`, `promise_requires_approval/1.1.0`, `promise_never/1.1.0`, `offer_expiry/1.1.0`, `seller.destination_rail/1.1.0`, `action_class_gate/1.2.0`, `required_disclosure/1.2.0`; pack digests seller 0.1.5 and everyday 0.3.8 | all | to flag; each implements a ruled or confirmed what |

Not a door: #179 needs no `external-check-input/v0` member (option b); #191 is a test (#380, its
consequence, is D9).

## 9. What is not in this batch

- The Muse ports and vendoring: not in this lane, but in the batch's release gate (§0).
- #184's payment-received rule (it only reuses D6).
- The authorization-record input for s04 (capsule-cli#252).
