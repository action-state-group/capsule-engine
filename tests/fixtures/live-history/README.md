# A live two-thread sale (capsulectl, taxonomy 6)

One sale of one synthetic item on one throwaway profile, asking 1900 with a floor of 1700, and two
buyer threads. Every byte of the two bundles and the five check inputs is what capsulectl wrote.

## Producer

- capsule-cli commit `424e79479c40138fcb425674a2ec7c4e540ce478` (the head of capsule-cli pull request
  #194, "deal: a history act carries the counterparty its check sealed", on main after `v0.1.0-rc14`), built
  with `git archive` and `CGO_ENABLED=0 go build -trimpath -ldflags "-s -w -buildid= -X …"`.
- `capsulectl --version` prints
  `capsulectl v0.1.0-rc14-17-g424e794 (commit 424e79479c40138fcb425674a2ec7c4e540ce478)`.
- Rebuilt with that commit on 2026-10-09 so each history act carries `counterparty`; against the
  rc14-11 build it replaced, only `s09-no-repeated-act` on the second check of A's commit changed
  (with the added disposition: `pass` to `fail`, as the replay decides it).
- A re-run makes other keys, ids and timestamps, so the bundles, the check inputs, the digests
  pinned in the test, the deal ids pinned in `tests/test_seller_statements.py` and `expected_live.json`
  are replaced together.

## Commands

`build.sh` holds the exact commands. Run it as:

```sh
build.sh <capsulectl> <capsule-cli>/skills/deal/profile/materiality-predicate/neutral.json <dir>
```

`<dir>` must hold `inputs/` (copied to `<dir>/in/`). The script uses a throwaway `HOME`,
`XDG_CONFIG_HOME` and plugin root under `<dir>`, and the profile `synthetic-seller`. Nothing is sent
anywhere: no `deal tick`, no cadence and no remote checker. A local stub rules checker is pinned
only to keep each external-check-input/v0 capsulectl hands it; it allows and names no rule.

1. The sale (`deal sale new`), with two buyer threads (`deal open --sale`).
2. Buyer A: a claim of the item's condition, an offer at 1900 and its act, the buyer's acceptance
   of that offer, then a commit at 1900 and its act.
3. Buyer B, after A's commit: the same claim, an offer at 1850 and its act, the buyer's acceptance,
   then a commit at 1850, checked.
4. Buyer A again: the commit at 1900 checked a second time.
5. Each thread's own copy, last: `bundle --deal`.

## Files

| file | what |
|---|---|
| `buyer-a.bundle.json`, `buyer-b.bundle.json` | each thread's own copy, replayed by the engine in that order |
| `check-inputs/<thread>-<action>-<amount>.json` | the external-check-input/v0 of each check, its `history` included |
| `inputs/` | the sale, thread, claim, check and act bodies |
| `expected_live.json` | each check input decided live under `asg/seller/0.1.3` with its history as sealed, with an `accept` disposition added to every history act, with no history, (with that disposition) with no `item_ref` on the history's accepted commitments or on the checked record, (with that disposition) with every claim either thread sealed by the check's time added to the history, and (with that disposition) with the checked record's `deal_claims` added by `add_deal_claims.py`; the ledger each was decided on; sorted canonical JSON |

Regenerate `expected_live.json` with `python -m tests.test_live_history`.
`tests/test_live_history.py` compares it byte for byte.

## What the records say

- **No history act seals a disposition.** Each is a typed `action-record/v0`, sealed `fyi`, its
  basis the task authority. capsulectl seals a disposition only on an act whose action maps to a
  registered effect type, which today is a payment alone.
- **Each thread seals its own task authority.** A's and B's commits cite different
  `task_authority_ref`s; the sale's `item_ref` is the one value they share.
- **The history is newest first.** Acts sealed in the same second keep the order they were sealed in.
- **An act record seals no counterparty.** A check does (the thread's buyer); the act that follows
  it does not. capsulectl gives each history act, beside its capsule, `counterparty`: the block its
  check sealed, exactly as sealed, keyed per deal.

The `accept` dispositions in `expected_live.json` are added by the test, not sealed by capsulectl:
those capsules no longer verify, and the engine does not verify them here.
The claims in the history are added by the test too: capsulectl seals them (they are in the bundles,
byte for byte) but puts only acts in a checker's history. Each is given as an act is: its capsule
with its disclosed `agent_input`, and the sale's `item_ref` beside it.

`add_deal_claims.py` is a documented fixture step: it gives a check input the `record.deal_claims`
capsulectl will pass beside the checked record (AMENDMENT 9), built from the thread's own bundle out of
the sealed claim record's own values. capsule-cli 65f54e528a78 does not pass them. The step refuses an
input that already carries `deal_claims`; once capsulectl passes them, rebuild this fixture and delete
the step.

## Identity

The buyers are `Example Buyer A` and `B` at `buyer-a.example` and `buyer-b.example`, and the item is
`example bicycle`. Only `inputs/` names a buyer: no bundle or check input carries a buyer's name or
domain. The check inputs carry the floor's opening and the sale's item reference in clear, as
capsulectl gives them to the user's own checker; both are synthetic.
