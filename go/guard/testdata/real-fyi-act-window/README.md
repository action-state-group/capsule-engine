# real-fyi-act-window

Three `external-check-input/v0` envelopes capsulectl v0.1.0-rc14 (49651e2f, the release binary rebuilt
byte for byte) sent its rules checker in the fyi buyer-acts probe run (2026-10-09), copied unchanged
(`acc-<line>.json`). Synthetic deal data; the history carries fingerprints only.

| line | history | check | v0.0.9 r05#7d | from v0.0.10 (G3 ruling) |
|---|---|---|---|---|
| l4-commit-x3 | 3 x booking commit 2000 (fyi), each in its own deal | pay 2000 | not_evaluable | pass, 8000 / 10000 |
| l4-commit-x5 | 5 x booking commit 2000 (fyi) | pay 2000 | not_evaluable | **fail**, 12000 / 10000 |
| l1-commit-default | 2 paid purchases (2000, 3000) + booking commit 2000 (fyi) | the same commit 2000 | not_evaluable | pass, 9000 / 10000 |

The fyi commits are counted at their sealed `spend_minor`, as the pay controls (l4-pay-x3: 8000,
l4-pay-x5: 12000) are counted at their accepted amounts. Held by `internal/plugin/g3_vectors_test.go`.
