# SPDX-License-Identifier: Apache-2.0
"""Shared foundation for the outcome compiler: closed vocabulary, the
advisory effect model, and the three sealed-capsule builders the setup
verbs need directly (offer/response, refusal, scope census).

``vocabulary.py``/``effect_model.py`` are the pure, dependency-free pieces
``capsule_engine.packs.loader`` needs at pack-load time to validate
``outcomes[]`` entries. ``offer_response.py``/``refusal.py``/
``scope_census.py`` DO touch ``guards.capsule``/``guards.signing`` --
reclassified into this bucket (2026-08-22, SETTLED reclassification) so
``capsule_engine.setup.{observe,propose,confirm}`` can build these three
capsule kinds in-package rather than depending on the separate,
never-donated ``capsule-compiler`` repo for them. The dual compiler proper
-- ``compile_declaration``, the sealed compilation record C, judge/confirm
-- stays in ``capsule-compiler``, which imports all five modules here as
``capsule_engine.compiler.*``.
"""
from __future__ import annotations
