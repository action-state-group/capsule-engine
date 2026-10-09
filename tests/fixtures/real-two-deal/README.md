# A real two-deal bundle (capsulectl, taxonomy 6)

Two deals on one throwaway deal profile. Each is a purchase of the same item from the same
synthetic merchant, checked and paid for the same amount. Every byte of the four bundles is what
capsulectl wrote. `capsulectl verify --bundle` reports each one `VALID`. Each check seals
`taxonomy_version` `6`.

## Producer

- capsule-cli commit `d1615229de65a17e250594ea4e1f456671c12eba`: the head of capsule-cli pull
  request #169 ("deal: seal taxonomy version 6 on new records"), not yet merged. The released
  `v0.1.0-rc13` still seals taxonomy `5`.
- Built from `git archive d1615229de65a17e250594ea4e1f456671c12eba` with `CGO_ENABLED=0 go build
  -trimpath -o capsulectl ./cmd/capsulectl`. The `-ldflags -X` set `internal/cli.cliVersion` to
  `v0.1.0-rc13-4-gd161522` (`git describe --tags` of that commit) and `internal/cli.cliCommit` to the
  commit above, as `scripts/release-build.sh` does.
- `capsulectl --version` prints
  `capsulectl v0.1.0-rc13-4-gd161522 (commit d1615229de65a17e250594ea4e1f456671c12eba)`.
- Once #169 merges, rebuild from the merged commit and run `build.sh` again. A re-run makes other
  keys, ids and timestamps, so the bundles, the digests pinned in the test and
  `expected_decisions.json` are replaced together.

## Commands

`build.sh` holds the exact commands. Run it as:

```sh
build.sh <capsulectl> <capsule-cli>/skills/deal/profile/materiality-predicate/neutral.json <dir>
```

`<dir>` must hold `inputs/` (copied to `<dir>/in/`). The script uses a throwaway `HOME` and
`XDG_CONFIG_HOME` under `<dir>`, and the profile `synthetic-two-deal`. Nothing is sent anywhere:
no `deal tick`, no cadence and no remote checker. No rules checker is pinned, so each check seals
`rules: not_configured`.

1. `deal init`, then deal 1: `deal open`, then `deal check` (pay 2000 USD), then `deal note --kind act`.
2. Deal 2: the same open and check, then an act with another order reference.
3. Each deal's counterparty copy: `disclose --deal ID --share counterparty --to counterparty`.
4. Each deal's own copy, last, so it holds every record: `bundle --deal ID`.

Re-running produces other keys, ids and timestamps, so the bytes here are the fixture and the
script is the record of how they were made.

## Files

| file | what |
|---|---|
| `deal-1.bundle.json`, `deal-2.bundle.json` | the user's own copy (`bundle --deal`), replayed by the engine |
| `deal-1.counterparty.bundle.json`, `deal-2.counterparty.bundle.json` | the counterparty's shared copy (`disclose --share counterparty`) |
| `inputs/` | the `deal open`, `deal check` and `deal note --kind act` bodies |
| `expected_decisions.json` | the engine's decision for every record of the two own copies that gets one (the two checks), replayed in that order under `asg/everyday/0.3.4`; sorted canonical JSON |

Regenerate `expected_decisions.json` with `python -m tests.test_real_two_deal_bundle`.
`tests/test_real_two_deal_bundle.py` compares it byte for byte.

## Identity

- The operator is the synthetic profile name `synthetic-two-deal`.
- The merchant is `Example Merchant` at `example-merchant.test`.
- Every deal record seals the merchant as HMAC fingerprints. Each check is keyed per deal, and each
  `counterparty_profile` companion is keyed per profile.
- The merchant's name appears in clear in one place only: the report capsule of the user's own
  copy (the user's own words and what was done).
- No share names it. There is no email address, handle or local path anywhere.

## What the replay decides

Only a check states an act, so only the two checks get a decision.

- **Deal 1's check asks.** The merchant is new: r02 and r06 fail on `counterparty_seen_before`.
- **Deal 2's check reads the merchant as seen** (r02 and r06 pass, `prior_count` 1). Deal 1's act was
  carried out: its sealed approval (`proceed: true`) approves the verdict on that check, and the
  sealed action step names that approval under `authorized_by`. The replay counts the act as an
  earlier one with that merchant (`counterparty_seen_before/3.0.0`), keyed on the profile
  fingerprint both companions carry.
- **Deal 2's check asks on r27**: it is the same act as deal 1's, in another deal (`dedupe`).
- **No other record gets a decision**: the companions, baselines, verdicts, approvals, action steps
  and both reports of each deal. No rule fires on them, the gate included. The counterparty's
  report is withheld in the user's own copy, so nothing in it can be read.
- **The shares withhold the companion, but still list it.** No share discloses a
  `counterparty_profile` record or carries its fingerprint. Each share does carry the companion's
  sealed capsule (digests and signature), and one step marked withheld with
  `"kind":"counterparty_profile"`.
