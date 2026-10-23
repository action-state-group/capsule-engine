# Design note: the seller 0.1.5 / everyday 0.3.8 batch

Status: v9.1, as built, 2026-10-22. Converged as v8.4 before the code was built (Codex v8, `claude -p` v8, Sherlock2 on every
what-point), Ethan's agent (session local_f0ad46ac). Reviewers: Codex, a `claude -p` stand-in for Kimi, Sherlock2 (product
rules and wording).

Code read: capsule-engine 8e2f887, capsule-cli 3acdeee (rc19), actionstate-muse 69c759d. As built: capsule-engine
0c93a58 and capsule-cli 8eff953 (origin/main after the batch).

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
the seller-offer-then-acceptance flow (§3), and a check carrying `no_proposal` beside a `source` is decided on its source (§3).
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

**v9 (as built).** The batch merged: capsule-engine #195, #197, #206, #203, #201, #202, #204, #205 and
capsule-cli #379, #382, #387, #390, #394, #392, #391, #393, #398, #404 ("Order and PRs" lists the merge
commits). Each section now says what was built; "(as built: …)" marks where the code differs from the
converged design, and "Future work" marks what the design proposed and the batch did not build. §9
collects the follow-ups.

**v9.1.** Takes review round 1 of v9 (Codex, `claude -p`): every future-work item names an open
issue (actionstate-muse#238, #240; capsule-cli#407); the rules-page line's owner is left to
actionstate-muse#233; s13's malformed-block cases and the 31-day card conditions are exact (§1);
the post card copy, `plan_action` and the skeleton wording (§5); the share question's both/all
(§6); the #400 provenance (§9).


Items: capsule-engine #179, #180, #181, #186, #187, #188, #189, #190, #191, #192, #193 and
capsule-cli#364, plus capsule-cli#379 (#191's test) and capsule-cli#380 (its consent fix). Each has a How ack, and Sherlock2 confirmed the what
(buzz://message?channel=0d760ea3-529d-4dec-b309-24184967f8e0&id=5b7d03a0409cbea1240a832db50b82976f68207840c54db00caf75f75809362d).
Steven ruled #186-#192 (2026-10-22 03:27 UTC) and #181 (2026-10-22).

## 0. Shape of the batch

Users migrate once: one seller pack 0.1.5 and one everyday pack 0.3.8. No pack or capsulectl
release is cut by this work; the coordinator plans it (rc20 or rc21). Until then the catalog
packs on `main` carry the unreleased 0.1.5 / 0.3.8, and the first PR that touches each pack
freezes the released one under `python/tests/fixtures/packs/` as #173 did.

As built: no pack release was cut. #195 froze seller 0.1.4 (`c14c8f81…9e5b`) and #203 froze everyday
0.3.7 (`c1cea374…7d4d`). On engine `main` the unreleased packs are seller 0.1.5 at
`81013aedbc3d930dcb9a4871d932373eca9de6968e5cff9fb450158d1fcc576a` (after #209, the last to amend it, which reworded s04's reason in promise_requires_approval/1.1.0's semantics) and
everyday 0.3.8 at `ac7697061fd1c50177000648e2458f6236068679fc67ae924870ee8fb1cd8488` (#203). capsulectl
v0.1.0-rc20 (capsule-cli#395, 401f39b) was cut during the batch and already carries #379 and #382
(`pickup_area`, its guard and its skill text); every later capsule-cli PR here is under Unreleased.

Muse (actionstate-muse) is the rules checker users run. Every engine change below needs its Go
port, and Muse vendors the packs only once they are released. This lane lands engine and
capsule-cli on `main`; the Muse ports are their own lane once the 0.1.5 / 0.3.8 digests are
fixed (actionstate-muse#215, #216, #231; #228, which ported #206 in actionstate-muse#237, merged
as 3fab90b, is closed). Future work: `pickup_area` in Muse, actionstate-muse#238; and, tracked in
actionstate-muse#240, the ports `offer_expiry/1.1.0`, `seller.destination_rail/1.1.0`,
`required_disclosure/1.2.0`, `action_class_gate/1.2.0` and the two promise 1.1.0 wickets if #215
doesn't cover them, vendoring the released packs, and the release e2e.

**Release gate.** Merging to `main` changes no user's rules, because nothing is released. The
release that ships seller 0.1.5 / everyday 0.3.8 must ship together: the capsulectl rc that emits
the new members (C1-C6) and the Muse plugin carrying the ports and vendored packs. Shipping one
without the other breaks a case: capsulectl emitting `pickup_area` to a Muse that doesn't know it
drops the share's whole class list (Muse `internal/guard/action.go` L235), and the s10 bridge fix
without C2's attribution would refuse the user's own claim. The batch is done when a Muse seller
e2e on the release candidate passes every case in §1-§6. The Muse ports block the release. (As
built: the engine issues closed when their engine and capsule-cli halves merged, so the Muse issues
above carry the release blocker. C1 already shipped in rc20, so a Muse without the `pickup_area` port
meets it now: Muse `main` at 3fab90b does not know the class; future work: actionstate-muse#238.)

### What seller 0.1.5 changes besides the wickets

- `action_semantics`: `personal_data.share` requires `target`, `recipient_role` and
  `disclosed_classes` and takes `item_ref` and `deal_id` (no `representation_class`);
  `offer.make` takes `proposal_at` or `no_proposal`; `offer.accept` (the seller's commit,
  `agreement.accept`) requires `proposal_at` (the accepted seller offer's time) and `rail`, and takes
  `handover`; `sale.complete` takes `handover` (as built, #201); `statement.send` takes
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

No field joins `LOCAL_ONLY_PAYLOAD_FIELDS`, and no export path changes. (As built, the table held:
`statement_attributed_to`, `handover` and `no_proposal` are sealed in `asg_payload` only when set and read
back by `from_capsule`; two engine-internal Action fields were added and are never sealed:
`representation_text_commitment`, which an opening is checked against (#205), and
`counterparty_malformed`, which marks a counterparty block out of shape (#197).)

### Order and PRs

| PR | Repo | Items | Depends on | Merged as |
|---|---|---|---|---|
| E1a | capsule-engine | #181: `pickup_area`, `seller.recipient_role/1.2.0`, seller 0.1.5 opened (capsule-engine#195) | D1 ruled; its D8 wicket implements that ruling | [#195](https://github.com/action-state-group/capsule-engine/pull/195), 2a873e6 |
| E1b | capsule-engine | #179, #186: `seller.release_on_acceptance/1.2.0`, s13's `action_semantics` | D8 | [#197](https://github.com/action-state-group/capsule-engine/pull/197), 9066490 |
| E1c | capsule-engine | #200 (found while building E1b): read a thread copy's standalone report in a sale bundle | none; blocks the release | [#206](https://github.com/action-state-group/capsule-engine/pull/206), 0b9a293 |
| C1 | capsule-cli | #181: emit `pickup_area` | the release gate (capsule-cli owns the class name) | [capsule-cli#382](https://github.com/action-state-group/capsule-cli/pull/382), 6210b93 (in rc20) |
| C1b | capsule-cli | #179: the seller's cancel and the 31-day card reason (§1) | E1b | [capsule-cli#390](https://github.com/action-state-group/capsule-cli/pull/390), f4b354f |
| E2 | capsule-engine | #180 + #189 together: the `representing` bridge, `promise_requires_approval/1.1.0`, `promise_never/1.1.0`; #208, replay reads the openings | E1a (pack open); Steven on door D5 | [#205](https://github.com/action-state-group/capsule-engine/pull/205), 0c93a58 |
| C2 | capsule-cli | #189: `representing.attributed_to` | door D5 | [capsule-cli#391](https://github.com/action-state-group/capsule-cli/pull/391), 9eb4b60 |
| E3 | capsule-engine | #193 + #364's replay half: `offer_expiry/1.1.0` | door D2 | [#202](https://github.com/action-state-group/capsule-engine/pull/202), d3066f5 |
| C3 | capsule-cli | #364: the offer's `source` ref, `proposal_at` and `no_proposal` | door D2 | [capsule-cli#392](https://github.com/action-state-group/capsule-cli/pull/392), 3a3358c |
| E4 | capsule-engine | #192: `seller.destination_rail/1.1.0`, the `handover` bridge | door D6 | [#201](https://github.com/action-state-group/capsule-engine/pull/201), 6c5d999 |
| C4 | capsule-cli | #192: `terms.handover` | door D6 | [capsule-cli#394](https://github.com/action-state-group/capsule-cli/pull/394), d536699 |
| C5 | capsule-cli | #187 + #190: the listing post on the sale log, `listing_classes` on each thread's offer and commit | doors D3, D4 | [capsule-cli#398](https://github.com/action-state-group/capsule-cli/pull/398), 8eff953 |
| E6 | capsule-engine | #190: `required_disclosure/1.2.0` reads `listing_classes` | E1a; door D4 | [#204](https://github.com/action-state-group/capsule-engine/pull/204), d336c3a |
| E5 / C6 | both | #188: `repeat_disclosures`, `action_class_gate/1.2.0`, everyday 0.3.8 | door D7 | [#203](https://github.com/action-state-group/capsule-engine/pull/203), b5d2c77; [capsule-cli#393](https://github.com/action-state-group/capsule-cli/pull/393), f14e94c |
| C7 | capsule-cli | #191: a pinning test (capsule-cli#379) | none | [capsule-cli#379](https://github.com/action-state-group/capsule-cli/pull/379), 7511ae3 (in rc20) |
| C8 | capsule-cli | capsule-cli#380: a card names every term a check changes; an approval adopts only what its card named | door D9; blocks the release | [capsule-cli#387](https://github.com/action-state-group/capsule-cli/pull/387), cf1835d |
| CI | capsule-cli | capsule-cli#403: the plugin tests outgrew `go test`'s per-package timeout; also a History privacy test reads field by field | none | [capsule-cli#404](https://github.com/action-state-group/capsule-cli/pull/404), c7c5f6c |

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
  requirement (`deal_counterparty.go` L72-83) plus a rejection of any conflict: at least one per-person kind (email, phone, profile id,
  relay address) present on both sides must match; a shared marketplace domain or a name
  is never enough (L20-24); and any identity kind on both sides that differs fails. Identity
  kinds are the per-person kinds: email, phone, profile id, relay address;
  `name` and `payee` are fingerprinted too but are not identities ("one seller can write it many
  ways"), so they are left out of the comparison, and the thread default's `payee := name`
    (`deal_profile.go` L262-266) never refuses the right buyer. Fallback (v8.2, found while
  building #197): a thread opened on a marketplace may seal only `{domain, name}` for its buyer
  (the skill asks for every identifier the agent can see, and that can be all of them). When
  neither side seals any per-person kind, the identity part passes only if both seal `name`
  and `domain` and both are equal; `deal_id` equality still binds the thread. When either side
  has a per-person kind the rule above applies unchanged, so a per-person kind on one side only,
  or a name-only or domain-only pair, refuses. A missing
  fingerprint on either side fails. On replay the sale history is rebuilt from the bundle's
  check records, which seal the same block. (As built, #197: a counterparty block out of shape on
  the current share, such as `profile_id: null`, fails the share naming `counterparty`, so a dropped
  member never opens the name-and-domain reading. A malformed block on the historical acceptance
  instead loses its buyer block, binds nothing and gets the generic reason below
  (`release_on_acceptance.py` L270-284, and the historical-malformation test). No acceptance, an unknown one, and another deal's or
  buyer's all fail with one reason, "the history given shows no acceptance of this sale by this
  buyer in this deal", so a buyer can't learn that someone else accepted; a share naming no class
  and a never-shared class have their own reasons.)
- **Only while the deal is live** (Sherlock2, Q7). A closed thread already takes no check
  (`deal.go` L1947). A seller's `cancel` act in a thread whose buyer accepted makes every later
  share in that thread ask: capsulectl adds its own `acceptance_called_off` difference ("the
  buyer's acceptance was called off"). No pack change. A buyer who backs out in chat
  ("never mind") without a close leaves the acceptance live, so the deal skill has the agent
  record the seller's `cancel` when the buyer withdraws (Sherlock2); the seller e2e has that
  case. Not a close: after an offer or commit act, `deal close` refuses `called_off` and
  `not_selected`, and a typed seller thread allows only completed, mismatch, open or
  not_selected outcomes (deal-skill-208 lane). (As built, [capsule-cli#390](https://github.com/action-state-group/capsule-cli/pull/390):
  only a recorded `cancel` act calls the acceptance off, and a later acceptance in the same thread ends
  it. A seller's cancel check on a thread with a live acceptance asks "The buyer backed out: record
  their acceptance as called off?". Recording it does not reopen the item to other buyers, since s11
  allows one acceptance; future work: capsule-engine#199.)
- Its inputs are `disclosed_classes`, `item_ref`, `deal_id`, the thread-keyed fingerprint and a
  known acceptance in the history; unlike s11 (`single_commitment.py` L178-180), not
  `task_authority_ref`. (A sale is always typed: `deal sale new` forces typed records.)
- No cited-mandate path in 1.2.0: no producer cites, and a cited share would fail s12 against
  the synthetic history anyway (`verify_before_dispatch.py` L50).
- No re-verification on the same-deal path, as s11 has none. The history entries are the
  producer's own sealed records on the user's device, the same trust s11 places in them.
- `except_recipient_roles: [platform]` as 1.1.0. So s13 applies to a buyer, a third party and the
  user alike, and the never-shared classes go to no one but the platform, where s01 decides (as
  built, #197).

This does not change which record counts as the acceptance, so it needs no ruling (Sherlock2,
A1). It needs no capsulectl or `external-check-input/v0` change: capsulectl already sends the
commit act in `history` with `item_ref`, its deal and its check's counterparty.

**The 31-day window.** Live, capsulectl sends only the last 31 days of acts
(`rules_checker.go` L779-787), so an older acceptance reads as none and a share refuses. The
refusal says why and what to do (Sherlock2): "the buyer's acceptance is older than 31 days; ask
them to accept again". capsulectl can tell this case from no acceptance at all, because it
holds the whole thread; it adds that reason to the card. (As built, capsule-cli#390: card text
only, no new difference, and only for s13's own finding: a share to the thread's buyer naming a
class other than `pickup_area`, with every accepted commit sealed before the cutoff of the
history the checker was given, never on an escalation or another rule's finding. With
`rules_deny` and an s13 `fail`, the card gives `card.why.rules_deny.acceptance_too_old`, "your
rules don't allow this, because the buyer's acceptance is older than {days} days. Ask them to
accept again", shown as "Why I stopped: …". With `rules_not_evaluable`, no deny and an s13 `fail`
or not-evaluable finding, it gives `card.why.acceptance_too_old`, "the buyer's acceptance is older
than {days} days; ask them to accept again". A deny where s13 is only not evaluable gets the
generic deny reason, "your rules don't allow this". `{days}` is 31.) Replay reproduces the live window for
s13: it reads the sale history from the 31 days before the share. That is as close to live as a
sale bundle can get, not exact: live history is profile-wide and capped at 1,000 acts, and past
the cap it reads incomplete and the acceptance unknown (`rules_checker.go` L758-795,
`single_commitment.py` L127-128), which a bundle of one sale can't show. So a share refused live
for a truncated history may pass on replay of a complete bundle. That difference is stated and
tested, not hidden. s11's replay is unchanged.

**Replay.** The engine gives the replayed sale's history only to s11 today
(`engine.py` `_SALE_HISTORY_CHECK`, L499 and L779). s13 reads it too, with the same
completeness rule (an incomplete or unreadable history is no acceptance), so a replayed share
after acceptance decides as it did live, within the completeness limit stated above. (As built,
#197: `GuardEngine.check` takes a separate `release_history`, cut to the 31-day window, while s11
keeps the whole `sale_history`. Outside a sale replay, such as a dry-run report of a deal's own
records, s13 has no counterparty block to read, so every share that needs the acceptance refuses.
A current capsulectl sale bundle replays at all only since
[#206](https://github.com/action-state-group/capsule-engine/pull/206) (#200): its thread copies seal
a standalone `x-deal-report/v0` report, which the sale reader now verifies as capsulectl does and
otherwise reads the thread as incomplete.)

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

As built ([capsule-cli#382](https://github.com/action-state-group/capsule-cli/pull/382),
`readsAsStreetAddress`): the value is NFKC-normalized with format characters removed; a house number
is decimal digits in any script with an optional letter, never an ordinal; the street type must
follow within the next four words (at most three name words between) and before a comma or
semicolon; the types add Loop, Plaza, Alley and Pike; a distance word (blocks, miles, min, km)
ends the street name, and a number after exit, ward or zone is a label. Two drifts from the text
above: an ordinal street passes ("near 5th Ave" is a test row), since "5th" is no house number;
and a number with four or more name words before the type ("7 Old Mill Creek Run Rd") passes, a
stated limit beside "1600 Broadway" and "Hauptstraße 7". Future work: the guard does not yet take
the listing word guard's Unicode hardening (lookalikes and marks), capsule-cli#399.

**To verify while building.** The live-history fixtures carry commit acts with no disposition
(`a-commit-again-1900.json`), which reads as an unknown acceptance. Current capsulectl seals
approved commit acts with a disposition (`deal.go` `basisDisposition`); E1b regenerates a seller
live-history fixture from rc19 and tests the share after acceptance on it. And history carries
only acts with an amount (`rules_checker.go` L783): the seller's commit act states its price,
which the fixture confirms. (As built, #197: the fixture `python/tests/fixtures/release-on-acceptance/` was
built with capsulectl from capsule-cli 6210b93, after rc19; its commit act seals `accept` and the
buyer's block, and the shares decide live with no edit. The 31-day case is tested on moved records,
because capsulectl's deal clock has no override.)

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
statement path. Muse's `record.go` gets the same mapping (future work: actionstate-muse#215).

**s04's ordered classes (#180).** The engine reads `body.authorized_representation_classes`;
capsulectl seals `body.ordered_representations` in `task-authority/v0`. The sealed name stays.
`promise_requires_approval/1.1.0` adds a config member `authorized_path`
(`[body, ordered_representations]`); 1.0.0 keeps today's constant. The engine has no production live path; its live-history
harness already passes `task_authority_record` (`python/tests/test_live_history.py` L220), and E2
extends it to read `representing_opening` too. Muse is the live path and does both. Replay does the same: it
resolves the task-authority record the check's `task_authority_ref` names in the bundle, binds
it by digest, and passes it to the check (today no `_decide` branch passes one,
`replay.py` L1342). A missing or mismatched record leaves s04 asking. In an x-deal-v0 deal there
is no task-authority record, so only a sealed approval clears s04 there, as now. (As built,
[#205](https://github.com/action-state-group/capsule-engine/pull/205): binding the authority on
replay also moves s08 from not applicable to pass on replay, as live. Under 1.1.0 an ordered list
counts only when every member is a known class string. A represent check whose `representing` is
missing or not an object, or whose class is not a non-empty string, gets the class
`malformed_representing`, so s04 and s10 fail instead of reading not applicable.)

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
- The template is one sentence: no sentence mark between the prefix and the suffix, so "The
  seller says it's used. I guarantee it's authentic (unverified)" fails, and the claim between
  them is not blank. As built (#205, capsule-cli#391), the marks are an explicit, frozen list of
  181 code points: every Unicode 17.0 Sentence_Terminal code point, every line terminator
  Python's `str.splitlines` uses, and U+2026. Both repositories hold it in a byte-identical
  fixture (engine `python/tests/fixtures/sentence-marks.txt`, capsule-cli `plugins/internal/plugin/testdata/sentence-marks.txt`,
  sha256 `8869f15b…b9aa`), and each side's test compares its list with the fixture code point for
  code point. A blank claim is judged as Python's `str.strip` judges it on both sides.
- Engine (E2): when the envelope gives `representing_opening` and it opens the commitment,
  `promise_never/1.1.0` also checks the template; a mismatch fails. A live check with no
  opening fails. On replay the engine reads the opening from the deal report's
  `representations` (inline, standalone or in-log) by the commitment it recomputes; with none,
  s10 reads not evaluable ("the words for this attributed claim are not in the bundle"), never
  a fail (v8.4, from #205's review and Sherlock2). Opening a salted commitment reuses the code `price_floor` uses for
  `commercial_bounds_opening`. (As built, [#205](https://github.com/action-state-group/capsule-engine/pull/205),
  closing #208: on replay s10 is n/a in scope, naming `representing_opening`, and fails closed,
  so the sealed verdict is `not_evaluable`, as Sherlock2 accepted. The bridge tells an absent
  `representing.text_commitment` from a malformed one: a malformed commitment fails, live and on
  replay ("the check seals no commitment to the words of this attributed claim"); an absent one
  fails live and is not evaluable on replay, because a decision capsule replayed through
  `Action.from_capsule` seals the attribution but never the commitment. The shared opening code is
  `guards/checks/commitment.py`.)
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
says why: "no order covers this promise". (As built, #205: that reason is s04's own, "no task
authority order or approval of yours covers this promise".)

As built, capsulectl's side ([capsule-cli#391](https://github.com/action-state-group/capsule-cli/pull/391)):
the card asks `Tell {buyer}, as your own claim: "{text}"?` with the reason "these words go out as your
claim, so only you can OK them"; the claim record gains no member, since it binds to the check
through its words; `check_profile.py` refuses an attributed representation without the user's own
approval and a passing verdict on an attributed check; and `attributed_to` on any other class is
refused at the schema stage.

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
  the buyer then accepts; `answering` on a commit is refused. (As built,
  [capsule-cli#392](https://github.com/action-state-group/capsule-cli/pull/392): `answering` is the
  capsule id that `deal note --kind message` returned, and it is refused on every action but an
  offer, before anything is sealed.)
- `no_proposal` rests on the agent noting each buyer message (`deal note --kind message`), which
  only the agent can do. And a message's sealed `at` is when it was noted, not when the buyer
  sent it (the body is `{from, content_commitment}`), so a 30-hour-old message noted an hour ago
  reads as fresh. Both are stated limits, and s06's statement names the second. Where it can be fixed it is
  (Sherlock2): a `message` note may carry `sent_at`, the platform's own time for the message
  when the agent can read it, sealed beside the note's `at`, and `proposal_at` is `sent_at` when
  present, else the noted `at`. The deal skill has the agent note each buyer message as it
  arrives. (As built, capsule-cli#392: `sent_at` is given in RFC 3339 with any offset, sealed in UTC
  at whole seconds, and refused when unreadable or in the future; `check_profile.py` holds it to a
  real UTC time at or before the record's `at`. On replay, #202, a `sent_at` that is malformed or
  later than the message's `at` gives no time and never falls back to `at`.)
- An offer (the first or a later one) on a thread that has no counterparty message sealed before it carries the sealed
  rule input `no_proposal: true` instead: capsulectl decides it from its own whole log and seals
  it on the check, so a reader never derives it from what a bundle happens to show (a bundle may
  withhold a message's disclosure, `sale_bundle.py` L494). An offer naming no `answering` on a
  thread that has buyer messages gets neither, and still fails closed. capsulectl never seals
  both a `source` and `no_proposal`. (As built: a `no_proposal` in the input file is ignored, and the
  schemas allow `no_proposal` only on an offer and never beside a `source` ref.)
- Engine (E3): `offer_expiry/1.1.0` reads `Action.no_proposal`, bridged from the sealed check
  body: `true` with no `proposal_at` and no `source` ref is `n/a`; `true` beside a `proposal_at`
  fails; the bridge carries `no_proposal` only from a check that names no `source` ref, so a
  record carrying both is decided on its source (unresolved: fails; resolved: that message's
  age), never as not applicable (v8.3, from #202's review); a missing
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
if 1.1.0 merges first, #152 rebases to describe 1.1.0's cases too. (As built: 1.1.0 merged first in
#202; #152 is still open, tracked as #158, so the rebase is future work there.)

## 4. #192: cash at in-person pickup, rail checked at commit

- capsule-cli (C4): `terms.handover`, a closed member of `$defs/terms`: `in_person | shipped`,
  stated on a seller's commit and on a sale. It is not added to the material-field count (that
  would move `material_fields_basis` for every check). (As built,
  [capsule-cli#394](https://github.com/action-state-group/capsule-cli/pull/394): a commit seals
  exactly the accepted offer's terms, compared whole, so the handover is stated on the offer and
  repeated on the commit, like the rail. It goes only in a seller's offer or commit check and the
  opening terms of a seller's deal or a sale, and is refused, sealing nothing, on a buyer's deal,
  on any other check, in `asked`, a `detail_change` or a close's `delivered`, and for any value
  but the two (trimmed and lowercased first). PROFILE §6 rule 14; `check_profile.py` enforces it.)
- Engine (E4): `seller.destination_rail/1.1.0`: `action_classes: [agreement.accept,
  marketplace.sale]`, `allowed_rails: [card, p2p]`, `in_person_rails: [cash]` (allowed only with
  `handover: in_person`), and `rail_aliases: {venmo: p2p, zelle: p2p, cash_app: p2p, paypal:
  p2p}`. The aliases live in the wicket's config, so a sealed `rail` token keeps its meaning.
  The bridge maps `terms.handover` to `Action.handover`. (As built,
  [#201](https://github.com/action-state-group/capsule-engine/pull/201): an alias matches exactly;
  a rail that is not a string, such as a foreign capsule's `[]` or `{}`, is counted as itself and
  fails as not an allowed rail, under 1.0.0 and 1.1.0, never n/a; a handover outside the set, or
  not a string, reads as none. Future work: `destination_rail` configured with only
  `watched_rails` still passes a non-string rail, capsule-engine#207, which predates this batch.)
- s07 stays ASK (Sherlock2).

- A seller's commit that states no rail asks (Sherlock2, Q2), with the reason "payment method
  not stated"; `seller.destination_rail/1.1.0` sets `missing_rail: fail` for
  `agreement.accept`. Cash with no `handover` asks too. The deal skill tells the agent to state
  the rail and the handover on a seller's commit (as built: on every offer and on the commit).
  Commits sealed before capsulectl states the rail now ask on s07 under 0.1.5; #201 regenerated the
  real capsulectl fixtures to show it.

**Cases.** cash + in_person at commit passes; cash + shipped asks; cash with no handover asks;
an unlisted rail asks at commit; venmo and zelle pass, at commit and on `marketplace.sale`; no
rail at commit asks and names the gap; the 1.0.0 cases on `marketplace.sale` decide as before;
capsulectl refuses `handover` on a buyer's deal (it is a seller's term).

#184 (payment received before handover) reuses `terms.handover`, nothing else here. Future work:
#184 is open.

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
  again. This does not depend on a rules checker being pinned. (As built,
  [capsule-cli#398](https://github.com/action-state-group/capsule-cli/pull/398): `deal note --deal
  sale-…` takes only `--kind approval` and `--kind act`, and `runDeal` admits a sale id for these two
  commands only. The act is authorized by the user's first answer to the latest post check, unused,
  within `valid_until`, and must carry exactly the checked posting. A newer post proposal supersedes
  every earlier post check, even one whose check never sealed, so an answer sealed after it, and the
  act on it, are refused, by capsulectl and by `check_profile.py`. A closed sale takes no post. The
  post act is sealed `fyi`; no AAC effect type is proposed for it. `posting.platform` is the site's
  registrable domain. The post is classed `communication.publish` with spend 0. A task authority's
  `allowed`, `allowed_actions` and `preconditions` can never name `post` (`record-common-v0`
  `plan_action`). The card copy, from the wording pack at capsule-cli 8eff953: the question
  `Post this listing on {platform}: "{text}"?` with a "Post it" button beside "Hold"; the facts
  line "This listing states the item's {classes}", or, with no class, "This listing doesn't state
  the item's condition, price or anything else your rules track"; and the `public_post` reason
  "posting a listing always asks you first".)
- No seller pack rule. Citing `action_class_gate/1.1.0` would bring back its
  `personal_disclosure` selector, which refuses every share (why 0.1.1 dropped the gate). A
  seller-only gate would add a second mechanism for an ask that is already unconditional.
- The rules page can't be silent about it (Sherlock2): Muse's rules page shows one fixed line
  outside the pack rules, "Posting a listing always asks you first (built into capsulectl)."
  That line is Muse presentation, in the Muse lane. (As built: not built, and its owner is not
  settled. Per actionstate-muse#233, Muse owns its closing `ready.md` message, while the rules page
  itself is built by capsulectl's presentation build, so where the line goes is decided in that
  issue's How ack. Future work: actionstate-muse#233.)
- A sale-level `post` check calls no rules checker: no pack rule reads it, and `rulesInput`
  assumes a thread's opening (`events[0].Event.Open.ItemRef`). Its verdict carries a new rules
  status `not_applicable` (no checker runs on a post, pinned or not), not `not_configured`,
  which means none is pinned (part of D3). A test pins a post with a checker pinned. (As built:
  `deal list`'s `totals.checks.rules` gains `not_applicable`.)
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

As built (capsule-cli#398): the words are spotted on a skeleton of the text: escapes decoded
(`decodeEscapes`), format characters removed, NFKD, every mark (`\p{M}`) dropped, each non-ASCII letter or digit read as its
Unicode TR39 confusable prototype, then lower case; i, l and 1 read as one letter and o and 0 as
one; a vertical stroke (`|` and the non-ASCII characters whose TR39 prototype is l, such as `｜`,
`ǀ` and `│`, except those whose NFKD form is `l`, `I` or `1`, which stay letters so "Originaℓℓy"
passes) is read both as the letter i and as a word boundary, so "AUTHENT|C" and "used|GENU|NE" match;
styled letters ("𝐎𝐫𝐢𝐠𝐢𝐧𝐚𝐥𝐥𝐲") read as their plain forms, so "Originally" passes. Words are whole
words; `knock` and `off` match with any space, dash, math sign, control or underscore between.
The skeleton never grants the exception: the template is matched exactly, case-sensitive, on the
folded text only, with the 181 sentence marks of §2 (one shared variable with the represent path).
`authenticity` is in `posting.classes` exactly when a template sentence claims it: a template
sentence without the label is refused too ("add authenticity to classes and check again"). The
refusal quotes the matched word as written (folded) and offers both fixes, the template "in one
sentence, exactly" or rewording. (In review, not shipped behavior: the reviewers' dictionary
sweeps of about 236k words found no false positive, per capsule-cli#398's review record.)

**Design (#190, A8).** s05 already runs on every offer, acceptance and sale; the listing just
has to count. A post names its platform (`posting.platform`, the site's domain, as a share check
names a platform recipient), and a later post on the same platform replaces the earlier one there.
Each offer and commit check on a sale's thread carries the sealed rule input `listing_classes`:
the classes every live listing states, the intersection over the latest `post` act per platform
at the moment of the check. So two listings on two sites never stand in for each other: a buyer
from the site whose listing omits the condition is never counted as told it (Q16 below). It is
`[]` when the listings in force state no class in common, and absent when the sale has no post
act. capsulectl reads it from the sale's log, which it holds
locally (as built: from the local index, never from the input file, under the store's writer
lock, which every post act is also sealed under, so no post lands between the read and the
check's seal). `required_disclosure/1.2.0` ([#204](https://github.com/action-state-group/capsule-engine/pull/204)) counts a required class as stated when it is in
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
  input, like the other rule inputs (PROFILE §8). (As built: `check_profile.py SALE.json
  THREAD.json` recomputes it when given the sale's log beside its threads.)
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
latest post act per platform; a typed post check and post act validate against their
schemas.

## 6. #188: a repeat of the same detail to the same party passes (everyday)

- capsule-cli (C6): a share check carries two rule inputs from capsulectl's telling memory,
  which already measures both apart (`deal_rules.go` L1440-1472): `repeat_disclosures`, the
  classes of `disclosing` already told to this same party (`dealRecipient.Repeat`), and
  `recipient_told_before: true` when this party was told any class before, in this deal or
  another: `toldBefore` over every class of the disclosure memory (`deal_counterparty.go`
  L220-232). Not `!FirstTime`: that counts earlier dealings, never disclosures, and leaves out
  the current deal (L149, L215). Both are absent when there is no memory. (As built,
  [capsule-cli#393](https://github.com/action-state-group/capsule-cli/pull/393), the half of
  [#203](https://github.com/action-state-group/capsule-engine/pull/203) that seals the inputs: only
  what was given with authority counts as told. A disclosure sealed `authority: none` does not
  count, and a share the user refused seals no disclosure. The filter is in `counterpartyMemory`, so
  it also governs capsulectl's own first-telling pause. Both inputs are also absent when the
  recipient has no identity to remember them by; when known, both are present, `[]` and `false`
  included. The input file cannot set them.)
- As built, a class that went to a recipient before without the user's OK is never called a first
  time (capsule-cli#393, Sherlock2's leak card). The check's difference reads "Your `<class>` already
  went to `<recipient>` without your OK"; the card asks "Your `<class>` already went to
  `<recipient>` without your OK. Share it again?" with the reason "it went out without your OK, so I
  can't count `<recipient>` as already told". When the share gives other classes too, the question
  names every one ("This also shares your email, which they already have, and your name, which
  would be new to them. Share all of them?"): "Share both?" when the share gives two classes, and
  "Share all of them?" for three or more. A held disclosure note says "You haven't OK'd telling
  `<recipient>` your `<class>` before." and then "This also shares …" with the same clauses; a held
  first telling names the other classes the same way.
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
  passes) lands with the Muse port (future work, actionstate-muse#240).

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
`check_profile.py` run; a typed check sealed at `rule_inputs_version` 3 validates against its
schema. Which terms a card named is recomputed from the check and the agreement in
force, because the card is rendered deterministically from them and the approval commits to that
exact card (`card_commitment`). Tests run share → approval → pay for a user approval and a
`standing_intent`, with a term change and with a recourse change.

As built ([capsule-cli#387](https://github.com/action-state-group/capsule-cli/pull/387), closing
capsule-cli#380; capsule-cli#379's adopted-share test flips there): the card gains one line, "Saying yes
also agrees to …" (`card.terms.*`), naming every term the check changes from what was agreed, each
with its "(was …)" where it had a value, from the same comparison `material_fields_changed` counts.
`who.payee` is left out (an approval never adopts who). A deny, a represent check and a pass carry no
line. At the check step `foldDeal` records the proposal the card was rendered from and the keys it
named; an approved proceed adopts a changed key only when the card named it, a named drop included,
while a key the proposal states with no change (a way to pay stated for the first time) is adopted
as before. A wording pack whose `card.terms.*` entry is blank or changes its placeholders is refused
and, if pinned before, not used. `check_profile.py` does not re-derive material counts; it refuses a
check that states `rule_inputs_version` without both `material_fields_changed` and
`material_fields_basis`.

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
item, not in this batch (future work: capsule-cli#384); if a thread later records its platform, that platform's latest post
replaces the intersection.

## 8. One-way doors

Doors close when published (a pack or capsulectl release), which the coordinator schedules.
As built, D1 is already published in capsulectl v0.1.0-rc20 (#382 is in it); the rest are on
`main` only, unreleased.

| | Door | Items | State | Shipped in |
|---|---|---|---|---|
| D1 | `pickup_area` disclosure class in x-deal-v0, outside the address classes | #181, #186 | ruled yes (Steven, 10-22) | capsule-cli#382 (rc20); engine #195 |
| D2 | x-deal-v0 and typed `proposed-action-v0`: an **offer** check may carry a `source` ref to the buyer `message` it answers, or the sealed rule input `no_proposal: true` (refused on any other action); a `message` note may carry `sent_at`; `external-check-input/v0`: `proposal_at` on an offer check answering a message. Commits keep the accepted seller offer's time | #364, #193 | to flag; confirmed in set C (Ethan, 10-22) | capsule-cli#392; engine #202 |
| D3 | x-deal-v0, and the typed records a sale uses (`record-common-v0` `check_action` and `action` enums gain `post`; `proposed-action-v0` body gains `posting`): a `post` check action (`communication.publish`) with a sealed `posting {text_commitment, classes, platform}`; a sale's log admits check/verdict/approval/action for `post`; a verdict rules status `not_applicable` | #187 | to flag; confirmed in set C | capsule-cli#398 |
| D4 | x-deal-v0 and typed `proposed-action-v0` rule input `listing_classes` on a sale thread's offer and commit checks: the classes every live listing states; `posting.platform` on a post | #190 | to flag; confirmed in set C | capsule-cli#398; engine #204 |
| D5 | x-deal-v0 and typed `proposed-action-v0`: `representing.attributed_to: "user"` on authenticity, words in the template "The seller says … (unverified)"; every attributed claim asks the user | #189 | to flag; confirmed in set C | capsule-cli#391; engine #205 |
| D6 | x-deal-v0: `terms.handover: in_person \| shipped` | #192 (#184 reuses it) | to flag; confirmed in set C | capsule-cli#394; engine #201 |
| D7 | x-deal-v0 and typed `proposed-action-v0` rule inputs `repeat_disclosures` and `recipient_told_before` | #188 | to flag; confirmed in set C | capsule-cli#393; engine #203 |
| D9 | PROFILE §6 rule 13 / §8: an approval agrees only to the terms its card named; an approval with no card agrees to no changed term. A new sealed check member `rule_inputs_version` (3), in the x-deal-v0 check body and the typed `proposed-action-v0` body, selects the rule; checks without it re-derive unchanged | capsule-cli#380 | to flag; ruled "ok go" (Steven) | capsule-cli#387 |
| D8 | New wicket versions: `seller.recipient_role/1.2.0`, `seller.release_on_acceptance/1.2.0`, `promise_requires_approval/1.1.0`, `promise_never/1.1.0`, `offer_expiry/1.1.0`, `seller.destination_rail/1.1.0`, `action_class_gate/1.2.0`, `required_disclosure/1.2.0`; pack digests seller 0.1.5 and everyday 0.3.8 | all | to flag; each implements a ruled or confirmed what; confirmed in set C | engine #195, #197, #205 (both promise wickets), #202, #201, #203, #204, in the wickets' order; pack digests in §0 |

Set C (D2-D8) was confirmed by Ethan on 2026-10-22; the ruling line is on this PR. As-built wicket
digests: `seller.recipient_role/1.2.0` `6216addf…89d1`, `seller.release_on_acceptance/1.2.0`
`854718b6…a832`, `promise_requires_approval/1.1.0` `52514fb6…e386` (#209; `66d1e62d…d5d5` at #205), `promise_never/1.1.0`
`ed9e02ed…2b8b`, `offer_expiry/1.1.0` `e55083b3…90f1`, `seller.destination_rail/1.1.0`
`a86844bd…be0e`, `action_class_gate/1.2.0` `39a7c5b9…97bf`, `required_disclosure/1.2.0`
`cae96230…224f` (engine `python/tests/test_wicket_definition.py`).

Not a door: #179 needs no `external-check-input/v0` member (option b); #191 is a test (#380, its
consequence, is D9).

## 9. What is not in this batch

- The Muse ports and vendoring: not in this lane, but in the batch's release gate (§0).
- #184's payment-received rule (it only reuses D6).
- The authorization-record input for s04 (capsule-cli#252).

Future work found or left by the build, each still open unless marked:

- The release itself: seller 0.1.5, everyday 0.3.8 and the capsulectl rc that carries the rest of
  C1-C8, shipped with the Muse plugin and passing the Muse seller e2e on the release candidate
  (§0), including the "never mind" cancel case (§1) and the #188 phone fixture (§6): tracked in
  actionstate-muse#240.
- Muse ports: actionstate-muse#215 (the `representing` bridge, s04 and s10), #216 (reasons),
  #231 (s13 1.2.0), #238 (`pickup_area`, which rc20 already emits); the rest, and vendoring, are
  tracked in actionstate-muse#240 (§0).
- actionstate-muse#233: the rules-page line "Posting a listing always asks you first (built into
  capsulectl)." (§5); its owner is decided in that issue's How ack.
- capsule-engine#199: re-open a sale after the accepted buyer backs out; a recorded cancel does
  not reopen the item to other buyers (§1).
- capsule-engine#207: `destination_rail` with only `watched_rails` passes a non-string rail (§4;
  predates the batch).
- capsule-engine#152 (tracked as #158): rebase `offer_expiry/1.0.2`'s semantics onto 1.1.0 (§3).
- capsule-cli#384: record a listing taken down (`unpost`) (§5, Q16).
- capsule-cli#399: the `pickup_area` guard's Unicode hardening, as the listing word guard has (§1).
- capsule-cli#400: two uncaptured local plugin-run failures under load, one each in the
  attribution and proposal lanes (capsule-cli#391, #392). Every rerun passed. The suspected cause,
  `TestDealRemoteCheckerOnlyAddsAndFallsBack` asserting wall-clock time, is unproven.
- capsule-cli#407: CI headroom. The slowest race shards run about 14 of their 15 minutes after
  capsule-cli#404, which split the shards and closed #403.
