# Executed acts in the 7-day spend window

Seventeen sequences of external-check-input/v0 envelopes, each a check of a
2000 purchase commit whose `history` holds the profile's earlier acts. Step
`k` of a sequence holds its first `k` acts. Every act is a real capsulectl
v0.1.0-rc14 capsule (synthetic deal data, fingerprints only) re-sealed with
its own capsule id, action id, deal id, operator and time, and with the body
members a sequence names changed; `agent_input_digest` is recomputed and the
signature is dropped. These are test records, not signed ones.

- `inputs/<sequence>/step-<k>.input.json`: the envelope at step `k`.
- `expected.json`: per step, the 7-day half of rule `r05-spending-limits` as
  the Go plugin decides it on the same envelope: its verdict, the projected
  total (`value`, history plus this 2000; `null` when not evaluable), the
  limit and the card's reason. Its per-step values are the plugin's results
  file, sha256 `8c9a8d9670bc16a77e82fcf34bc094cd912851992236ec56c5218189b00deadc`,
  with only `schema` renamed.

The rule the sequences exercise, as both the engine and the plugin read it:

1. An accepted act counts at its `amount_minor`.
2. An act whose money moved in (`direction: "in"`) counts 0, decided or not.
3. An act taken without a check (`body.unchecked`, or `body.attempted` on a
   typed outcome) counts at its `spend_minor`, else its `amount_minor`.
4. Any other executed act (sealed `fyi`, with no decision) counts at its
   `spend_minor` when that is an integer of at least 0.
5. An executed act whose spend cannot be read leaves the window not
   evaluable, with the reason "an earlier act has no recorded decision, so
   the total cannot count it", and the check asks.
6. An act that shares a capsule id or an action id with an accepted act, or
   with an act already counted, is the same act and counts once.

Only acts in `[check time - 7d, check time]` under the check's operator count.

`tests/test_executed_acts_window.py` decides every step through the live
check path (`report/live_history.py`) under everyday 0.3.6 and compares it
with `expected.json`, every step of every sequence.
