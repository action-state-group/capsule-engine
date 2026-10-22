# Go vectors: seller s05/s07 required_disclosure counts the agent's claim in the deal

Source: capsule-engine branch `seller-s07-asks-on-every-offer`, commit
`2c02db9e9bd24107c7d38684e4a8d42c39930d6d` (base `a710636c29814ce67e838c673026fd2063d913f8`).
Pack `asg/seller/0.1.3` (`f0068688317b0cae3c40e839d7cff0137991aca466a878f9b558356f1808321a`),
wicket `required_disclosure/1.1.0` (`80f3af6e428f2331f6ab458997d996e172b9fc2e721b55fcf473f4e31d1492c0`),
fold `disclosure.statement_seen/1.0.0` (`925e7ccd2e29b3f1984f966e968bdef47d0d22d2b85b8e0205a39d6a5a7ff0be`).
The engine's rule id is `s05-required-statement-made-first`; the Go port calls it s07.

## The rule (AMENDMENT 5a / 5a-bis)

A statement of class C was made to the counterparty iff the deal has an `x-deal-v0` record with
`record_type` `claim`, bound by its capsule's `agent_input_digest`, with `body.source_kind == "agent"`
and `body.class == C`, in the one deal it names (`x-deal-v0.deal_id`, or `chain_id`; both and
different = no deal = not a statement). `delivery_promise` reads as `delivery_date` (only alias).
Never from a disclosure's `body.fields[].class`. Counted when it precedes the check in the same deal:
replay = ledger order; live = present in the check input's `history` (all of which precedes the check).
A class also counts, as before, via an accepted earlier action to the same target carrying
`representation_class`. Neither target nor deal -> n/a.

Live, a claim entry in `history` is a statement, never an act: it must not be counted as unread, and
must not move s11 (single_commitment), dedupe or any spend window.

## Files

- `check-inputs/<name>.claims-in-history.json`: the real check inputs from
  `tests/fixtures/live-history` (capsulectl 65f54e528a78) with an `accept` disposition on every history
  act and every claim either thread sealed by the check's time added to `history` (capsule +
  `agent_input` + the sale's `item_ref`), newest first. **Synthetic in one respect: capsulectl does not
  put claims in a checker's history today.** The claim capsules themselves are the bundles' bytes.
- `expected.json`:
  - `live`: per input, s05 result + evidence + outcome + all rule results, for `disposition-accept`
    (no claims: s05 fail) and `disposition-accept-claims-in-history` (s05 pass);
  - `replay`: the replay of both bundles, s05 per check (all pass);
  - `reader`: unit cases for the claim reader (agent condition, alias, buyer's claim, no class, a
    message, a disclosure's fields) with the statement record expected, or null.

`a-commit-again-1900` holds both threads' claims; only its own deal's counts (`stated_counts`
`{"condition": 1}`).

Live precedence: a claim entry is written only when its `timestamp` is at or before the checked
record's `timestamp`; none when the checked record's time is unreadable. A claim given twice is
written once. A claim entry never produces a spend-window record (it is not an executed act).
