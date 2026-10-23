# A live check's history of untyped acts (capsulectl, taxonomy 6)

Eight buyer check inputs (external-check-input/v0), each exactly as capsulectl handed it to a
rules checker: every byte of each file is what capsulectl wrote. In each, the `history` holds the
profile's earlier acts as `x-deal-v0` records of `record_type` `action` (and, in one, an `outcome`
stating an act taken without a check), not as typed `action-record/v0` records.

## Producers

| directory | capsulectl |
|---|---|
| `rc14/` | `v0.1.0-rc14`, commit `49651e2f156e370208dec605ded653c5f0a9a4bf` |
| `main-2214b0f/` | `v0.1.0-rc14-16-g2214b0f`, commit `2214b0fa61bfb66cfb229084e7c005260d56d873` |

Each record names its producer in `agent_input.x-deal-v0.producer`. The profiles, merchants and
items are synthetic. A re-run makes other keys, ids and timestamps, so the inputs, the digests
pinned in the test and `expected_live.json` are replaced together.

## Files

| file | the check | its history (every pay sealed `accept`) |
|---|---|---|
| `main-2214b0f/pay-a-merchant-paid-before.input.json` | a pay of 1299 | two pays at the same merchant, in other deals |
| `main-2214b0f/repeat-pay-in-the-same-deal.input.json` | a pay of 2000 | three pays at the same merchant, one of 2000 in the same deal |
| `rc14/repeat-pay-in-the-same-deal.input.json` | the same, from the other producer | the same |
| `main-2214b0f/repeat-pay-in-another-deal.input.json` | a pay of 2000 | three pays at the same merchant, two of 2000, none in the same deal |
| `main-2214b0f/pay-after-a-partial-cancel.input.json` | a pay of 1299 | two pays at the same merchant, and a partial cancel of 500 (`direction` `in`) sealed with no disposition |
| `main-2214b0f/repeat-booking-in-the-same-deal.input.json` | a booking commit of 2000 | the same booking's commit in the same deal, sealed with no disposition, and two pays |
| `main-2214b0f/booking-at-another-merchant.input.json` | a booking commit of 2000 | a booking commit of 2000 at another merchant in another deal, sealed with no disposition, and two pays there |
| `main-2214b0f/pay-after-an-unchecked-act.input.json` | a pay of 1299 | an `outcome` stating an act taken without a check (`body.unchecked`), and a pay at another merchant |
| `expected_live.json` | each input decided live under everyday, with the state each history record was written in and each act's target; sorted canonical JSON | |

Regenerate `expected_live.json` with `python -m tests.test_live_history_untyped_acts`.
`tests/test_live_history_untyped_acts.py` compares it byte for byte.
