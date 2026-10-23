# Go vectors: capsule-cli #213 (offer, commit, sign and cancel acts seal their disposition and counterparty)

Every file here except this README is byte for byte what was staged as capsule-cli #213's
vectors, and `SHA256SUMS` is that file unchanged.

## Provenance

- Producer: capsulectl at capsule-cli commit `c7ff0880a353159a8c022de03a116452f1bd5e61`
  (`v0.1.0-rc15-10-gc7ff088`), the name every bundle and check input seals.
- Merged as capsule-cli `c3d464f7ac7cda26765d4ed8b5ebe719929b5bf1` (#213). From `c7ff0880` to `c3d464f7`, the
  only non-test, non-Markdown change is a comment in `internal/cli/deal.go`, so the bytes `c3d464f7` seals differ
  only in the producer name and in keys, ids and timestamps. No capsule-cli release contains it yet (rc16 is not cut).
- Decided by capsule-engine `fd43d43` (the seller pack `asg/seller/0.1.3`, the everyday pack `asg/everyday/0.3.6`):
  `live-history/expected_live.without-deal-claims-step.json` and `history-act-counterparty/expected_live.json`.
- `SHA256SUMS` sha256: `3eb6be91edd09e1e759294b04c65c135334bd1404f2c7b9931e3086809fbcd9d`. It lists 22 staged
  files and one more line, `./SHA256SUMS.tmp` (the empty file's sha256), the staging script's own scratch file,
  which was not staged.

## What the tests here hold

- `TestSealDisposition213VectorsMatchTheirSHA256SUMS`: every staged file matches `SHA256SUMS`.
- `TestSealDisposition213SaleAgreesWithTheEngine`: the five check inputs of the two-thread sale, in the six ways
  the engine decided them (as sealed first), agree with the engine on every rule, single_commitment's result,
  reason and evidence, and the ledger.
- `TestSealDisposition213SaleAsSealed`: as sealed, every history act seals an `accept` disposition; B's commit and
  offer after A's are refused on s11, and A's commit is allowed.
- `TestSealDisposition213BookingAgreesWithTheEngine`: the buyer's two-deal booking agrees with the engine in all
  five ways.

`booked-merchant-then-pay/` holds inputs and a build script only; no test reads them.
