# capsule-engine

The thin single-node enforcement layer for AI agent actions: the guard that
consumes compiled artifacts as data (never authors compile-time judgments
itself), append-only fold compute over `capsule_emit.account`'s neutral
semantics, the packs runtime (loader, schema, catalog packs), policy
activation, and the local console/report/bundle-viewer runtime.

Built on top of [capsule-ledger](https://github.com/action-state-group/capsule-ledger)
(the ledger store, chains, admission) and the public
[agent-action-capsule](https://github.com/action-state-group/agent-action-capsule)
reference library for capsule parsing — never vendored into this repo.
`capsule-engine` needs zero compiler imports: it evaluates already-compiled
artifacts (folds, packs, policy manifests) against the ledger, it does not
compile them.

## Install

```
git clone https://github.com/action-state-group/capsule-engine.git
cd capsule-engine
python3 -m venv .venv && source .venv/bin/activate
pip install "capsule-ledger @ git+https://github.com/action-state-group/capsule-ledger.git"
pip install -e .
```

## What's here

- `guards/` — the `GuardEngine`: evaluates a proposed action against
  compiled wickets, e.g. `guards/checks/plan_containment.py`.
- `folds/` — append-only fold compute, consuming `capsule_emit.account`'s
  neutral semantics through its public interface (never re-forking them).
- `packs/` — the packs runtime (loader, schema, corpus verification,
  measurability report) plus the standard catalogs (`packs/catalog/`)
  published from the company side.
- `policy/` — the policy manifest: a lockfile of active fold/wicket
  definitions cited by digest, never by copy.
- `console/`, `report/`, `bundle_viewer/` — the local investigation, report,
  and offline-verify surfaces.
- `mcp/`, `telemetry/`, `holds/` — MCP server surface, telemetry, and
  reservation-as-capsule engine-side plumbing.

## License

Apache-2.0. See `LICENSE`.
