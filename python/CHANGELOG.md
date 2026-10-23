# Changelog: capsule-engine (Python)

The Python package, released to PyPI as `capsule-engine` under `python/v<version>` tags. The Go
module has its own changelog, `go/CHANGELOG.md`.

## Unreleased

- Changed (capsule-engine#217): `GuardDecision.verdict` of a decision held only because the
  caps window could not be evaluated (`asked_unevaluated_checks`), with nothing failed and an
  approver for the class, is its outcome, `escalate`, no longer `not_evaluable`. With no
  approver the decision is still refused (`deny`) and its verdict is still `not_evaluable`.
  Outcomes and sealed capsules do not change.

## 0.1.0 — 2026-10-23

- First PyPI release of the package. It installs the `capsule_engine` package and the
  `capsule-engine` command.
