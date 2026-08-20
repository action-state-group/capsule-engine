# SPDX-License-Identifier: Apache-2.0
"""``capsule setup`` (design §3, build plan Phase 3): the onboarding
journey on top of the outcome compiler (``capsule_ledger.compiler``, Phase
2). Five verbs, ``init``/``observe``/``propose``/``confirm``/``enforce``,
attacking the two hard parts of onboarding -- standing up an instance is
mechanizable (``init``); working out what to record and what the outcomes
are is where every integration dies, and that is what ``observe`` ->
``propose`` -> ``confirm`` -> ``enforce`` attacks.

Submodules, in the order data flows through them:

- ``candidates`` -- the templates ``propose`` grades (design §2.1's D-shape).
- ``declarations`` -- the on-disk store keyed by outcome_id, the thing that
  makes re-derivability checkable across CLI invocations.
- ``init`` -- instance bring-up.
- ``adapters`` / ``observe`` -- the emit-layer recorder and its four
  onboarding-path adapters.
- ``propose`` -- coverage grading + the drift check.
- ``compile_bridge`` -- turns a stored candidate into a real
  ``compiler.compile.CompiledDeclaration``.
- ``confirm`` -- T1/T2/T4, the human touchpoints.
- ``enforce`` -- T3 promotion, shadow-first, plus the reproduction path
  ``capsule verify --refusal`` uses.
- ``scan`` -- shared ledger-read helpers ``propose``/``enforce`` both use.
"""
from __future__ import annotations
