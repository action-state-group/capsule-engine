# Changelog: capsule-engine (Python)

The Python package, released to PyPI as `capsule-engine` under `python/v<version>` tags. The Go
module has its own changelog, `go/CHANGELOG.md`.

## Unreleased

- Added (capsule-engine#162): `capsulectl-engine`, a capsulectl plugin (`cli-plugin/v1`).
  `capsulectl engine check --ruleset FILE` reads one external-check-input/v0 on stdin and prints
  one external-check-result/v0, tier `recomputed`, with a finding per clause of an
  `engine-ruleset/v0` JSON file (`schemas/engine-ruleset-v0.json`). The result's `definition_digest`
  is SHA-256 over the JCS bytes of the ruleset. The same launcher pins as a deal profile's rules
  checker. The first check is `record_type_coverage/1` over the history; an empty declaration is
  `not_applicable`, never a verdict. See docs/WHATS-IN-THIS-REPO.md.
- Changed: `evaluate_record_type_coverage` counts only a string as a kind. An object or a list at
  `kind_field` raised `TypeError`; it is now not a kind.
- Changed (capsule-engine#217): `GuardDecision.verdict` of a decision held only because the
  caps window could not be evaluated (`asked_unevaluated_checks`), with nothing failed and an
  approver for the class, is its outcome, `escalate`, no longer `not_evaluable`. With no
  approver the decision is still refused (`deny`) and its verdict is still `not_evaluable`.
  Outcomes and sealed capsules do not change.
- Changed (capsule-engine#214 hold A, Steven's ruling A, ops 5053da83): what the caps window
  counts for an accepted act in a live check's history, where it can now only rise.
  - An accepted act that seals no `spend_minor` counts at its stated `amount_minor`, never below
    0, under every fold. Under the frozen spend.weekly/3.0.0 and 1.0.0 (everyday 0.3.4 and 0.3.5,
    seller 0.1.0 and 0.1.1) it was left out of the total.
  - Under spend.weekly/3.1.0 (everyday 0.3.6 to 0.3.8) an accepted act counts at the larger of
    its stated `amount_minor` and its `spend_minor`, in a live check's history and in a replay's
    record of an executed act. It counted at the stated amount, so a spend above it was left out.
    A partial charge (spend below the stated amount) still counts at the stated amount.
  - Under 3.0.0 and 1.0.0 an act that seals a `spend_minor` still counts at it.

  Outcome change: such a check can move from allow to escalate (asking for approval), or to deny
  with no approver for the class. The engine now decides as the Go guard (go/v0.1.3) does. A live
  history's record of such an act carries the stated amount as `stated_amount_minor` (ledger
  state, never sealed, never read by dedupe).

## 0.1.0 — 2026-10-23

- First PyPI release of the package. It installs the `capsule_engine` package and the
  `capsule-engine` command.
