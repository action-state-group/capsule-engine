# SPDX-License-Identifier: Apache-2.0
"""Tests for tau2_conversation_exchange.py -- previously zero test coverage
in either capsule-ledger (where this module used to live) or here. Covers the two
functions the module exports: the sim-to-exchange-message shape conversion,
and the actual capsule seal end to end."""
from __future__ import annotations

from capsule_engine.examples.tau2_conversation_exchange import (
    TAU2_MODEL_ID,
    TAU2_PROVIDER,
    seal_tau2_sim_exchange,
    tau2_sim_to_exchange_messages,
)
from capsule_engine.guards.signing import LocalSigner

_SIM = {
    "sim_id": "test-sim-1",
    "messages": [
        {"role": "user", "content": "I need to cancel my flight."},
        {
            "role": "assistant",
            "content": "Let me look that up.",
            "tool_call_names": ["get_reservation_details"],
        },
    ],
    "generation_parameters": {"temperature": 0.0, "seed": 7},
    "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
}


def test_tau2_sim_to_exchange_messages_carries_role_content_and_tool_calls():
    messages = tau2_sim_to_exchange_messages(_SIM)
    assert messages[0] == {"role": "user", "content": "I need to cancel my flight."}
    assert messages[1]["role"] == "assistant"
    assert messages[1]["tool_calls"] == [{"name": "get_reservation_details"}]


def test_tau2_sim_to_exchange_messages_omits_tool_calls_when_absent():
    sim = {"messages": [{"role": "user", "content": "hi"}]}
    messages = tau2_sim_to_exchange_messages(sim)
    assert "tool_calls" not in messages[0]


def test_seal_tau2_sim_exchange_produces_a_verifiable_capsule():
    signer = LocalSigner(key_id="test-key", secret=b"test-secret")
    capsule = seal_tau2_sim_exchange(_SIM, operator="test-op", developer="test-dev@v1", signer=signer)
    assert capsule["capsule_id"]
    assert capsule["asg_payload"]["event"] == "conversation_exchange"
    assert capsule["asg_payload"]["detail"]["session_id"] == "tau2-airline/test-sim-1"
    attestation = capsule["model_attestation"]
    assert attestation["model_id"] == TAU2_MODEL_ID
    assert attestation["provider"] == TAU2_PROVIDER
    compute = attestation["compute_attestation"]
    assert compute["served_by"] == "api"
    assert compute["usage"] == _SIM["usage"]
    assert compute["generation_parameters"]["seed"] == _SIM["generation_parameters"]["seed"]


def test_seal_tau2_sim_exchange_never_fabricates_absent_metadata():
    signer = LocalSigner(key_id="test-key", secret=b"test-secret")
    sim = {"sim_id": "no-metadata-sim", "messages": [{"role": "user", "content": "hi"}]}
    capsule = seal_tau2_sim_exchange(sim, operator="test-op", developer="test-dev@v1", signer=signer)
    compute = capsule["model_attestation"]["compute_attestation"]
    assert "usage" not in compute
    assert "generation_parameters" not in compute
