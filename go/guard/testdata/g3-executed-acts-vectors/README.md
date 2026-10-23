# G3 executed-acts vectors (Go side, v0.0.10)

The Go answer to the engine-side G3 ruling (2026-10-09) on buyer acts that are not a pay, for
the follow-up that replay counts executed acts as spend: that item is accepted when the Python
replay gives `go-results.json` on these inputs.

## The rule, as Go implements it (rule 5 window-only from v0.0.10)
For each act in `history` (r05-spending-limits#7d, caps/5.0.0, spend.weekly/3.0.0, key operator):
1. **Accepted decision** (`disposition.decision == "accept"`): the fold counts it (`amount_minor`), as before.
2. **Money in** (`body.direction == "in"`, any decision or none): counts 0, never holds the window (#130).
3. **Unchecked** (`body.unchecked`, or a typed `action-outcome/v0` `body.attempted`): counted at its
   `spend_minor`, else `amount_minor` (ruling (a), unchanged).
4. **Executed, no recorded decision** (every checked act but pay through rc14: commit, sign, sealed
   `fyi`): counted at its sealed `body.spend_minor` when that is an integer >= 0. `amount_minor` is not
   read for it (it may be absent).
5. **Executed, spend cannot be read** (no `spend_minor`, a negative one, or one that is not an integer):
   the window is not_evaluable, reason (card) "an earlier act has no recorded decision, so the total
   cannot count it", and the user is asked (decision escalate, never allow, never deny). **Window-only**
   (engine-side ruling on these vectors, 2026-10-09): only such an act the window would count holds it, i.e.
   one inside `[check timestamp - 7d, check timestamp]` (both ends included) with the check's operator.
   One outside cannot change the 7-day total and does not hold it. See `unreadable-outside-the-window`
   (rewritten: 480h before, no longer holds) and `unreadable-window-edge` (169h does not hold; 167h does).
6. **Counted once**: an executed or unchecked record that shares a `capsule_id` or an `action_id` with
   an accepted record adds nothing; the same executed/unchecked act given twice counts once.
Only acts inside the window `[check timestamp - 7d, check timestamp]` with the check's operator count.

## Files
- `corpus.json`: 17 sequences, oldest act first, each with `expect`: r05#7d `[verdict, value]` per step,
  computed by hand from the rule above (not from Go). value = projected total (history + this 2000).
- `templates/`: real capsulectl v0.1.0-rc14 capsules. `check.json` = the envelope of
  `../real-fyi-act` with empty history (commit 2000, money.purchase, buyer); `pay`, `commit` from that
  envelope's history; `sign` and `commit-typed` from the 2026-10-09 fyi buyer-acts probe run (acc-l3-default-rental-sign,
  acc-l1-commit-typed); `unchecked` from `../real-unchecked-act`; `money-in` (fyi cancel, direction in,
  spend 0) from `../refund-never-blocks-vectors/real`.
- `build.py`: `python3 build.py <this dir>` rewrites `inputs/` byte for byte. Per act it sets capsule_id,
  action_id, deal id, operator ("deal") and timestamp, applies the corpus body edits, recomputes
  `agent_input_digest`, and drops `signature` (these are re-sealed test records, not signed ones).
- `inputs/<sequence>/step-<k>.input.json`: the external-check-input/v0 envelope at step k (history newest first).
- `go-results.json`: per step, the binary's r05#7d verdict, value, limit, reason.
- Held by the consuming plugin's G3 vector test (Go == expect == go-results.json; `G3_VECTORS_WRITE=1`
  regenerates results and `MANIFEST.sha256`), and by `go/guard/g3_engine_test.go` (engine-results.json).

## The engine half: closed by capsule-engine #138 (a710636c, asg/everyday/0.3.6)
The replay used to count spend from accepted decisions only, adding 0 for every executed act above, and
had no window coverage for the `unreadable-*` steps. #138 (spend.weekly/3.1.0 under caps/5.1.0) counts
every executed act once, as above; its own fixture (`tests/fixtures/executed-acts-window`) carries these
`inputs/` byte for byte and this `go-results.json` as its `expected.json` (sha256 `8c9a8d96…deadc`).

- `engine_results.py`: run from capsule-engine at the commit it is given, it decides every step as the
  engine's `tests/test_executed_acts_window.py` decides a live check (everyday 0.3.6) and writes
  `engine-results.json`: the card halves above plus the outcome, verdict, caps' result and sealed
  evidence digest, and each rule's result. At `a710636c` its card halves equal `go-results.json` on all
  76 steps, and `go/guard/g3_engine_test.go` holds the plugin's caps evidence digest, outcome and
  every rule to the rest.
- The shapes these sequences do not reach are in `../executed-acts-036-vectors`.

The rulings named here (ruling (a), ruling (b), the G3 ruling) are defined in the package doc of
`go/guard` (`action.go`).
