# SPDX-License-Identifier: Apache-2.0
"""Seals one tau2-bench airline simulation's full conversation (assistant/
user message text + flattened tool-call trail, from
``scripts/vendor_tau2_airline_conversations.py``) as a
``conversation_exchange`` capsule -- the same shape
``capsule-emit-mesh``'s ``mesh_record_emitter.py`` uses for its inference
capsules (``capsule_ledger.conversation.build_conversation_exchange_capsule``).

[tau2-engagement-conversation-detail-capsules]: proves ONE capsule shape
spans mesh inference, tau2, and (once wired) the real Alchemy/Amplifier
Security engagements. Model identity is supplied by the caller rather than
guessed from the sim record -- a real engagement transcript's own metadata
(not a filename convention) is where that identity actually lives, and
keeping the capsule-building side agnostic to WHERE model identity came
from is what lets it generalize to Alchemy/Amplifier Security without
change. ``TAU2_MODEL_ID``/``TAU2_PROVIDER`` below are just this module's own
default for the one dataset it vendors
(``vendor_tau2_airline_conversations.py``: ``claude-3-7-sonnet``).

tau2-bench's transcripts never carry a reasoning field or full tool-call
arguments (``vendor_tau2_airline_conversations.py``'s own docstring: tool
calls are flattened to name only) -- ``tau2_sim_to_exchange_messages``
carries over only what the source actually has, which is exactly what
leaves ``reasoning_digest`` absent from the sealed capsule (see
``digest_conversation_exchange``) rather than a fabricated digest over
nothing.
"""
from __future__ import annotations

from typing import Any

from capsule_ledger.conversation import build_conversation_exchange_capsule

from ..guards.signing import Signer

__all__ = ["TAU2_MODEL_ID", "TAU2_PROVIDER", "tau2_sim_to_exchange_messages", "seal_tau2_sim_exchange"]

TAU2_MODEL_ID = "claude-3-7-sonnet-20250219"
TAU2_PROVIDER = "anthropic"


def tau2_sim_to_exchange_messages(sim: dict[str, Any]) -> list[dict[str, Any]]:
    """tau2's vendored per-sim record (``role``, ``content``,
    ``tool_call_names``) -> the generic exchange-message shape
    ``digest_conversation_exchange``/``build_conversation_exchange_capsule``
    read."""
    messages: list[dict[str, Any]] = []
    for m in sim["messages"]:
        message: dict[str, Any] = {"role": m["role"], "content": m.get("content", "")}
        tool_call_names = m.get("tool_call_names")
        if tool_call_names:
            message["tool_calls"] = [{"name": name} for name in tool_call_names]
        messages.append(message)
    return messages


def seal_tau2_sim_exchange(
    sim: dict[str, Any],
    *,
    operator: str,
    developer: str,
    signer: Signer,
    model_id: str = TAU2_MODEL_ID,
    provider: str = TAU2_PROVIDER,
) -> dict:
    """Seal one tau2 simulation's full conversation as a
    ``conversation_exchange`` capsule with the mesh capsule shape, carrying
    the recovered inference metadata (``generation_parameters``, ``usage``,
    and the honest ``served_by: "api"`` marker) the vendor script now vendors
    per sim.

    The metadata is passed through honestly-by-absence: a sim whose vendored
    record lacks ``usage`` or a given generation parameter seals WITHOUT that
    field rather than a fabricated placeholder. tau2 runs against hosted API
    models, so ``served_by`` is ``"api"`` and no local hardware block is
    fabricated (``hardware`` stays genuinely absent)."""
    generation_parameters = sim.get("generation_parameters") or None
    usage = sim.get("usage") or None
    # served_by rides from the vendored record, defaulting to the honest
    # api-served marker -- tau2 has no local GPU/VRAM to attest.
    served_by = sim.get("served_by", "api")
    return build_conversation_exchange_capsule(
        session_id=f"tau2-airline/{sim['sim_id']}",
        exchange_id=sim["sim_id"],
        messages=tau2_sim_to_exchange_messages(sim),
        model_id=model_id,
        provider=provider,
        operator=operator,
        developer=developer,
        signer=signer,
        served_by=served_by,
        generation_parameters=generation_parameters,
        usage=usage,
    )
