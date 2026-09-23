# What's open, what's ours, what's operated

A public-safe projection of the workspace's own repo classification. It does
not create the classification — that line is a standing company decision
(Amendment J) — it just states the result in one place an adopter can read
without a private repo checkout.

This document names repos and their class. It does not describe what any
private repo contains.

## The three classes

**Donatable** — the open standard and the neutral libraries a counterparty
needs to interoperate, intended for a neutral foundation:

| Repo | What it is |
| --- | --- |
| `agent-action-capsule` | The IETF profile (the standard) + neutral reference library + test vectors |
| `scitt-cose` | Vendor-neutral RFC9162_SHA256 receipt verifier |
| `capsule-emit` | Neutral capsule producer/emission layer |
| `capsule-anchor` | Neutral SCITT Transparency Service (the witness) |
| `capsule-registry` | Semantics registry: composition slot profiles, action-type/outcome conventions, purpose labels |
| `capsule-viewer` | Offline bundle-verification viewer |
| `capsule-gate-hermes` | Runtime adapter for `capsule-emit` |
| `agentactioncapsule-site` | The standard's public site and docs |

**Company OSS** — built and roadmapped by us, shipped open:

| Repo | What it is |
| --- | --- |
| `capsule-engine` | The thin single-node enforcement engine: guard, append-only fold compute, packs runtime, local console/report/bundle-viewer |
| `capsule-skills` | The evaluation/obligations compiler, packaged as installable skills |
| `capsule-cli` | CLI plugins we ship open |

**Private** — named by function, not by repo name; nothing about what these
contain beyond the function itself:

- **The operated layer** — running the ledger as a hosted service, plural
  witnessing on by default, hosted attainment/reports, fleet console, OEM
  provisioning.
- **The countersign / Authority tier** — the liability-bearing second
  signature issued after independent recomputation.
- **The engine planner/executor** — the proprietary reasoning and execution
  internals that sit upstream of the public guard.
- **Licence issuance** — entitlement and activation for the operated and
  Authority tiers.
- **Guaranteed evidence delivery** — two-party delivery of evidence with
  proof, operated on request.

## Dependency direction

Public repos never import from a private repo. This is a CI rule, not a
convention: a public repo whose dependency graph resolves to a private
import fails its build. The scanning mechanism that enforces this against
live public clones is tracked separately (`[engine-boundary-scan-public-clones]`,
not yet landed as of this writing) — this document states the rule; that
item is where the rule becomes a running check.

## Licence per repo

Every repo in the donatable and company OSS columns above ships
`Apache-2.0`. Every repo in the private column is proprietary, with no
public licence terms.

## Not in this document

Pricing, packs-as-products, and Authority/countersign terms are not covered
here — see the company site for those.

## Change log

- 2026-09-22 — drafted from the workspace's private classification (Amendment
  J). The one-sentence public test of the boundary ("everything a stranger
  can check is donated; the party that runs it … is paid") is held out of
  this draft pending Steven's ruling on whether it is public-safe language;
  see the routing note in the ledger lane outbox. Column headings stand on
  their own until that ruling lands.
- Steven's ruling: **pending.**
