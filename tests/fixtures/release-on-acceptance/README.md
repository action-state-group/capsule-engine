# Shares after a seller's acceptance (capsulectl, taxonomy 6)

One sale of one synthetic item on one throwaway profile, asking 1900 with a floor of 1700, and two
buyer threads, each opened with a synthetic name and marketplace profile id. Every byte of the sale
bundle and the five check inputs is what capsulectl wrote.

## Producer

- capsule-cli commit `6210b9345fd9ecc6d6ed3590cba78fc813770292` (main after `v0.1.0-rc19`, with
  `pickup_area`, capsule-cli#382). The core and the deal plugin were built from `git archive` of
  that commit as `scripts/release-build.sh` builds them, for darwin/arm64:

  ```sh
  L="-s -w -buildid= -X github.com/action-state-group/capsule-cli/internal/cli.CLIVersion=v0.1.0-rc19-6-g6210b93 \
    -X github.com/action-state-group/capsule-cli/internal/cli.CLICommit=6210b9345fd9ecc6d6ed3590cba78fc813770292"
  CGO_ENABLED=0 go build -trimpath -buildvcs=false -ldflags "$L" -o capsulectl ./cmd/capsulectl
  (cd plugins && CGO_ENABLED=0 go build -trimpath -buildvcs=false -ldflags "$L" -o ../capsulectl-deal ./cmd/capsulectl-deal)
  ```

- `capsulectl --version` prints
  `capsulectl v0.1.0-rc19-6-g6210b93 (commit 6210b9345fd9ecc6d6ed3590cba78fc813770292)`.
- A re-run makes other keys, ids and timestamps, so every file here and the digests pinned in
  `tests/test_release_on_acceptance_history.py` are replaced together.

## Commands

`build.sh` holds the exact commands. Run it as:

```sh
build.sh <capsulectl> <capsulectl-deal> builtin:neutral <dir>
```

Before running, copy this directory's `inputs/` to `<dir>/in/` yourself: the script reads `in/` and
copies nothing. It writes `out/sale.bundle.json` and `out/check-inputs/*.json`. To refresh the
fixture, copy `out/sale.bundle.json` here and the five `out/check-inputs/` files named in
`check-inputs/` (the script also writes `a-offer-1900`, which no test reads, so it is not kept). The
script uses a throwaway `HOME`,
`XDG_CONFIG_HOME` and plugin root under `<dir>`, and the profile `synthetic-seller`. Nothing is sent
anywhere. A local stub rules checker is pinned only to keep each external-check-input/v0; it allows
and names no rule.

1. The sale, with two buyer threads opened by name and profile id.
2. Buyer A: a claim of the item's condition, an offer at 1900 and its act, the buyer's acceptance,
   then a commit at 1900 and its act.
3. The seller checks a share with A of the phone, then of the pickup area; then the same two with B.
   Each share pauses (a first telling) and is approved with the user's words.
4. The user's own copy of the sale: `sale.bundle.json`.

## Files

| file | what |
|---|---|
| `sale.bundle.json` | the sale's copy, both threads carried |
| `check-inputs/<thread>-<check>.json` | the external-check-input/v0 of each check, its `history` included |
| `inputs/` | the sale, opening, claim, check and act bodies |

## What the records say

- **The commit act seals a disposition** (`accept`, approver `policy`), and its history entry carries
  the counterparty block its check sealed: name, payee and profile id under `hmac-sha256-chain-key`.
- **No clock can be set.** capsulectl reads the time from `cli.DealClock`, a package variable with no
  flag or environment override, so the commit cannot be sealed more than 31 days before the share
  without patching the producer. The window is tested on moved records instead.
- **The copy's thread reports are standalone** (`x-deal-report/v0`, capsule-cli#299): the engine's
  sale reader looks for the report on the thread's log, so it reads this copy as not under its sale
  (`thread_is_not_under_this_sale`) and a replay gets no sale history. The replay test is an expected
  failure until the reader reads the standalone report.
