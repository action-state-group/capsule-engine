# real-rc13-lockstep

One `external-check-input/v0` envelope a real capsulectl **v0.1.0-rc13** (7eb05ac1, release asset hash
reproduced) sent its rules checker in the v0.0.10 acceptance run
(scenario R13, `acc-lockstep-rc13-envelope.json`, copied unchanged). Synthetic deal
data (`demo`, "Example Shop"); the history carries fingerprints only.

The check is a pay of 1299 sealed at action taxonomy **5** (the engine's is 6), with no party_role.
Re-sealed at taxonomy 6 it is a plain **allow**: only the taxonomy holds it.

- v0.0.10 RC (f50650d) emitted `not_evaluable` (22 class-keyed rules "the record's taxonomy version is
  not the engine's action taxonomy"), and capsulectl rc13 showed "Your rules were not fully checked: …
  [Hold] [Pay anyway]", so the act could proceed on one tap.
- The product ruling that a record's taxonomy gates it and the engine (capsule-engine 6d456fc) refuse
  such a would-allow (sealed reject). From v0.0.10 the plugin emits **deny**, each held rule saying
  "this check was recorded by an older capsulectl; update capsulectl to check it", and capsulectl shows
  "Not allowed by your rules: …" with no way to go ahead.

Held by `internal/plugin/old_taxonomy_refused_test.go`.
