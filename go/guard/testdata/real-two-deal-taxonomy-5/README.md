# A real two-deal bundle (capsulectl v0.1.0-rc13)

Two deals on one throwaway deal profile. Each is a purchase of the same item from the same
synthetic merchant, checked and paid for the same amount. Every byte of the four bundles is what
capsulectl wrote. `capsulectl verify --bundle` reports each one `VALID`.

## Producer

- capsule-cli tag `v0.1.0-rc13`, commit `7eb05ac1c277f4a7dad1ece8b1a1aec2df7eac11`.
- Built from `git archive v0.1.0-rc13` with `CGO_ENABLED=0 go build -trimpath -o capsulectl
  ./cmd/capsulectl`. The `-ldflags -X` set `internal/cli.cliVersion` to `v0.1.0-rc13` and
  `internal/cli.cliCommit` to the commit above, as `scripts/release-build.sh` does.

- `capsulectl --version` prints `capsulectl v0.1.0-rc13 (commit 7eb05ac1c277f4a7dad1ece8b1a1aec2df7eac11)`.

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
| `expected_decisions.json` | the engine's decision for every record of the two own copies, replayed in that order under `asg/everyday/0.3.3`; sorted canonical JSON |

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

## Where the replay differs from the expectation

The two failing expectations are pinned as strict xfails in the test. They are not written into
`expected_decisions.json` as passes.

- **r06 on deal 2 FAILS** (`prior_count` 0), though its target is the profile fingerprint deal 1's
  check also had. A replay has no accepted earlier act: deal 1's check escalates (r02, r06), and
  no sealed approval or action is replayed as its acceptance.
- **The companions are DENIED, not read as not applicable.** Under the pack, `action_class_gate`
  fails closed on a record with no action class, as it does on every record that states no act
  (baseline, verdict, approval, action, report).
- **The report capsules trip r27** (dedupe), on deal 1's second report and both of deal 2's.
- **The shares withhold the companion, but still list it.** No share discloses a
  `counterparty_profile` record or carries its fingerprint. Each share does carry the companion's
  sealed capsule (digests and signature), and one step marked withheld with
  `"kind":"counterparty_profile"`.
