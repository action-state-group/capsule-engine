# capsule-engine

The thin single-node enforcement layer for AI agent actions: the guard that
consumes compiled artifacts as data (never authors compile-time judgments
itself), append-only fold compute over `capsule_emit.account`'s neutral
semantics, the packs runtime (loader, schema, catalog packs), policy
activation, and the local console/report/bundle-viewer runtime.

Built on top of [capsule-ledger](https://github.com/action-state-group/capsule-ledger)
(the ledger store, chains, admission; that repository is archived, read-only,
but it is still this package's ledger dependency, see Install) and the public
[agent-action-capsule](https://github.com/action-state-group/agent-action-capsule)
reference library for capsule parsing. No library code from either is
vendored here; two data files are (a schema and a registry snapshot, below),
and `envcompat.py` is a small helper copied from capsule-ledger.

`capsule-engine` does not depend on the separate `capsule-compiler` package:
it evaluates already-compiled artifacts (folds, packs, policy manifests)
against the ledger. It does carry two small compiler pieces of its own:
`compiler/` (the closed vocabulary and the advisory effect model, which
`packs/loader.py` uses at pack-load time to validate a pack's `outcomes[]`)
and `register/compiler.py` (the sample compiler from an obligations register
to requirements, which produced the EU AI Act packs' outcomes).

## Install

capsule-engine is installed from source. Neither it nor capsule-ledger is on
PyPI, so install capsule-ledger from its (archived) repository first:

```
git clone https://github.com/action-state-group/capsule-engine.git
cd capsule-engine
python3 -m venv .venv && source .venv/bin/activate
pip install "capsule-ledger @ git+https://github.com/action-state-group/capsule-ledger.git"
pip install -e .
```

`capsule-emit` comes from PyPI (0.8.6 carries the `ledger_io` and `period`
modules this package uses). pip prints `capsule-emit 0.8.6 does not provide
the extra 'ledger-io'`; that warning is harmless. With these steps the test
suite passes (`pip install pytest "mcp<2"`, then `python -m pytest`).

## The `capsule-engine` command

| Command | What it does |
|---|---|
| `capsule-engine init --pack NAME` | Install a starter pack in observe mode: it records, it enforces nothing. |
| `capsule-engine constraints list` | The registered guard checks and the action-class taxonomy. |
| `capsule-engine guard dry-run --ledger PATH` | Replay a ledger through the guard checks and write a self-contained HTML report. |
| `capsule-engine guard enforce` | Record locally that this install moved from dry-run to enforce. |
| `capsule-engine tenant init` / `upgrade` / `list` | One engine instance per tenant: its own ledger directory, policy manifest and signing key. |
| `capsule-engine telemetry status` / `funnel` | The telemetry disclosure and opt-in state; the funnel report. |
| `capsule-engine packs propose --pack NAME` | A read-only measurability report for any pack over a JSONL corpus. |
| `capsule-engine verify CAPSULE_ID` | Verify one ledger record, or a whole bundle file (`--bundle`). |
| `capsule-engine bundle` | Write a self-contained, verifiable slice of the ledger. |
| `capsule-engine console` | Serve the local console over the real ledger. |

`verify`, `bundle` and `console` are present unless `CAPSULE_LEDGER_ARM=guards-only`
(`packaging.py`), which keeps the guard checks and dry-run and hides the
record-query verbs; the default is `full`. Run any command with `--help`.

## What's here

- `guards/` — the `GuardEngine`: evaluates a proposed action against
  compiled wickets, e.g. `guards/checks/plan_containment.py`.
- `folds/` — append-only fold compute, consuming `capsule_emit.account`'s
  neutral semantics through its public interface (never re-forking them).
- `packs/` — the packs runtime (loader, schema, corpus verification,
  measurability report) plus the catalog packs (`packs/catalog/`, below).
- `policy/` — the policy manifest: a lockfile of active fold/wicket
  definitions cited by digest, never by copy.
- `console/`, `report/`, `bundle_viewer/` — the local investigation, report,
  and offline-verify surfaces.
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
  snapshot it can fall behind its source: `scripts/vendor_cpb_registry.py`
  refreshes it and the `vendor-drift` workflow checks it.
- `tenants.py` — engine-instance-per-tenant provisioning, behind
  `capsule-engine tenant`.
- `packaging.py` — the `CAPSULE_LEDGER_ARM` switch (`full` or `guards-only`).
- `examples/` (in the package) — runnable demos: an offline replay of
  tau2-bench airline trajectories through the guard
  (`python -m capsule_engine.examples.tau2_airline_reference --all`) and a
  tau2 simulation sealed as one conversation-exchange capsule.

### Catalog packs (`capsule_engine/packs/catalog/`)

| Pack | `pack_id` | What it is |
|---|---|---|
| `airline-engagement/` | `asg/airline-engagement/1.0.1` | An airline-engagement starter pack, used at judge and report time (its `outcomes[]`). |
| `eu-ai-act/` | `asg/eu-ai-act-obligations/0.1.1` | EU AI Act obligations, backward-only: its outcomes are the sample compiler's output from `register.yaml`. |
| `eu-ai-act-deterministic/` | `asg/eu-ai-act-deterministic/0.1.0` | EU AI Act obligations, backward-only, produced from `../eu-ai-act/obligations-register.yaml` (26 contracts). |
| `payments-safety/` | `asg/payments-safety/1.0.0` | Caps per scope and window, dedupe, and verify-before-dispatch on money movement, plus the spend fold. |
| `standard-vendor/` | `asg/standard-vendor/1.0.1` | The standard outcome catalog over the general capsule vocabulary: one pack that grades any vendor's ledger. |

For the parts you can use on their own (the Evidence Contract schema and
validator, the obligation register format and its sample compiler, and the
examples, including the Evidence Contracts in `examples/contracts/`), see
[docs/WHATS-IN-THIS-REPO.md](docs/WHATS-IN-THIS-REPO.md).

## License

Apache-2.0. See `LICENSE`.
