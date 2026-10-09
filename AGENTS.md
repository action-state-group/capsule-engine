# Notes for contributors and coding agents

## Guard decisions with local-only fields stay on this machine

Some fields are sealed into a guard decision's `asg_payload` only so that later checks on the same
machine can match against it. They are listed once, in `LOCAL_ONLY_PAYLOAD_FIELDS`
(`capsule_engine/guards/capsule.py`): the deal an act was checked in, the item a sale is about, and
what a money-in record returns and reverses. Those values can describe deals with other
counterparties, so a decision capsule carrying any of them never enters an artifact made for
another party. The fields cannot be stripped from a sealed capsule, because `capsule_id` covers
them. So every export path refuses such records, naming the fields and the record count and never
a value. Today those paths are `capsule bundle` (the bundle file, its verify link and its offline
viewer) and `capsule guard dry-run --share`. `tests/test_local_only_payload_fields.py` enforces this.

Adding a field to `LOCAL_ONLY_PAYLOAD_FIELDS`, sealing a new locally matched field on a decision,
or adding a path that exports decision capsules needs the share boundary reviewed. A new export
path calls `local_only_refusal` and gets a test in that file.
