# Task-authority golden vector

One real sealed task-authority record and the reference an action carries
to it, both exported from the producer (capsulectl, fixed test clock) and
copied here byte-for-byte:

- `task-authority-record.json`: the whole sealed `task-authority/v0` record,
  stored as its RFC 8785 (JCS) bytes with no trailing newline.
- `task_authority_ref.json`: the typed reference to it,
  `{"type", "digest_alg", "digest"}`.

`tests/test_scalar_input_checks.py` checks that the engine's digest of the
record equals the reference, that the plan at `body` decides containment,
and that the plan alone is a reference mismatch. Never edit these files by
hand: a producer encoding change replaces both.
