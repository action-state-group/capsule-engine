# SPDX-License-Identifier: Apache-2.0
"""Named-reason errors for pack definitions (mirrors ``folds/errors.py`` /
``guards/wickets/errors.py`` / ``policy/errors.py``).

A dev's AI coding tool is the primary author of ``pack.yaml`` files (starter-
packs plan: "AI-coded by design"), so every message here follows the same
rule that made those three modules' errors actionable: name the field, say
what was expected, show a correct example inline. A reason code alone is
for tests and tooling; the message is for whoever (human or model) has to
fix the file.
"""
from __future__ import annotations

PACK_NOT_FOUND = "pack_not_found"
MALFORMED_PACK = "malformed_pack"
INVALID_PACK_ID = "invalid_pack_id_namespace"
MISSING_REQUIRED_FIELD = "missing_required_field"
OBLIGATION_CHECK_NOT_DECLARED = "obligation_check_not_declared"
DUPLICATE_OBLIGATION_ID = "duplicate_obligation_id"
INVALID_ACTION_SEMANTIC = "invalid_action_semantic"
UNKNOWN_NORMALIZED_FIELD = "unknown_normalized_field"
UNKNOWN_ACTION_CLASS = "unknown_action_class"
DUPLICATE_ACTION_TYPE = "duplicate_action_type"
INVALID_CONSTRAINT = "invalid_constraint"
DUPLICATE_CONSTRAINT_WICKET_ID = "duplicate_constraint_wicket_id"
FOLD_FILE_NOT_FOUND = "fold_file_not_found"
INVALID_FOLD_REF = "invalid_fold_ref"
INVALID_HOLDS_INTEGRATION = "invalid_holds_integration"
INVALID_FIXTURES = "invalid_fixtures"
FLOAT_IN_PACK_DIGEST = "float_in_pack_digest"
UNSAFE_INTEGER_IN_PACK_DIGEST = "unsafe_integer_in_pack_digest"

# outcomes[] -- the sister table to obligations[] (compiler-and-setup design
# 2026-08-19 §4b; supersedes [ldg-outcome-declaration-schema]).
DUPLICATE_OUTCOME_ID = "duplicate_outcome_id"
INVALID_OUTCOME = "invalid_outcome"
MISSING_EVIDENCE_RULE = "missing_evidence_rule"
INVALID_VERDICT = "invalid_verdict"
MISSING_REFUSAL_REASON = "missing_refusal_reason"
EFFECT_CLAIM_NOT_REFUSED = "effect_claim_not_refused"
UNKNOWN_EFFECT_CLAIM = "unknown_effect_claim"
INVALID_RE_DERIVABILITY_GRADE = "invalid_re_derivability_grade"
INVALID_SCOPE_CENSUS = "invalid_scope_census"

# tier ([ldg-bj-tier-field], backward-judge design §8.2): whether an outcome
# gates a session's job-success (must_have) or is reported without gating
# (informational, the default).
INVALID_TIER = "invalid_tier"

# mode ([ldg-bp-mode-tag], standard-outcome-pack design §3): which of the
# seven ways an outcome is judged (structural/value/judged/fold_rollup/
# fold_counterparty/fold_agent/fold_cohort).
INVALID_MODE = "invalid_mode"

# profiles[] -- relationship-topology profiles over a pack's own outcomes
# ([ldg-bp-topology-profiles], standard-outcome-pack design §6b/§7).
INVALID_PROFILE_ID = "invalid_profile_id"
DUPLICATE_PROFILE_ID = "duplicate_profile_id"
UNKNOWN_PROFILE_ID = "unknown_profile_id"
INVALID_COUNTERPARTY_BINDING = "invalid_counterparty_binding"
DUPLICATE_PROFILE_OVERRIDE_OUTCOME_ID = "duplicate_profile_override_outcome_id"
UNKNOWN_OUTCOME_IN_PROFILE_OVERRIDE = "unknown_outcome_in_profile_override"
TOPOLOGY_INVARIANT_OVERRIDE = "topology_invariant_override"

# measurability / evidence_instrument (pack-harden-tau2-oracle: closes the
# adversarial-review finding that a term's "declared, not measured on this
# corpus" claim was an unchecked, hand-authored lambda -- see
# corpus_verify.py's module docstring for the oracle this data feeds).
INVALID_MEASURABILITY = "invalid_measurability"
MISSING_EVIDENCE_INSTRUMENT = "missing_evidence_instrument"
INVALID_EVIDENCE_INSTRUMENT = "invalid_evidence_instrument"

# Constraint scope declaration + cross-constraint agreement (generalizes the
# capsule-emit PR #54 finding: a lock/cap/aggregate scope mismatch let a
# cross-class race jointly admit what sequential execution would deny --
# see schema.py's module docstring for the full rationale).
MISSING_CONSTRAINT_SCOPE = "missing_constraint_scope"
INVALID_SCOPE_DIMENSION = "invalid_scope_dimension"
SCOPE_MISMATCH = "scope_mismatch"

# Registry-pin verification (pins.py) -- a distinct failure family from
# pack.yaml parsing: these are trust/integrity failures against a pins
# source (a local file today, a live capsule-registry fetch later), not
# malformed-file failures, so they get their own reason codes and their own
# exception type (``RegistryPinError`` below) rather than overloading
# ``PackDefinitionError``.
MALFORMED_PINS_FILE = "malformed_pins_file"
PIN_NOT_FOUND = "pin_not_found"
PIN_DIGEST_MISMATCH = "pin_digest_mismatch"


class PackDefinitionError(ValueError):
    """A pack definition (``pack.yaml`` plus its referenced files) fails to
    parse or validate. Carries a stable reason code, same discipline as
    ``FoldDefinitionError`` / ``WicketDefinitionError`` / ``PolicyManifestError``."""

    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        super().__init__(f"{reason}: {message}")


class RegistryPinError(ValueError):
    """A pack or fold definition fails registry-pin verification: no pin on
    record, or the definition's real digest doesn't match the pinned one.
    Always fail-closed -- see ``pins.py``'s module docstring."""

    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        super().__init__(f"{reason}: {message}")


# corpus_verify.py's oracle -- a distinct failure family from pack.yaml
# parsing (PackDefinitionError): this is a trust/integrity failure found by
# actually scanning a corpus against what the pack declared, not a malformed-
# file failure, so it gets its own reason codes and exception type.
DECLARED_NOT_MEASURED_EVIDENCE_RESOLVED = "declared_not_measured_evidence_resolved"


class CorpusVerificationError(ValueError):
    """A pack's declared ``measurability``/``evidence_instrument`` data does
    not hold against a real corpus -- see ``corpus_verify.py``'s module
    docstring. Always fail-closed, same discipline as ``RegistryPinError``."""

    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        super().__init__(f"{reason}: {message}")
