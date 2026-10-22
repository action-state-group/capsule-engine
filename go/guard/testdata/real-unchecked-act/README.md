# Real unchecked acts (capsulectl, after a paused check)

Two deals on one throwaway deal profile, one sealed in x-deal-v0 records and one in typed records. In
each, the purchase is checked, the pinned rules checker answers `not_evaluable` so the check pauses,
the agent pays anyway with no sealed approval, and the same purchase is checked again. capsulectl seals
the payment unchecked (`unchecked: true`, rule `no_sealed_approval`): an x-deal-v0 `outcome` record
whose `body.unchecked` is the act, or a typed `action-outcome/v0` whose `body.attempted` is. Neither
carries a disposition, an action class, a taxonomy version or a payee.

## Producer

- capsule-cli `main` at `53b702f7d673207f1f016dae27cca285a35d51ac` (after #180), built from
  `git archive` of that commit with `CGO_ENABLED=0 go build -trimpath -ldflags "-s -w -buildid= -X
  …cliVersion=main-53b702f -X …cliCommit=53b702f"`. The producer block says `main-53b702f`.
- `build.sh` holds the exact commands:
  `build.sh <capsulectl> <capsule-cli>/skills/deal/profile/materiality-predicate/neutral.json <dir>`,
  with `inputs/` copied to `<dir>/in/`. Throwaway `HOME`, `XDG_CONFIG_HOME` and plugin root under
  `<dir>`; the profile `synthetic-unchecked`. Nothing is sent anywhere. The pinned rules checker is a
  local capture script that saves the `external-check-input/v0` it is given.
- A re-run makes other keys, ids and timestamps: the files here are the fixture, replaced together.

## Files

| file | what |
|---|---|
| `x-deal-v0-check-2.input.json` | the envelope capsulectl sent at the second check of the x-deal-v0 deal: its history is that deal's unchecked act |
| `typed-check-1.input.json`, `typed-check-2.input.json` | the envelopes at the typed deal's two checks: the first holds the x-deal-v0 deal's unchecked act, the second both deals' (one of each shape) |
| `x-deal-v0-act.json`, `typed-act.json` | capsulectl's answer to each `deal note --kind act`: `unchecked: true`, rule `no_sealed_approval` |
| `x-deal-v0.bundle.json`, `typed.bundle.json` | each deal's own copy (`bundle --deal`) |
| `engine-replay.json` | capsule-engine's replay of each bundle under the catalog everyday pack (`generate_engine_replay.py`, at the commit it names): the caps result and evidence of every record that gets a decision |

## The known difference, closed

At capsule-engine `c878edf0` the replay gave the unchecked act no decision and its spend window summed
accepted decisions only, so the second check saw `weekly_spend_minor` 0, where this plugin counts the act's
2000 (ruling (a)); the engine side filed a follow-up, on 2026-10-09, for replay to count executed
acts as spend. capsule-engine #138 (`a710636c`,
asg/everyday/0.3.6, spend.weekly/3.1.0) counts it: `engine-replay.json` regenerated there gives 2000, and
`TestTheEngineReplayCountsTheUncheckedSpendAsThisPortDoes` holds the engine's window to this plugin's.

The rulings named here (ruling (a), ruling (b), the G3 ruling) are defined in the package doc of
`go/guard` (`action.go`).
