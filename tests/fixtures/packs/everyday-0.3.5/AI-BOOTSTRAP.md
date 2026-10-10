# AI-BOOTSTRAP: everyday

Paste everything below the line into your AI coding assistant, in the repo
whose payment and messaging code you want covered by the `everyday` pack.

---

You are helping me wire `capsule-engine`'s `everyday` pack into this
codebase. The pack records a decision for each payment, purchase, booking
and outgoing message an assistant makes on a household's behalf, and runs
sixteen deterministic checks over the recorded fields. It carries 27 rules:
24 are measured by those checks, and 3 are declared not measured (see
below). It governs four action types:

- `payment.make`, mapped to the `money.transfer` guard class;
- `message.send`, mapped to the `comms.external` guard class;
- `purchase.make`, mapped to the `money.purchase` guard class;
- `booking.create`, mapped to the `booking.create` guard class.

**What each check reads** (the ONLY fields this pack's checks read; do not
invent others):

| Normalized field | Read by | What it is |
|---|---|---|
| `amount_minor` | `caps` | the amount in minor currency units (cents), always an integer: the amount the payment is expected to take |
| `spend_authorized_minor` | `caps` | optional: the most the payment may take, in minor units, when that is more than `amount_minor` (a card pre-authorisation or a buffer); leave it unset when there is none |
| `currency` | -- | ISO 4217 code, e.g. `"EUR"` |
| `action_class` | `action_class_gate` | the guard class the action is declared in; the gate reads nothing else |
| `target` (pack-facing name: **payee_ref**, or **merchant_ref** on a purchase) | `dedupe`, `counterparty_identity_change`, `counterparty_seen_before` | a stable reference for who is paid, bought from or messaged |
| `rail` | `destination_rail` | the payment rail, e.g. `"card"`, `"bank_transfer"`, `"p2p"`, `"gift_card"`, `"crypto"` |
| `counterparty_account_ref` | `counterparty_identity_change` | an opaque reference to the account the payee is paid into -- a token or digest that is stable per account, never the account, card or IBAN number, because it is recorded on the capsule. A value shaped like a raw number is refused |
| `outgoing_content` | `credential_pattern` | the text the action sends; it is matched and then discarded, never recorded |
| `recurrence` | `recurring_charge` | whether the payment repeats: `"one_time"`, or e.g. `"monthly"` for a subscription |
| `equivalence_key` | `dedupe` | optional: your own idempotency key, if two payments to one payee are genuinely different payments (two monthly bills) |
| `recipient_role` | `recipient_role` | on a personal-data disclosure: the receiver's role, exactly one of `"fulfilling_merchant"`, `"third_party"`, `"self"`; a role, never a name |
| `refundable` | `refundability` | `true` or `false`: whether the payment can be refunded |
| `material_fields_changed`, `material_fields_basis` | `material_fields_changed` | an integer count of the fields on the check's pinned list that differ from what the user approved, and the SHA-256 (over JCS bytes) of that list; the count is read only when the digest matches |
| `offer_fields_changed`, `offer_fields_basis` | `offer_fields_changed` | the same, over the offer list, against what the user stated |
| `channel`, `first_contact_channel` | `channel_change` | the kind of channel in use now and the one the relationship started on, e.g. `"marketplace"`, `"email"`, `"whatsapp"`; a kind, never an address |
| `upfront_amount_minor` | `upfront_amount` | the stated deposit, an integer in the same minor units as `amount_minor` |
| `task_authority_ref` | `task_authority` | the SHA-256 digest of the whole sealed task-authority record the action cites; the whole record is passed as `guard_engine.check(action, task_authority_record=record)`, the engine recomputes its digest, and the plan inside it (`outcome_id`, `allowed_actions`, `preconditions`) is read only when that digest matches |

Every one of these is a single number, a member of a small closed set, or
an opaque reference. None is an object, a list, a difference or free text;
leave a field unset when you do not have it, never `0` or `""`.

**The checks** (each one is a wicket cited by digest from the engine's own
catalog):

1. `caps` (`caps/5.0.0`) -- the most this payment may take must be at or
   under the per-action limit (default 25.00), and the operator's rolling
   7-day spend plus this amount must be at or under the window limit
   (default 100.00); the record names which limit tripped. The per-action
   limit reads `spend_authorized_minor` when it is set and not below
   `amount_minor`, and `amount_minor` otherwise; the record names which one
   it read and whether it fell back. The rolling total adds only
   `amount_minor`, so a pre-authorisation is never counted twice. The limits cover every
   class that pays money out (transfers, purchases, subscriptions, creating
   or changing a booking); cancelling a booking is not capped. The total is
   kept per `operator`, so a new agent version or a second tool acting for
   the same operator draws on the same total, and it leaves out dry runs.
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
7. `action_class_gate` (`action_class_gate/1.0.0`) -- named selectors over
   the action taxonomy: a class with no consequential effect passes (today,
   `info.query` alone); public posting, creating or cancelling a booking,
   deleting stored data, and disclosing personal data are flagged. It reads
   the declared class only, never what the action contains. Eight rules
   cite this check, and each names the one selector it is measured by
   (`selector:` in `pack.yaml`): a rule fails or passes only when its
   selector matched the action's class, and does not apply otherwise, so a
   booking fails the booking rule and no other. A class with no row in the
   taxonomy fails all eight, because the gate fails closed. A flagged class
   asks the approver its taxonomy row names when every rule it failed
   declares ASK; it is refused when any of them declares NEVER, or when the
   class names no approver.
   `packs.obligation_results` reads one decision's result per rule.
8. `counterparty_seen_before` (`counterparty_seen_before/4.0.0`) -- a
   purchase from a merchant the operator has no accepted earlier action with
   is flagged; an earlier act that asked, was approved and was executed
   counts as one, and a dry run does not. The record
   names the count and the key it was read under. A purchase with no
   `target`, or an empty one, is flagged as a first-time merchant, with the
   reason "no payee named".
9. `recipient_role` (`recipient_role/1.0.0`) -- a personal-data disclosure
   passes for the fulfilling merchant or the user and is flagged for a third
   party; a role outside the set is flagged.
10. `refundability` (`refundability/1.1.0`) -- a payment or sale declared
    not refundable is flagged.
11. `material_fields_changed` (`material_fields_changed/1.1.0`) -- any
    change on the pinned material list (item, quantity, price, deposit,
    currency, date, place, conditions, rail, refundability, payee) is
    flagged.
12. `offer_fields_changed` (`offer_fields_changed/1.1.0`) -- any change on
    the pinned offer list (item, quantity, price, deposit, conditions,
    refundability) is flagged.
13. `recipient_seen_before` (`recipient_seen_before/1.0.0`) -- a message to
    a recipient the operator has no accepted earlier action addressed to is
    flagged, counted as `counterparty_seen_before/2.0.0` counts.
14. `channel_change` (`channel_change/1.1.0`) -- `channel` differing from
    `first_contact_channel` on the same action is flagged.
15. `upfront_amount` (`upfront_amount/1.1.0`) -- a deposit above 100.00, or
    above 25% of `amount_minor`, is flagged.
16. `task_authority` (`task_authority/1.1.0`) -- an action outside the
    allowed actions of the task-authority record it cites is flagged.

Checks 10, 11, 12, 14, 15 and 16 also read an action declared in the
`marketplace.sale` class, the user's agent selling something. `caps` does
not: a sale is money coming in.

**Rules declared not measured.** 3 of the 27 rules need an input no action
records yet: a message classified as committing, where an instruction came
from, and the user's control state. Each one is in `pack.yaml` with `measurability:
declared_not_measured` and the `evidence_instrument` field its input would
arrive in. It cites no check and is never evaluated: a decision record
carries nothing for it. Do not invent those fields to make a rule apply.

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

1. Scan this codebase for every call that moves money, buys, books or sends
   a message to someone outside the household (payment SDK calls, bank or
   wallet transfers, checkouts, reservations, email/SMS/chat sends). Look at
   what is actually here.
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
