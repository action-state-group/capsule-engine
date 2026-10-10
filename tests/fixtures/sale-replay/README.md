# A sale bundle of a two-thread sale (capsulectl, taxonomy 6)

One sale of one synthetic item on one throwaway profile, asking 1900 with a floor of 1700. It has
two buyer threads, A and B, with the steps of `../live-history/`. A third thread, C, is opened last
and nothing is done in it. Every byte of the two sale bundles and the five check inputs is what
capsulectl wrote.

## Producer

- capsule-cli commit `6ac32ecd8b4c1519e3b4d8958fcc59e64e4e2d01` (pull request #196, "deal sale:
  register each thread on the sale's log; the user's own sale bundle", on main after
  `v0.1.0-rc14`). It was built from `git archive` of that commit with:

  ```sh
  CGO_ENABLED=0 go build -trimpath -ldflags "-s -w -buildid= \
    -X github.com/action-state-group/capsule-cli/internal/cli.cliVersion=v0.1.0-rc14-22-g6ac32ec \
    -X github.com/action-state-group/capsule-cli/internal/cli.cliCommit=6ac32ecd8b4c1519e3b4d8958fcc59e64e4e2d01" \
    -o capsulectl ./cmd/capsulectl
  ```

  The `-X` values are `git describe --tags` of that commit and the commit itself.
- `capsulectl --version` prints
  `capsulectl v0.1.0-rc14-22-g6ac32ec (commit 6ac32ecd8b4c1519e3b4d8958fcc59e64e4e2d01)`.
- A re-run makes other keys, ids and timestamps. These are replaced together: the bundles, the check
  inputs, the digests and deal ids pinned in `tests/test_sale_replay.py`, `expected_live.json` and
  `expected_replay.json`.

## Commands

`build.sh` holds the exact commands. Run it as:

```sh
build.sh <capsulectl> <capsule-cli>/skills/deal/profile/materiality-predicate/neutral.json <dir>
```

`<dir>` must hold `inputs/`, copied to `<dir>/in/`. The script uses a throwaway `HOME`,
`XDG_CONFIG_HOME` and plugin root under `<dir>`, and the profile `synthetic-seller`. Nothing is sent
anywhere: there is no `deal tick`, no cadence and no remote checker. A local stub rules checker is
pinned only to keep each external-check-input/v0 capsulectl hands it. It allows and names no rule.

Every capsulectl call waits one second first. A record's time is whole seconds, and two threads are
two logs, so a replay can order the records of two threads only when their seconds differ.

1. The sale (`deal sale new`), with two buyer threads, each registered on the sale's log
   (`deal open --sale`).
2. Buyer A: a claim of the item's condition, an offer at 1900 and its act, the buyer's acceptance of
   that offer, then a commit at 1900 and its act.
3. Buyer B, after A's commit: the same claim, an offer at 1850 and its act, the buyer's acceptance,
   then a commit at 1850, checked.
4. Buyer A again: the commit at 1900 checked a second time.
5. The user's own copy of the sale (`deal sale bundle`): `sale.bundle.json`.
6. Thread C (`deal open --sale`), then the user's own copy again: `sale-after-c.bundle.json`.

## Files

| file | what |
|---|---|
| `sale.bundle.json` | the user's own copy of the sale after step 5, A and B carried |
| `sale-after-c.bundle.json` | the user's own copy after step 6, A, B and C carried |
| `check-inputs/<thread>-<action>-<amount>.json` | the external-check-input/v0 of each check, its `history` and `deal_claims` included |
| `inputs/` | the sale, thread, claim, check and act bodies |
| `fixture_steps.py` | the two documented fixture steps below |
| `expected_live.json` | each check input decided live under `asg/seller/0.1.3`, with its history as sealed and with an `accept` disposition added to every history act, and the ledger each was decided on |
| `expected_replay.json` | each check decided by the replay of each sale bundle (as written, after C, and after C with B withheld), with its acts as sealed and with that disposition, and the ledger its `single_commitment` read |

Regenerate both expected files with `python -m tests.test_sale_replay`. The test compares them byte
for byte.

## What the records say

- **The checkpoint `sale.bundle.json` carries is the one cut at B's registration.** It is
  before every check. Nothing was appended to the sale's log after B's registration, and capsulectl
  cuts no checkpoint for a log that has not grown. So that copy cannot show that no thread was
  registered after B, and a replay holds every check of it n/a. Opening C appends a registration,
  so `sale-after-c.bundle.json` carries a checkpoint cut after every check. C is in the fixture for
  that reason only. Once `deal sale bundle` cuts a checkpoint when it writes the copy, drop step 6.
- **No act seals a disposition.** Each is a typed `action-record/v0`, sealed `fyi`. capsulectl seals
  a disposition only on an act whose action maps to a registered effect type, which today is a
  payment alone.
- **Each thread names its registration.** Its task authority carries one `registration` ref to the
  sale's `thread` record, beside its `sale_authority_commitment`. Each thread's sealed report opens
  that commitment (`sale_authority_opening`) to the digest of the sale's own task authority, the
  same value in every thread.
- **Each thread carries one record it does not disclose.** It is the report sealed when step 5
  wrote its copy, after every check. Step 6 sealed a newer one.

## Fixture steps

`fixture_steps.py` holds both.

- `with_act_dispositions` adds an `accept` disposition to every act of a sale the engine has
  already verified, as `tests/test_live_history.py` adds one to every act of a live check's history.
  The capsules it changes no longer verify, and the replay does not verify them again. It refuses a
  sale whose acts already seal one. When capsulectl seals one, rebuild this fixture and delete the
  step.
- `withhold_thread` writes the copy an adjudicator receives with one buyer's thread withheld. That
  thread's `sale_threads` entry keeps only its registration and `member: missing`, and
  `x-deal-sale/v0` no longer carries it. capsule-cli 6ac32ecd8b4c writes only the user's own copy.

## What a replay cannot check

- **The producer's own signatures.** Every checkpoint is the producer's, and the replay reads no
  witness receipt, so a producer holding the profile's keys can sign a log that leaves an act out.
  Only witnessing the sale's and its threads' checkpoints shows that a log was not rewritten.
- **`never_opened`.** Nothing sealed on the sale's log shows that a thread never opened. A copy
  that relabels a present thread `never_opened` and leaves it out reads as complete. The same
  holds for a copy that drops `threads_predate_registration`.
  `test_never_opened_is_the_producers_word` pins this.
- **Other deals.** The replay's history holds the sale's registered threads only. A live check is
  given every act of every deal on the profile from its last 31 days. It holds the sale's
  acceptance unknown when an accepted commitment of another deal names no item, and the replay
  does not.

## Identity

The buyers are `Example Buyer A`, `B` and `C` at `buyer-a.example`, `buyer-b.example` and
`buyer-c.example`, and the item is `example bicycle`. Only `inputs/` names a buyer: no bundle or
check input carries a buyer's name or domain. The check inputs carry the floor's opening and the
sale's item reference in clear, as capsulectl gives them to the user's own checker. Both are
synthetic.
