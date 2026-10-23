# Executed acts under asg/everyday/0.3.6 (Go side, v0.0.11)

capsule-engine #138 (merge `a710636c`) ships asg/everyday/0.3.6: caps/5.1.0 over spend.weekly/3.1.0,
whose 7-day total counts every act a deal carried out. The G3 sequences
(`../g3-executed-acts-vectors`) hold the plugin to the engine on the acts capsulectl seals today; these
hold it on the shapes those sequences do not reach, each decided by the engine at `a710636c` and by
the plugin on the same input. Nothing here is hand-computed: every expected value is the engine's.

## Files
- `corpus.json`, `build.py`: 15 history sequences, each act one of `../g3-executed-acts-vectors/templates`
  with the edits the corpus names (`why` says what each holds). `python3 build.py <this dir>` rewrites
  `inputs/` byte for byte; on top of the G3 builder it can set an act's sealed disposition (outside
  `agent_input`, so no digest moves) and edit the checked record's body (its digest recomputed).
- `engine-results.json`: `../g3-executed-acts-vectors/engine_results.py <this dir> a710636c...`, run from
  capsule-engine at `a710636c`: per step caps' result and sealed evidence digest, the outcome and
  verdict, and each rule's result. (Its `r05_7d_*` members are the engine test's own reading of the
  card; the plugin test compares the rest.)
- `ledger_cases.py`, `ledger-results.json`: caps/5.1.0 over a ledger holding the engine's own records
  of executed acts (`disposition.decision` `executed`, `spend_unreadable`, a `live_history` copy),
  as a `--ledger` check reads them.
- `scope_cases.py`, `scope-results.json`: the engine's window on the plugin's two envelopes that
  declare an incomplete history (`../emit/pay-history-incomplete.json`,
  `../emit/pay-history-empty-incomplete.json`). caps.py does not read `history_scope`; the plugin
  leaves the window not evaluable. A pinned known difference (an open engine-side follow-up).
- Held by `go/guard/g3_engine_test.go`.

## What the engine decides here, and the plugin now does too (0.3.6 only)
- A history act sealed with a decision other than `accept` (`escalate`, `reject`) is an act carried
  out: it counts at its sealed `spend_minor`, or holds the window when that cannot be read.
- A cancel or refund (`booking.cancel`, `money.refund`) among the executed or unchecked acts adds
  nothing: the engine's record of it is a reversal that cites no charge (unlinked).
- Money in counts 0; an accepted act of a reversal class is the fold's own unlinked reversal.
- A per-action failure stands beside an unreadable act; caps' evidence names `window_unreadable_count`.
- The acts are read oldest first, and a repeat of an act (the same `capsule_id` or `action_id` as an
  accepted act, or as one already read) is dropped before the window is read: an unreadable copy of an
  accepted act does not hold the window, the older of two copies is the one counted, and a copy
  outside the window still claims the act.
- A window held by an unreadable act is caps `n/a` in scope naming `spend_minor`, reason
  "an earlier act has no recorded decision, so the total cannot count it".

Under the frozen 0.3.5 (spend.weekly/3.0.0) the plugin keeps the G3 rule it had.
