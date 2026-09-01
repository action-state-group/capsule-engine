# SPDX-License-Identifier: Apache-2.0
"""``registry.conventions``: the action_class convention-label lookup
(design principle item 2) -- the local, vendored stand-in for the
not-yet-existing ``capsule-registry`` "Action-type conventions" table.

Moved here from capsule-ledger during the [ldg-ledger-scope-re-extraction]
RESIDUALS pass (§3.1(a)): this repo carries the full vendor copy
(``describe_field_value``, ``conventions_digest``, the CPB provisional
tables); capsule-ledger keeps only its own minimal, self-contained
``describe_action_class`` shim with its own tiny embedded label data --
see capsule-ledger's own (much smaller) ``test_registry_conventions.py``
for that shim's coverage.
"""
from __future__ import annotations

from capsule_engine.registry import (
    conventions_digest,
    describe_action_class,
    describe_field_value,
)


def test_no_action_class_is_a_distinct_state_from_unregistered():
    convention = describe_action_class(None)
    assert convention.action_class is None
    assert convention.registered is False
    assert "no action class" in convention.label


def test_registered_action_class_resolves_a_real_label():
    convention = describe_action_class("money.transfer")
    assert convention.registered is True
    assert convention.label == "Money transfer"
    assert convention.description


def test_unregistered_action_class_renders_as_is_never_an_error():
    convention = describe_action_class("hold.reserve")
    assert convention.registered is False
    assert convention.action_class == "hold.reserve"
    assert convention.label == "hold.reserve"  # renders as-is, not hidden


def test_conventions_digest_is_a_stable_real_digest():
    d1 = conventions_digest()
    d2 = conventions_digest()
    assert d1 == d2
    assert len(d1) == 64
    int(d1, 16)  # hex


# ---- Provisional field-value conventions (vendored CPB payload class) -------
def test_mesh_effect_type_resolves_known_provisional():
    fc = describe_field_value("effect.type", "inference_completion")
    assert fc.registered is True
    assert fc.status == "provisional"
    assert fc.payload_class == "mesh-inference-exchange"
    assert fc.label == "Inference completion"


def test_mesh_effect_attestation_resolves_known_provisional():
    fc = describe_field_value("effect_attestation", "host_served_observed")
    assert fc.registered is True and fc.status == "provisional"
    assert fc.payload_class == "mesh-inference-exchange"


def test_mesh_chain_relation_follows_resolves_known_provisional():
    fc = describe_field_value("chain.relation", "follows")
    assert fc.registered is True and fc.status == "provisional"
    assert fc.payload_class == "mesh-inference-exchange"


def test_unregistered_field_value_renders_as_is_never_an_error():
    fc = describe_field_value("effect.type", "teleport_completion")
    assert fc.registered is False
    assert fc.status is None
    assert fc.label == "teleport_completion"  # renders as-is


def test_absent_field_value_is_a_distinct_state():
    fc = describe_field_value("effect.type", None)
    assert fc.registered is False
    assert "no value" in fc.label
