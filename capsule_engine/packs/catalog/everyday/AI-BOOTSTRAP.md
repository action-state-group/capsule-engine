# AI-BOOTSTRAP: everyday

Paste everything below the line into your AI coding assistant, in the repo
whose payment and messaging code you want covered by the `everyday` pack.

---

You are helping me wire `capsule-engine`'s `everyday` pack into this
codebase. The pack records a decision for each payment and each outgoing
message an assistant makes on a household's behalf, and runs six
deterministic checks over the recorded fields. It governs two action types:

- `payment.make`, mapped to the `money.transfer` guard class;
- `message.send`, mapped to the `comms.external` guard class.

**What each check reads** (the ONLY fields this pack's checks read; do not
invent others):

| Normalized field | Read by | What it is |
|---|---|---|
| `amount_minor` | `caps` | the amount in minor currency units (cents), always an integer |
| `currency` | -- | ISO 4217 code, e.g. `"EUR"` |
| `target` (pack-facing name: **payee_ref**) | `dedupe`, `counterparty_identity_change` | a stable reference for who is paid or messaged |
| `rail` | `destination_rail` | the payment rail, e.g. `"card"`, `"bank_transfer"`, `"p2p"`, `"gift_card"`, `"crypto"` |
| `counterparty_account_ref` | `counterparty_identity_change` | an opaque reference to the account the payee is paid into -- a token or digest that is stable per account, never the account, card or IBAN number, because it is recorded on the capsule. A value shaped like a raw number is refused |
| `outgoing_content` | `credential_pattern` | the text the action sends; it is matched and then discarded, never recorded |
| `recurrence` | `recurring_charge` | whether the payment repeats: `"one_time"`, or e.g. `"monthly"` for a subscription |
| `equivalence_key` | `dedupe` | optional: your own idempotency key, if two payments to one payee are genuinely different payments (two monthly bills) |

**The checks** (each one is a wicket cited by digest from the engine's own
catalog):

1. `caps` (`caps/3.0.0`) -- this amount must be at or under the per-action
   limit (default 25.00), and the operator's rolling 7-day spend plus this
   amount must be at or under the window limit (default 100.00); the record
   names which limit tripped. The limits cover every
   class that pays money out (transfers, purchases, subscriptions, bookings),
   and the total is kept per `operator`, so a new agent version or a second
   tool acting for the same operator draws on the same total.
2. `dedupe` (`dedupe/1.0.0`) -- the same payment by the same agent to the
   same payee is flagged if it was already recorded in the window.
3. `destination_rail` (`destination_rail/1.0.0`) -- a payment over a
   watched rail (peer-to-peer, gift card, crypto) is flagged.
4. `counterparty_identity_change` (`counterparty_identity_change/1.0.0`) --
   the payee's account is compared with the one last recorded for that payee.
5. `credential_pattern` (`credential_pattern/1.0.0`) -- outgoing content is
   matched against one-time-code, password and card-security-code patterns.
6. `recurring_charge` (`recurring_charge/1.0.0`) -- a payment whose
   declared recurrence is not one-time is flagged.

**When a check does not settle.** A check that could not be evaluated
records `n/a` with a small facts object `{constraint_id, in_scope,
missing_field}`, digested into the constraint record's `evidence_digest`:

- `in_scope: false` -- the rule did not apply to this action (no cap is
  configured for its class, it sends no content, the payee has no recorded
  account yet).
- `in_scope: true` with `missing_field` named -- the rule applied and the
  action lacked the field it needed. A payment with no `amount_minor` is the
  common case. This is the action's gap, not a pass.

Both objects are canonical and hold no private data, so anyone can
recompute the candidate digests and tell the two cases apart from
`evidence_digest` alone.

**A structural limit of the Result projection.** When decisions are
projected into an Evidence Result, an out-of-scope rule becomes an anonymous
+1 in `aggregate.coverage.excluded_not_applicable`. A reader of the Result
learns HOW MANY rules were excluded, but not WHICH rules or WHY. The facts
object above makes the two `n/a` cases distinguishable on the constraint
record only; the projection does not carry that distinction further.

**What the fixture corpus records about itself.** Each row of this pack's
fixture corpus records the pack and wicket digests it ran under and an
identity for the code that produced it, captured when the row was produced.
That code identity is a claim by whoever built the artifact (self-attested):
it lets rows be compared across a window as coming from the same code, but a
stranger cannot confirm it from the receipt.

**What I need from you:**

1. Scan this codebase for every call that moves money or sends a message
   to someone outside the household (payment SDK calls, bank or wallet
   transfers, email/SMS/chat sends). Look at what is actually here.
2. For each one, draft the `capsule_engine.guards.Action` that represents
   it, using only the fields above plus `verb`, `operator`, `developer` and
   `action_class`. Leave `action_type` at its default (`"decide"`); the pack's
   action type names are documentation, not field values. Show the draft
   beside the existing call; do not rewrite the call's logic.
3. List what you could not map, and why (the amount is only known later,
   there is no stable payee reference, the call is inside a library you
   cannot instrument). An honest "could not map" is more useful than a guess.
4. Do not change what the code does. Install in observe mode
   (`capsule init --pack everyday`) and call
   `guard_engine.check(action, dry_run=True)`; the pack records what it
   would have decided and changes nothing about the call itself.

Output: one section per call site with (a) file/function, (b) the drafted
`Action`, (c) your confidence, (d) what you could not determine; then a
final section for anything scanned but not mapped.
