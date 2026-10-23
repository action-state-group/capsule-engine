# AI-BOOTSTRAP: payments-safety

Paste everything below the line into your AI coding assistant, in the repo
whose payment-moving code you want covered by the `payments-safety` pack.
It has the pack's field contract already filled in -- you shouldn't need to
edit anything before pasting, though you're welcome to trim the "scope"
paragraph if you already know exactly which files to point it at.

---

You are helping me wire `capsule-ledger`'s `payments-safety` starter pack
into this codebase. This pack governs one normalized action type,
`payment.dispatch`, mapped to the `money.transfer` guard class. Every call
that actually moves money -- initiates a transfer, dispatches a payout,
settles an invoice, or similar -- needs to go through this action type
before it executes.

**The field contract** (the ONLY fields this pack's checks read; do not
invent others):

| Normalized field | Required? | Type | What it is |
|---|---|---|---|
| `amount_minor` | required | integer | the amount, in minor currency units (cents), never a float |
| `currency` | required | string | ISO 4217 currency code, e.g. `"EUR"`, `"USD"` |
| `target` (pack-facing name: **counterparty_ref**) | required | string | who/what receives the payment -- an account id, invoice reference, or vendor identifier stable enough to dedupe on |
| `cited_mandate_capsule_id` | optional | string | if this payment executes an authorization that was itself recorded as a capsule, its `capsule_id` -- required for any payment your own policy says needs prior approval |
| `equivalence_key` | optional | string | override the pack's default dedupe formula (operator + developer + action_type + verb + target) if this codebase already has a stronger idempotency key |

**What I need from you:**

1. **Scan this codebase** for every function, endpoint, or job that moves
   money -- look for things like payment processor SDK calls (Stripe,
   Adyen, etc.), internal ledger/wallet debit-credit calls, ACH/wire
   initiation, or a "dispatch payout" queue producer. Don't assume a
   specific framework; look at what's actually here.

2. **For each one you find**, draft the `capsule_ledger.guards.Action` (or
   the equivalent `capsule-emit` call if this codebase already emits
   capsules through an adapter) that would represent it, using ONLY the
   fields in the table above plus the standard identity fields every
   action needs (`verb`, `operator`, `developer`). Show me the draft next
   to the existing function -- as a comment or a small wrapper, your call --
   don't silently rewrite the function's real logic.

   Example shape for a raw `emit()` call:
   ```python
   from capsule_ledger.guards import Action

   action = Action(
       verb="dispatch_payout",              # your own verb, any string
       operator="<this deployment's operator id>",
       developer="<the agent/service identity making the call>",
       action_class="money.transfer",
       # Leave action_type at its default ("decide") -- it's a base-spec
       # field with a closed set {fyi, decide}, not a place for this pack's
       # own action-type name. "payment.dispatch" is documentation: it's how
       # this pack's obligations/config reference this action family, not a
       # literal field value to set anywhere.
       amount_minor=<integer, minor units>,
       currency="<ISO 4217 code>",
       target="<counterparty_ref>",
       cited_mandate_capsule_id=<capsule_id or None>,
   )
   decision = guard_engine.check(action)   # allow / deny / escalate
   ```

3. **List what you could not map.** Be specific: which function, and why
   -- amount isn't known until a downstream step, no stable counterparty
   reference exists yet, the call is inside a vendored/third-party
   library you can't instrument, etc. Don't guess a mapping you're not
   confident in; an honest "couldn't map this" is more useful than a
   wrong field assignment.

4. **Do not change enforcement behavior.** This pack installs in observe
   mode (`capsule init --pack payments-safety`) -- it records what it
   would have decided without blocking anything. Your draft should call
   `guard_engine.check(action, dry_run=True)` and must not change what the
   surrounding code actually does with the payment call's result.

Output format: one section per money-moving call site you found, each with
(a) the file/function it's in, (b) your drafted `Action`, (c) your
confidence, and (d) anything you couldn't determine. Then a final section
listing anything you scanned but could not map at all.
