# real-fyi-act

One `external-check-input/v0` envelope capsulectl v0.1.0-rc14 (49651e2f,
the release binary rebuilt byte for byte) sent its pinned rules checker
during a real deal check: line s4 of the fyi buyer-acts probe run
(2026-10-09). Synthetic deal data (`demo: true`, "Example Shop"); the
history carries fingerprints only.

The flow: two paid purchases at the shop, then in a purchase deal
(`inputs/open.json`) a `commit` of 2000 is checked
(`inputs/check-commit.json`), proceeds, and is acted
(`inputs/act-commit.json`); the identical check again is this envelope.

The committed act is sealed `fyi` with no disposition: capsulectl seals a
`decide` Capsule only for an action with a registered AAC effect.type, and
through rc14 that is `pay` alone. It is not an unchecked act (no
`body.unchecked`), so it carries no recorded decision.

Until v0.0.10 that left the 7-day window uncounted (r05#7d `not_evaluable`,
"an earlier act has no recorded decision, so the total cannot count it").
Under the engine-side G3 ruling (2026-10-09) the window counts it at its sealed
`spend_minor`: r05#7d now passes at 9000 (2000 + 3000 paid, the 2000
commit, and this 2000) against 10000. The envelope is unchanged.

The rulings named here (ruling (a), ruling (b), the G3 ruling) are defined in the package doc of
`go/guard` (`action.go`).
