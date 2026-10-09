# Notes for contributors and coding agents

## Guard decisions with local-only fields stay on this machine

Some fields are sealed into a guard decision's `asg_payload` only so that later checks on the same
machine can match against it. They are listed once, in `LOCAL_ONLY_PAYLOAD_FIELDS`
(`capsule_engine/guards/capsule.py`): the deal an act was checked in, the item a sale is about,
what a money-in record returns and reverses, and the digest of a caller's equivalence key (the raw
key is never sealed; an order or invoice number is guessable from its digest). Those values can
describe deals with other counterparties, so a decision capsule carrying any of them never enters an artifact made for
another party. The fields cannot be stripped from a sealed capsule, because `capsule_id` covers
them. So every export path refuses such records, naming the fields and the record count and never
a value. Today those paths are `capsule bundle` (the bundle file, its verify link and its offline
viewer) and `capsule guard dry-run --share`. `tests/test_local_only_payload_fields.py` enforces this.

One value is local-only by its prefix rather than its field: a `target` starting with
`LOCAL_ONLY_TARGET_PREFIX` (`payee-fp:hmac-sha256-profile-key:`, beside the field list). That is a
payee keyed per profile, one value for one merchant across all of a profile's deals, so it links
them. The same refusal names `target` for it; a per-deal target is not refused.
`tests/test_payee_fp_profile.py` enforces this.

Adding a field to `LOCAL_ONLY_PAYLOAD_FIELDS`, sealing a new locally matched field on a decision,
or adding a path that exports decision capsules needs the share boundary reviewed. A new export
path calls `local_only_refusal` and gets a test in that file.
