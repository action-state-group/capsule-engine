# A history act's counterparty (capsulectl, taxonomy 6)

Two hotel bookings on each of two throwaway profiles, one per deal record set (`x-deal-v0` and
`typed`). Every byte of the bundles and the check inputs is what capsulectl wrote.

## Producer

- capsule-cli commit `424e79479c40138fcb425674a2ec7c4e540ce478` (the head of capsule-cli pull request
  #194, "deal: a history act carries the counterparty its check sealed"), built with `git archive`
  and `CGO_ENABLED=0 go build -trimpath -buildvcs=false -ldflags "-s -w -buildid= -X …"`, as
  `scripts/release-build.sh` does.
- `capsulectl --version` prints
  `capsulectl v0.1.0-rc14-17-g424e794 (commit 424e79479c40138fcb425674a2ec7c4e540ce478)`.
- A re-run makes other keys, ids and timestamps, so the bundles, the check inputs, the digests
  pinned in the test and `expected_live.json` are replaced together.

## Commands

`build.sh` holds the exact commands. Run it as:

```sh
build.sh <capsulectl> <capsule-cli>/skills/deal/profile/materiality-predicate/neutral.json <dir>
```

`<dir>` must hold `inputs/` (copied to `<dir>/in/`). The script uses a throwaway `HOME`,
`XDG_CONFIG_HOME` and plugin root under `<dir>`, and the profiles `synthetic-x-deal-v0` and
`synthetic-typed`. Nothing is sent anywhere: no `deal tick`, no cadence and no remote checker. A
local stub rules checker is pinned only to keep each external-check-input/v0 capsulectl hands it;
it allows and names no rule.

For each record set:

1. Deal 1: a double room at Example Hotel Shinjuku for 380.00, checked, then its act; then the same
   booking checked again (`repeat-in-one-deal`).
2. Deal 2: the same room for the same amount at Example Hotel Kyoto, checked (`other-merchant`).
3. Each deal's own copy, last: `bundle --deal`.

## Files

| file | what |
|---|---|
| `<set>/deal-1.bundle.json`, `<set>/deal-2.bundle.json` | each deal's own copy, replayed by the engine in that order |
| `<set>/repeat-in-one-deal.input.json`, `<set>/other-merchant.input.json` | the external-check-input/v0 of each check; its `history` holds deal 1's act |
| `inputs/` | the open, check and act bodies |
| `expected_live.json` | each check input decided live under everyday in five variants (below), with the target each history act was read with; sorted canonical JSON |

Regenerate `expected_live.json` with `python -m tests.test_history_act_counterparty`.
`tests/test_history_act_counterparty.py` compares it byte for byte.

## What the records say

- **The act carries its check's counterparty beside it.** Each history entry has `counterparty`,
  the block deal 1's check sealed, exactly as sealed: `hmac-sha256-deal-key` in `x-deal-v0`,
  `hmac-sha256-chain-key` in `typed`. The act record itself names none.
- **Per-deal keys.** In deal 1 the act's `ids.payee` equals the repeat check's; in deal 2 the check's
  differs. `counterparty_profile`, on both the record and the act, is the cross-deal key.
- **No disposition on a booking's act.** capsulectl seals a disposition only on a payment, so each
  act is sealed `fyi` with none.
- **In `x-deal-v0`, the act is an `x-deal-v0` `action` record**, not a typed act record.

## Variants

`as-sealed`; `disposition-accept` (a sealed `accept` disposition added to every history act);
`disposition-accept-no-profile` (that, with `counterparty_profile` removed from the record and the
acts); `disposition-accept-no-profile-no-counterparty` (that, with each act's `counterparty`
removed); `disposition-accept-no-profile-malformed` (that, with each act's `counterparty` in upper
case hex). The added dispositions are the test's, not capsulectl's: those capsules no longer verify,
and the engine does not verify them here.

## Identity

The hotels are synthetic. Only `inputs/` and the user's own request text in each bundle name one; no
check input carries a hotel's name or domain.
