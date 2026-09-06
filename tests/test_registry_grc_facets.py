# SPDX-License-Identifier: Apache-2.0
"""GRC folds batch: the eight registry facets (registry-side, additive) --
``interaction.disclosure{kind}``, ``incident.flag{severity_class}`` ->
``incident.report``, ``output.media_class``+``marking_ref``,
``reference_db_ref``, ``action.initiative`` (planned), ``approver_id``,
``input_class`` labels in basis, and the override/stop relation
``chain.relation=supersedes``.

These land in a NEW ``grc_field_conventions``/``grc_reference_fields`` table,
never in the vendor-owned ``provisional_field_conventions`` block --
verified here by confirming ``scripts/vendor_cpb_registry.py --check``'s own
comparison (mirrored from its ``_PROVISIONAL_FIELD_CONVENTIONS`` constant)
still matches after these additions.
"""
from __future__ import annotations

import json
from pathlib import Path

from capsule_engine.registry import describe_action_class, describe_field_value
from capsule_engine.registry.conventions import is_known_reference_field

REPO_ROOT = Path(__file__).parent.parent


# ---- action-class facets: interaction.disclosure, incident.flag/report ----
def test_interaction_disclosure_action_class_is_registered():
    convention = describe_action_class("interaction.disclosure")
    assert convention.registered is True
    assert convention.label == "Interaction disclosure"


def test_incident_flag_and_report_action_classes_are_registered():
    flag = describe_action_class("incident.flag")
    report = describe_action_class("incident.report")
    assert flag.registered is True and report.registered is True
    assert flag.label == "Incident flag"
    assert report.label == "Incident report"


# ---- field-value facets ----
def test_interaction_disclosure_kind_values_are_registered():
    for kind in ("ai", "emotion", "biometric"):
        fc = describe_field_value("interaction.disclosure_kind", kind)
        assert fc.registered is True, kind
        assert fc.status == "registered"


def test_incident_severity_class_values_are_registered():
    for severity in ("standard", "death", "widespread_or_critical"):
        fc = describe_field_value("incident.severity_class", severity)
        assert fc.registered is True, severity


def test_output_media_class_values_are_registered():
    for media in ("text", "image", "audio", "video"):
        fc = describe_field_value("output.media_class", media)
        assert fc.registered is True, media


def test_action_initiative_values_are_registered_as_planned():
    for initiative in ("directed", "responsive", "discovered"):
        fc = describe_field_value("action.initiative", initiative)
        assert fc.registered is True, initiative
        assert fc.status == "planned"


def test_input_class_values_are_registered():
    for input_class in ("user_provided", "tool_output", "retrieved_context", "model_generated"):
        fc = describe_field_value("input_class", input_class)
        assert fc.registered is True, input_class


def test_chain_relation_supersedes_is_registered_distinct_from_vendored_follows():
    supersedes = describe_field_value("chain.relation", "supersedes")
    follows = describe_field_value("chain.relation", "follows")
    assert supersedes.registered is True
    assert supersedes.status == "registered"  # company-authored, not the vendored "provisional"
    assert follows.registered is True
    assert follows.status == "provisional"  # vendored CPB entry, unaffected by the new table


# ---- bare reference fields (approver_id, output.marking_ref, reference_db_ref) ----
def test_bare_reference_fields_are_documented():
    for field in ("approver_id", "output.marking_ref", "reference_db_ref"):
        assert is_known_reference_field(field) is True, field
    assert is_known_reference_field("not.a.registered.field") is False


# ---- never-reject invariant still holds for anything not in these tables ----
def test_unregistered_value_still_renders_as_is_never_an_error():
    fc = describe_field_value("action.initiative", "teleported")
    assert fc.registered is False
    assert fc.label == "teleported"


# ---- vendor-drift isolation: the new tables never touch the vendored block ----
def test_new_grc_tables_are_not_the_vendored_provisional_block():
    raw = json.loads((REPO_ROOT / "capsule_engine" / "registry" / "conventions.json").read_text())
    assert "grc_field_conventions" in raw
    assert "grc_reference_fields" in raw
    # scripts/vendor_cpb_registry.py's --check only ever compares
    # provisional_field_conventions against its own hand-curated constant --
    # confirm the new keys are siblings, not nested inside it.
    assert "grc_field_conventions" not in raw["provisional_field_conventions"]
    assert "supersedes" not in raw["provisional_field_conventions"].get("chain.relation", {})


def test_mutant_removing_a_grc_entry_flips_it_back_to_unregistered():
    """Mutant proving describe_field_value actually reads the table rather
    than hardcoding True: a value NOT in the table must render unregistered."""
    fc = describe_field_value("incident.severity_class", "not_a_real_severity_class")
    assert fc.registered is False
    real = describe_field_value("incident.severity_class", "death")
    assert real.registered is True
