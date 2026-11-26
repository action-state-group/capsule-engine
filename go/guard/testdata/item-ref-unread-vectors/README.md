Inputs for capsule-engine #328: a history whose scope declares it complete,
but that Python's `live_history.py` `history_ledger` reads as incomplete
(an act's `item_ref` not 64 lowercase hex, or a history that is absent or
null), so caps reports the window as not evaluable. Each is derived from
`../emit/pay-history-other-merchant.json` (or, for
`item-ref-null-per-action-over`, `../emit/pay-authorised-3000.json`, whose
amount is over the per-action limit, which still fails first) by the one
edit its name gives. `item-ref-readable` and `history-empty` are the
controls that stay complete. The `guard check` command refuses most of these
by schema; the Go library (`DecodeCheckInput`) does not run the schema.
Seeded into `conformance-vectors/guard/v0/item-ref-unread-vectors.json` by
`python scripts/conformance_vectors.py --seed go/guard/testdata/item-ref-unread-vectors`.
