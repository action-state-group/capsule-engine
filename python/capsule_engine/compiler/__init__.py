# SPDX-License-Identifier: Apache-2.0
"""Shared foundation for the outcome compiler: closed vocabulary and the
advisory effect model.

This subpackage holds ONLY the pure, dependency-free pieces of the outcome
compiler that ``capsule_engine.packs.loader`` needs at pack-load time to
validate ``outcomes[]`` entries. Neither module here touches
``guards.capsule``/``guards.signing`` -- the sealed-capsule builders
(compilation record, offer/response, refusal, scope census) stay in the
separate ``capsule-compiler`` repo, which imports these two modules as
``capsule_engine.compiler.vocabulary`` / ``capsule_engine.compiler.effect_model``.
"""
from __future__ import annotations
