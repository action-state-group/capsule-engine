# The tau2-airline reference: guard decisions on real agent trajectories

A stranger with this repo and no account, no API key, and no network can
replay real agent tool-call trajectories through the actual `GuardEngine`
and watch it deny an unscripted policy violation — not a hand-written test
fixture built to pass.

## Run it (offline, under a minute)

```console
$ pip install -e ".[dev]"
$ python -m capsule_engine.examples.tau2_airline_reference --all --out-dir /tmp/tau2-airline
dataset                     ALLOW   DENY   WITH-INSTRUMENTATION   sample refusal capsule
pilot1-gemini-2-5-flash         9      4                      0   f42fe531878454c8… (cancel_reservation, task 7)
  -> 22 capsule(s) written to /tmp/tau2-airline/pilot1-gemini-2-5-flash.jsonl
  -> verify one row offline:  capsule verify f42fe531878454c892488227c05de581068461ad26d1fac8fb2af7d4bd80345a --ledger /tmp/tau2-airline/pilot1-gemini-2-5-flash.jsonl
tau2-claude-3-7-sonnet         24     24                      0   5924a1a4e5c353b0… (cancel_reservation, task 1)
  -> 72 capsule(s) written to /tmp/tau2-airline/tau2-claude-3-7-sonnet.jsonl
  ...
tau2-gpt-4-1                   26     12                      1   c5bf5bd64b0d1fa6… (cancel_reservation, task 1)
  -> WITH-INSTRUMENTATION example: capsule 865986e82de9aafc… (cancel_reservation, task 48): no prior
     get_reservation_details for this reservation_id in the replayed log -- eligibility cannot be
     evaluated without a live DB read or an instrumented snapshot connector
  ...
tau2-o4-mini                   19     12                      4   6dc692748efe47b0… (cancel_reservation, task 1)
```

Then verify one denied row independently, with a different command than the
one that wrote it:

```console
$ capsule verify f42fe531878454c892488227c05de581068461ad26d1fac8fb2af7d4bd80345a --ledger /tmp/tau2-airline/pilot1-gemini-2-5-flash.jsonl
✓ verifies · f42fe531878454c892488227c05de581068461ad26d1fac8fb2af7d4bd80345a
```

No live model calls happen anywhere in this path. Every number above is
real and reproducible from the committed data — see
`tests/test_tau2_airline_reference.py`.

## What's being replayed, and why it isn't live inference

Five datasets, vendored under `capsule_engine/examples/data/tau2_airline/`
(provenance recorded in that directory's `PROVENANCE.json`):

| dataset | source | model | tasks |
|---|---|---|---|
| `pilot1-gemini-2-5-flash` | record-grounding-bench pilot-1, a **live** 24-task shift run | `vertex_ai/gemini-2.5-flash` | 24 |
| `tau2-claude-3-7-sonnet` | [tau2-bench](https://github.com/sierra-research/tau2-bench)'s own committed 4-trial airline results, trial 0 | claude-3-7-sonnet-20250219 | 50 |
| `tau2-gpt-4-1` | tau2-bench committed results, trial 0 | gpt-4.1-2025-04-14 | 50 |
| `tau2-gpt-4-1-mini` | tau2-bench committed results, trial 0 | gpt-4.1-mini-2025-04-14 | 50 |
| `tau2-o4-mini` | tau2-bench committed results, trial 0 | o4-mini-2025-04-16 | 50 |

Four of the five were never run by this project at all — they're
tau2-bench's own published benchmark transcripts, real recorded tool calls
from real agent runs against their airline domain, flattened into one
tool-call-per-line JSONL and replayed here. The fifth
(`pilot1-gemini-2-5-flash`) is the one live run this project actually
executed, reported in record-grounding-bench's `docs/pilot-1-report.md`
(2026-08-15): 24 tasks, $0.4813, 24/24 completed, 142 raw tool-call events.

**Loading and replaying already-run trajectories, offline, is the point of
this reference** — not generating new ones. A live-inference path (real API
keys, real cost, nondeterministic, dies on conference wifi) is explicitly
out of scope here; five minutes and no network is the bar.

## What "replay" actually checks

For every `cancel_reservation` and `update_reservation_flights` call in a
dataset, this reference re-derives the reservation's state *before* that
call from the most recent prior `get_reservation_details` result for the
same `reservation_id`, within the same task — exactly what a reader of the
tool-call log alone (no live DB) can reconstruct — and evaluates two of
tau2-bench airline's real `policy.md` rules against it:

- **`update_reservation_flights`**: *"Basic economy flights cannot be
  modified."* — refused outright if the reservation is basic economy.
- **`cancel_reservation`**: cancellable if booked within 24 hours or
  business cabin (narrowed from `policy.md`'s full rule — see the
  `_cancel_eligibility` docstring in
  `capsule_engine/examples/tau2_airline_reference.py` for exactly what's
  out of scope and why).

Every replayed call goes through a real `GuardEngine.check()` and produces
a real, signed, chained capsule. A denial is never this module's own
unilateral decision — it cites a real-or-dangling mandate capsule id and
lets `verify_before_dispatch` do the actual denying, the same
mandate-citation mechanism record-grounding-bench's manifest classifier
uses (independently re-derived here; this reference has no dependency on
record-grounding-bench).

## The headline: the same refusal, five different models

Task 17 asks the agent to change a basic-economy reservation's flights.
**Every one of the five datasets — one live model and four independently
generated committed transcripts — denies it, for the same reason.** This
isn't a fixture built to demonstrate the guard; it's five different agents,
none of them aware this reference exists, hitting the same real policy
constraint and getting refused in five separate, real, unforced trajectories.
`tests/test_tau2_airline_reference.py::test_task_17_basic_economy_denial_is_consistent_across_every_model`
checks this mechanically, not just in this doc's prose.

## WITH-INSTRUMENTATION: an honest capability gap, not a guess

When no prior `get_reservation_details` exists for a call's reservation
within the same task, this reference has no mechanical way to know whether
the call was eligible. It does not guess allow (silently trusting an
ungated call) or guess deny (inventing a violation that was never checked).
The action goes through uncited — `verify_before_dispatch` correctly reads
that as "n/a", not "fail" — and the capsule's own `extra.predicate_evaluable`
field, plus this module's printed summary, states the gap plainly. This
happens for real, unforced, in the vendored `tau2-gpt-4-1` (1 case) and
`tau2-o4-mini` (4 cases) data.

## Counts are never blended

Each dataset gets its own developer identity, its own signing key, and its
own independent capsule chain. The comparison table's whole point is
per-model rows — comparing how five different models behave against the
*same* two policy predicates over the *same* 50 tasks — never a single
combined number.

## What this reference deliberately does not cover, and why

- **Retail domain, and the advisory-vs-execute effect-model gap (Gap 1).**
  The original brief for this reference specified a retail-domain build
  first (retail has both recommend-only and execute paths). That was
  superseded: the operator's authoritative direction for this pass was to
  ship the airline **load-already-run-data** path first, since that's real,
  vendorable today with no new live runs, and to re-raise retail as
  separate follow-up work rather than let it delay this. Airline stays the
  cost/feasibility proof and the source of the task-17/18 unscripted-guard
  asset; retail is not silently dropped, it's explicitly deferred.
- **Live model calls / a "run it yourself against a live model" path.**
  Out of scope by design — see above.
- **Cross-task agent memory.** record-grounding-bench's own pilot-1 run
  doesn't carry agent conversational memory across tasks in a shift (see
  its `docs/pilot-1-report.md`); this reference inherits that same scoping
  from the data it replays.
- **The other four airline WRITE tools** (`book_reservation`,
  `send_certificate`, `update_reservation_baggages`,
  `update_reservation_passengers`). No capsule is emitted for these in
  this reference — they're real tools in the vendored data, but this
  reference only evaluates the two whose eligibility it can mechanically
  check and that produce this reference's refusals. Widening coverage to
  all six is real, separate follow-up work.

## Regenerating the vendored data

`scripts/vendor_tau2_airline_reference_data.py` is the (non-runtime)
extraction script that produced the five files under
`capsule_engine/examples/data/tau2_airline/`. Its own docstring has the
exact regeneration steps and the upstream source (tau2-bench's public
`data/tau2/results/final/*_airline_*_4trials.json`, plus
record-grounding-bench's pilot-1 log). Provenance for every vendored file
— source repo, source filename, the exact upstream `git_commit` each
result set was generated against, trial index used, event counts — is
recorded in `capsule_engine/examples/data/tau2_airline/PROVENANCE.json`.
