# A real seller fixture (capsulectl, taxonomy 6)

Two sales of one synthetic item on one throwaway profile, each asking 1900 with a floor of 1700.
Every byte of the three bundles and the five check inputs is what capsulectl wrote.

## Producer

- capsule-cli commit `831afeeb9fd76b7196486a9af38ca455b1230791`, the producer of
  `../real-two-deal/`, built the same way (`git archive`, `CGO_ENABLED=0 go build -trimpath
  -ldflags "-s -w -buildid= -X …"`).
- `capsulectl --version` prints
  `capsulectl v0.1.0-rc13-6-g831afee (commit 831afeeb9fd76b7196486a9af38ca455b1230791)`.
- A re-run makes other keys, ids and timestamps, so the bundles, the check inputs, the digests
  pinned in the test and `expected_decisions.json` are replaced together.

## Commands

`build.sh` holds the exact commands. Run it as:

```sh
build.sh <capsulectl> <capsule-cli>/skills/deal/profile/materiality-predicate/neutral.json <dir>
```

`<dir>` must hold `inputs/` (copied to `<dir>/in/`). The script uses a throwaway `HOME`,
`XDG_CONFIG_HOME` and plugin root under `<dir>`, and the profile `synthetic-seller`. Nothing is sent
anywhere: no `deal tick`, no cadence and no remote checker. A local stub rules checker is pinned
only to keep each external-check-input/v0 capsulectl hands it; it allows and names no rule.

1. Sale 1 (`deal sale new`), with two buyer threads (`deal open --sale`).
2. Buyer A: a claim of the item's condition (`source_kind` agent, class `condition`), an offer at
   1900, the act, the buyer's acceptance of that offer, then a commit at 1900 and its act.
3. Buyer B: the same claim, then an offer at 1500. It pauses on the floor and is never answered.
4. Sale 2, one buyer, C: the claim, an offer at 1600 answered with the user's words, its act,
   C's acceptance, then a commit at 1600, which pauses on the floor and is answered. No act follows.
5. Each thread's own copy, last, so it holds every record: `bundle --deal`.

## Files

| file | what |
|---|---|
| `buyer-a.bundle.json`, `buyer-b.bundle.json`, `buyer-c.bundle.json` | each thread's own copy, replayed by the engine in that order |
| `check-inputs/<thread>-<action>-<amount>.json` | the external-check-input/v0 of each check, as the pinned checker read it: the record, its task-authority record, the floor's opening, the sale's `item_ref` and `party_role` `seller` |
| `inputs/` | the sale, thread, claim, check and act bodies |
| `expected_decisions.json` | the replay's decision for every record that gets one, then the live decision on each check input, under `asg/seller/0.1.3`; sorted canonical JSON |

Regenerate `expected_decisions.json` with `python -m tests.test_real_seller_bundle`.
`tests/test_real_seller_bundle.py` compares it byte for byte.

## Identity

The buyers are `Example Buyer A`, `B` and `C` at `buyer-a.example`, `buyer-b.example` and
`buyer-c.example`, and the item is `example bicycle`. Only `inputs/` names a buyer: no bundle
or check input carries a buyer's name or domain, since the records seal the buyers as
fingerprints. The claim's words are sealed as a commitment; each bundle is the user's own copy,
so it also carries their opening. The
check inputs carry the floor's opening and the sale's item reference in clear, as capsulectl gives
them to the user's own checker; both are synthetic.

## What the records say

- **A sale is spend 0 and states its price.** Every offer and commit seals `spend_minor` `0` and
  the price as `amount_minor`. `price_floor` reads the stated price.
- **A commit is `agreement.accept`, an offer is `external_commitment.other`.** capsulectl classes
  no deal record `marketplace.offer` or `marketplace.sale`.
- **Only a `proposed-action/v0` gets a decision.** The task authority, the evaluations, the
  approvals (the user's and the buyer's acceptance), and the action records state no act.
- **A commit is dated by the offer the buyer accepted.** In a replay, each commit's `proposal_at`
  is the sealed time of the deal's latest offer that a buyer's acceptance names, with no change of
  details after it, so `offer_expiry` passes.
- **Live, the floor holds on a commit.** A's commit at 1900 passes `price_floor`; C's at 1600 fails
  it and asks. Neither states the floor.

## Still open

Each is a strict `xfail` in the test:

- An offer is `external_commitment.other`, a class no seller check names, so an offer under the
  floor is not checked against it.
- A commit's check input carries no `proposal_at` and no acceptance, so live, `offer_expiry`
  cannot date the offer and asks on every commit.
- A check input's history holds acts only, never the thread's claim, so live, s05 finds no
  statement in the deal and asks on every commit; the replay reads the claim in the bundle and
  passes it.
- A typed check names no payee, and a sale's spend is 0, so in a replay `dedupe` reads offers to
  different buyers at different prices as one act.
