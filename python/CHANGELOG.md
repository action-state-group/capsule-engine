# Changelog: capsule-engine (Python)

The Python package, released to PyPI as `capsule-engine` under `python/v<version>` tags. The Go
module has its own changelog, `go/CHANGELOG.md`.

## Unreleased

- **Breaking** (capsule-engine#175): the engine's three rendering surfaces are removed. The engine
  computes; pages are drawn by capsule-viewer. Removed released verbs and entry points:
  `capsule-engine console`; `capsule-engine bundle --with-viewer` and `--viewer-out` (the bundle file
  and its verify link are unchanged); the `capsule_engine.bundle_viewer` and `capsule_engine.console`
  packages; `capsule_engine.report.render` (`render_report_html`, `encode_fragment`, `decode_fragment`,
  `TelemetryConfig`) with its `verify.js`. There is no like-for-like replacement here. To draw an
  evidence file, use `capsule_viewer.evidence_report.render_report_html(bundle, check)` or
  `check_and_render(bundle)`; these draw a bundle, not a dry-run report. `guard dry-run` now writes the
  report as JSON (the former `to_fragment_payload` is `capsule_engine.report.to_payload`) and no page is
  drawn for it today. An existing `--out report.html` now gets JSON inside a `.html` file; the default
  `--out` is `dry-run-report.json`. Rows carry capsule bodies only with `--share`, still refused when one
  holds a local-only field, and `--share` no longer prints a URL. `--telemetry-opt-in` and
  `--telemetry-endpoint` are removed with the HTML beacon, and `CAPSULE_LEDGER_TELEMETRY_ENDPOINT` is now
  ignored without a warning. Drawing a withheld payload beside its digest moves to
  action-state-group/capsule-viewer#105. The next release takes a minor bump.
- Changed (capsule-engine#234, ruling A on #214 hold A): under spend.weekly/3.1.0, a live check's
  history now lets a cancel or refund free the headroom of the partial charge it cites. It lowers
  that charge's counted amount by the amount it returns, never below the charge's sealed
  `spend_minor`. It must state the money moved in, cite the charge's `capsule_id` under chain
  relation `supersedes`, carry the charge's operator and developer, and be sealed between the
  charge and the check; nothing is matched on amount, merchant or time alone. Any other
  cancel or refund frees nothing, as before. The fold, its definition and the packs do not
  change, and nor does any sealed record shape: the lowering is applied to the charge's executed
  record (`report/replay.py` `_history_freed`). The caps evidence still counts such a reversal
  as unlinked. Frozen folds (spend.weekly/3.0.0 and 1.0.0) are unchanged. Go does the same
  (go/CHANGELOG.md); new vectors in `history-spend.json`.
- Changed (capsule-engine#182): `packs.obligation_results` words an `action_class_gate` rule's reason
  without a selector or an action-class name. Rules 11 and 12 read "Asks: first time sharing your
  {class} with {party}", filled from the role and classes the `recipient_role` check recorded
  (a sentence about "your details" and "this recipient" when it recorded none); every other gate
  rule says "This rule does not apply to this action.", "Asks: this kind of action needs your
  approval first." or "The rules do not recognise this kind of action, so it is held.". A rule's
  result and the sealed constraint reasons and evidence do not change.
- Added (capsule-engine#182): catalog packs `asg/everyday/0.3.9` and `asg/seller/0.1.6`, wording
  only. Rule 3's statement leads with "Your details go only to the merchant filling your order, a
  listing platform, or you", and both packs define a platform once: the marketplace, booking site or mail
  relay between you and the other party. Ids, checks and definitions are unchanged, so no rule decides
  differently; the definition digests move (everyday `acb8a0f7...`, seller `5e112a7f...`). 0.3.8 and
  0.1.5 stay frozen under `tests/fixtures/packs` and `go/packs`.

## 0.2.0 — 2026-10-23

- Added (capsule-engine#162, #235): `capsulectl-engine`, a capsulectl plugin (`cli-plugin/v1`).
  `capsulectl engine check --ruleset FILE` reads one external-check-input/v0 on stdin and prints
  one external-check-result/v0, tier `recomputed`, with a finding per clause of an
  `engine-ruleset/v0` JSON file (`schemas/engine-ruleset-v0.json`). The result's `definition_digest`
  is SHA-256 over the JCS bytes of the ruleset. The same launcher pins as a deal profile's rules
  checker. The first check is `record_type_coverage/1` over the history; an empty declaration is
  `not_applicable`, never a verdict. See docs/WHATS-IN-THIS-REPO.md.
- Changed (#235): `evaluate_record_type_coverage` counts only a string as a kind. An object or a
  list at `kind_field` raised `TypeError`; it is now not a kind.
- Changed (capsule-engine#217, #229): `GuardDecision.verdict` of a decision held only because the
  caps window could not be evaluated (`asked_unevaluated_checks`), with nothing failed and an
  approver for the class, is its outcome, `escalate`, no longer `not_evaluable`. With no
  approver the decision is still refused (`deny`) and its verdict is still `not_evaluable`.
  Outcomes and sealed capsules do not change.
- Changed (capsule-engine#214 hold A, #233, Steven's ruling A): what the caps window
  counts for an accepted act in a live check's history (and, under spend.weekly/3.1.0, in a
  replay), where it can now only rise.
  - An accepted act that seals no `spend_minor` counts at its stated `amount_minor`, never below
    0, under every fold. Under the frozen spend.weekly/3.0.0 and 1.0.0 (everyday 0.3.4 and 0.3.5,
    seller 0.1.0 and 0.1.1) it was left out of the total.
  - Under spend.weekly/3.1.0 (everyday 0.3.6 to 0.3.8) an accepted act counts at the larger of
    its stated `amount_minor` and its `spend_minor`, in a live check's history and in a replay's
    record of an executed act. It counted at the stated amount, so a spend above it was left out.
    A partial charge (spend below the stated amount) still counts at the stated amount.
  - Under 3.0.0 and 1.0.0 an act that seals a `spend_minor` still counts at it.

  Outcome change: such a check can move from allow to escalate (asking for approval), or to deny
  with no approver for the class, and under 3.1.0 a cap that passed on replay can now fail. The
  engine now decides as the Go guard (go/v0.1.3) does. A live history's record of such an act
  carries the stated amount as `stated_amount_minor` (ledger state, never sealed, never read by
  dedupe).

## 0.1.0 — 2026-10-23

- First PyPI release of the package. It installs the `capsule_engine` package and the
  `capsule-engine` command.
