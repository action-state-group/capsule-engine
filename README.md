# capsule-engine

The thin single-node enforcement layer for AI agent actions: the guard that
consumes compiled artifacts as data (never authors compile-time judgments
itself), append-only fold compute over `capsule_emit.account`'s neutral
semantics, the Terms Packs (packs) runtime (loader, schema, catalog packs), policy
activation, and the report computation. It draws no pages; those belong to
[capsule-viewer](https://github.com/action-state-group/capsule-viewer).

Built on top of
[checkpointed-local-log](https://github.com/action-state-group/checkpointed-local-log)
(`cll.log`: the entry log that orders records),
[capsule-emit](https://github.com/action-state-group/capsule-emit)
(`capsule_emit.artifact`: the store that holds each signed record) and the public
[agent-action-capsule](https://github.com/action-state-group/agent-action-capsule)
reference library for capsule parsing. No library code from either is
vendored here; two data files are (a schema and a registry snapshot, below),
and `envcompat.py` is a small helper copied from the archived capsule-ledger.

`capsule-engine` does not depend on the separate `capsule-compiler` package:
it evaluates already-compiled artifacts (folds, packs, policy manifests)
against the stored records. It does carry two small compiler pieces of its own:
`compiler/` (the closed vocabulary and the advisory effect model, which
`packs/loader.py` uses at pack-load time to validate a pack's `outcomes[]`)
and `register/compiler.py` (the sample compiler from an obligations register
to requirements, which produced the EU AI Act packs' outcomes).

## Install

capsule-engine is on PyPI:

```
pip install capsule-engine
```

To work on it, install it from source:

```
git clone https://github.com/action-state-group/capsule-engine.git
cd capsule-engine
python3 -m venv .venv && source .venv/bin/activate
pip install -e ./python
```

`agent-action-capsule` (0.11.0 or later) comes from PyPI. `checkpointed-local-log` and `capsule-emit`
are installed from their GitHub repositories at a pinned revision until their next releases. With these steps
the test suite passes (`pip install pytest "mcp<2"`, then `python -m pytest` in `python/`).

## The `capsule-engine` command

| Command | What it does |
|---|---|
| `capsule-engine init --pack NAME` | Install a starter pack in observe mode: it records, it enforces nothing. |
| `capsule-engine constraints list` | The registered guard checks and the action-class taxonomy. |
| `capsule-engine guard dry-run --store PATH` | Replay stored records through the guard checks and write a JSON report (`--share` includes the sealed capsules). |
| `capsule-engine guard enforce` | Record locally that this install moved from dry-run to enforce. |
| `capsule-engine tenant init` / `upgrade` / `list` | One engine instance per tenant: its own store directory, policy manifest and signing key. |
| `capsule-engine telemetry status` / `funnel` | The telemetry disclosure and opt-in state; the funnel report. |
| `capsule-engine packs propose --pack NAME` | A read-only measurability report for any pack over a JSONL corpus. |
| `capsule-engine packs evaluate --pack DIR [--input FILE]` | Decides a live pack's rows on `{"actions": [...]}` (stdin when no `--input`) and prints JSON: the last `decision` (`allow`, `deny`, `escalate` or `not_evaluable`), and per row in `rows` `met`, `not_met` or `not_evaluable` (a pass on an unevaluable decision, a row no check measures and a declared outcome are `not_evaluable`), the rows whose rule is out of scope for the last action under `excluded_not_applicable` (an in-scope row whose check could not run, such as a missing `amount_minor`, stays `not_evaluable`), and the engine version. Every action needs `at`. Exit 2 when the pack or input is unusable. |
| `capsule-engine verify CAPSULE_ID` | Verify one stored record (`--store`), or a whole bundle file (`--bundle`). |
| `capsule-engine bundle` | Write a self-contained, verifiable slice of the ledger. |
| `capsule-engine referee judge` | The referee a deal's terms name: recompute the shared deal terms and each party's own pack, and seal an Evidence Result in the referee's book (`referee.py`). |

`verify` and `bundle` are present unless `CAPSULE_ARM=guards-only`
(`packaging.py`), which keeps the guard checks and dry-run and hides the
record-query verbs; the default is `full`. Run any command with `--help`.

A store directory (`--store`, or `$CAPSULE_STORE`) holds the engine's records: a
checkpointed-local-log entry log (`log.jsonl`) that orders them, capsule-emit's artifact store
(`artifacts.jsonl`) that holds each signed record, and the producer public keys the store trusts
(`trusted_keys`). Each record carries a producer envelope signed with an Ed25519 key derived from
your own signing secret (`--key-id`/`--secret`); the secret and the derived key are never written
to disk. Opening a store with a new secret adds its key to `trusted_keys`, and nothing removes one.
Keep the old key after a routine rotation: the store reads a record only while its signer's key is
listed. Delete a key only if its secret leaked, knowing that every record it signed then fails to
read (capsule-engine#258 tracks tying key trust to the rotation timeline). `bundle` still reads a
capsule-emit ledger (`--ledger`).

## What's here

- `guards/` — the `GuardEngine`: evaluates a proposed action against
  compiled wickets, e.g. `guards/checks/plan_containment.py`.
- `folds/` — append-only fold compute, consuming `capsule_emit.account`'s
  neutral semantics through its public interface (never re-forking them).
- `packs/` — the packs runtime (loader, schema, corpus verification,
  measurability report) plus the catalog packs (`packs/catalog/`, below).
- `policy/` — the policy manifest: a lockfile of active fold/wicket
  definitions cited by digest, never by copy.
- `report/` — the dry-run report and the Evidence Result, as data (no rendering).
- `referee.py` — the contract-named referee: the shared deal terms under
  `ec:deal-terms@1` and each party's own pack over its own records, as one
  Evidence Result, sealed in the referee's book.
- `mcp/`, `telemetry/`, `holds/` — MCP server surface, telemetry, and
  reservation-as-capsule engine-side plumbing.
- `cli/` — the `capsule-engine` command (above).
- `compiler/` — the closed vocabulary and the advisory effect model the pack
  loader validates `outcomes[]` against (see the second paragraph).
- `register/` — the sample register-to-requirement compiler.
- `conversation/` — the conversation-capsule profile: per-turn capsules,
  chained, a session-close capsule carrying a session digest (an MMR root),
  and a one-capsule-per-exchange form.
- `events/` — builds and seals passive event capsules.
- `registry/` — registry snapshots. `conventions.json` (action-class display
  labels) is loaded at runtime. `cpb_registry.json` is a verbatim copy of
  `scitt-payload-binding`'s `registry.json` at the commit it names
  (`_vendored_commit`); no runtime code loads it, and like any vendored
  snapshot it can fall behind its source: `python/scripts/vendor_cpb_registry.py`
  refreshes it and the `vendor-drift` workflow checks it.
- `tenants.py` — engine-instance-per-tenant provisioning, behind
  `capsule-engine tenant`.
- `packaging.py` — the `CAPSULE_ARM` switch (`full` or `guards-only`).
- `examples/` (in the package) — runnable demos: an offline replay of
  tau2-bench airline trajectories through the guard
  (`python -m capsule_engine.examples.tau2_airline_reference --all`) and a
  tau2 simulation sealed as one conversation-exchange capsule.

### Catalog packs (`python/capsule_engine/packs/catalog/`)

| Pack | `pack_id` | What it is |
|---|---|---|
| `airline-engagement/` | `asg/airline-engagement/1.0.1` | An airline-engagement starter pack, used at judge and report time (its `outcomes[]`). |
| `eu-ai-act/` | `asg/eu-ai-act-obligations/0.1.1` | EU AI Act obligations, backward-only: its outcomes are the sample compiler's output from `register.yaml`. |
| `eu-ai-act-deterministic/` | `asg/eu-ai-act-deterministic/0.1.0` | EU AI Act obligations, backward-only, produced from `../eu-ai-act/obligations-register.yaml` (26 contracts). |
| `inference/` | `asg/inference/0.1.0` | A node's basic policy before it buys an inference: the selected peer's advertised input and output rates (msat per million tokens) against two caps the operator's profile sets, a price that cannot be read refused, a counterparty list, and dedupe. |
| `payments-safety/` | `asg/payments-safety/1.0.0` | Caps per scope and window, dedupe, and verify-before-dispatch on money movement, plus the spend fold. |
| `standard-vendor/` | `asg/standard-vendor/1.0.1` | The standard outcome catalog over the general capsule vocabulary: one pack that grades any vendor's ledger. |

### The Go guard (`go/`)

`go/` is the Go module `github.com/action-state-group/capsule-engine/go`, the
Go implementation of the guard for the everyday, seller and inference packs. The Python
guard above is the reference, and CI decides the shared vectors in
`conformance-vectors/guard/v0` with both on every pull request. See
[go/README.md](go/README.md).

### The admission evaluator (`go/admission`, `rust/`)

`evaluate` for admission (capsule-engine#265): a pinned admission pack, in
admission pack profile v0 (evidencebook-skills `docs/admission-pack-profile-v0.md`:
authored as a `pack-source.yaml` with `scope.kind: admission`, compiled there to
`pack-definition.json`, the JCS bytes a deployment pins), and what was received about a
counterparty give a decision, GO, ASK, LIMIT or NO, with the tier and the rows
that decided it, as the `admission_decision` block a caller seals. The pack states
thresholds; the evaluator does the band, tier, freshness and count math. One
evaluator in two languages: the Go package `go/admission` and the Rust crate
`rust/capsule-engine` (module `admission`, beside `guard`, the Rust port of the
guard for the inference pack). Both decide `conformance-vectors/admission/v0` (the
shared cases, the loader's refusals and `admission-baseline@1`'s fixtures) the
same in CI. Neither reads YAML. The Rust crate is not published; a consumer pins it by git
commit.

A personal agent can admit a Mesh node from the node's `inference-service-history/0` answer
(capsule-engine#273). `InferenceServiceHistoryCounts` (Rust `inference_service_history_counts`)
checks the answer property by property against its definition document: the definition, the
binding, the artifact, the coverage declaration, the units, the labels and the periods. It then
reads the node's served periods. The pack's `card.self_reported_served_periods` row counts closed
periods with a published served total. Those counts are the node's own statement, and the decision
says so. Exchanges the other side's own half confirms come only from the agent's own book. The
evaluator gives no score and no ranking of nodes; it decides about one node at a time, from the
properties it checked.

The node baseline pack (`asg/admission_node_baseline`, capsule-engine#276; source and compile in
evidencebook-skills `packs/admission-node-baseline`) reads three counts of such an answer, each in
its own unit and never added to another: the node's own served periods, the witness receipts the
verifier counted on the card's checkpoints (`card.witness_receipts`, a count over the card's
checkpoints; the witness keys that count sit outside the pack and its digest, with the caller),
and the node's corroborated referee verdicts when its answer carries them
(`card.referee_verdicts`, applied to larger deals only; the four verdict classes stay separate
counts and only the corroborated one is read). A Mesh answer carries no referee verdicts,
so a larger deal with a Mesh node is limited. A count that cannot be read limits and never passes.

For the parts you can use on their own (the Evidence Contract schema and
validator, the obligation register format and its sample compiler, and the
examples, including the Evidence Contracts in `examples/contracts/`), see
[docs/WHATS-IN-THIS-REPO.md](docs/WHATS-IN-THIS-REPO.md).

## License

Apache-2.0. See `LICENSE`.
