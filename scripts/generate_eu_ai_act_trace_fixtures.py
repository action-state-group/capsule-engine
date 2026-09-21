#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Regenerate the TRACE v0.2 fixtures under
``capsule_engine/packs/catalog/eu-ai-act/fixtures/trace/`` used by
``tests/test_pack_eu_ai_act_trace.py``. Not run by the test suite itself --
the fixtures it produces are committed, static files, so a reviewer
recomputing the attainment report reads the same bytes every time rather
than re-signing a new record per run.

Requires the ``trace`` extra (``pip install -e ".[trace]"``). The generated
private signing key is discarded on purpose: only the public JWK a verifier
needs is written to disk (``trusted_issuer.jwk.json``), the same "only the
public half is evidence" posture ``agentrust_trace.sign.verify_record``
itself requires of a caller.
"""
from __future__ import annotations

import json
from pathlib import Path

from agentrust_trace.sign import generate_key, key_to_jwk, sign_record

FIXTURES_DIR = Path(__file__).parent.parent / "capsule_engine" / "packs" / "catalog" / "eu-ai-act" / "fixtures" / "trace"

_SUBJECT = "spiffe://action-state.example/agent/eu-ai-act-demo/prod"
_MEASUREMENT = "sha256:" + "11" * 32
_POLICY_BUNDLE_HASH = "sha256:" + "aa" * 32
_BUILD_DIGEST = "sha256:" + "bb" * 32


def _base_record(*, enforcement_mode: str) -> dict:
    """A TRACE v0.2-conformant record, synthetic throughout -- no real model
    weights, policy bundle, or build artifact stands behind these digests.
    ``appraisal.status: "none"`` / ``policy.enforcement_mode`` set explicitly
    (never defaulted to "advisory"/"affirming") is the same honest-minimum
    convention ``capsule-emit-mesh/tests/test_trace_citation.py``'s own
    ``_record()`` fixture uses: a synthetic record must never claim an
    evaluation that did not happen.
    """
    return {
        "eat_profile": "tag:agentrust-io.com,2026:trace-v0.2",
        "iat": 1758400000,
        "subject": _SUBJECT,
        "model": {
            "provider": "anthropic",
            "model_id": "claude-fable-5",
            "version": "2026-09",
            "aibom_uri": "https://models.example.org/aibom/claude-fable-5",
        },
        "runtime": {"platform": "software-only", "measurement": _MEASUREMENT},
        "policy": {"bundle_hash": _POLICY_BUNDLE_HASH, "enforcement_mode": enforcement_mode},
        "data_class": "internal",
        "build_provenance": {"slsa_level": 0, "digest": _BUILD_DIGEST},
        "appraisal": {"status": "none", "verifier": "https://verifier.example.org/v1"},
        # transparency (SCITT receipt) deliberately omitted on every fixture
        # here -- EU-75's not_present row depends on it being genuinely
        # absent from a verified record, not merely unmapped.
    }


def main() -> None:
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    signing_key = generate_key()
    trusted_jwk = key_to_jwk(signing_key)

    good = sign_record(_base_record(enforcement_mode="enforce"), signing_key)
    oversight_declared = sign_record(_base_record(enforcement_mode="declared"), signing_key)

    # The negative fixture: tamper AFTER signing, same pattern
    # test_trace_citation.py's own tamper test uses -- the record we read is
    # no longer what was signed, so verify_record must reject it outright.
    tampered = {**good, "subject": "spiffe://action-state.example/agent/attacker/prod"}

    (FIXTURES_DIR / "trusted_issuer.jwk.json").write_text(json.dumps(trusted_jwk, indent=2, sort_keys=True) + "\n")
    (FIXTURES_DIR / "good_execution.json").write_text(json.dumps(good, indent=2, sort_keys=True) + "\n")
    (FIXTURES_DIR / "oversight_declared.json").write_text(
        json.dumps(oversight_declared, indent=2, sort_keys=True) + "\n"
    )
    (FIXTURES_DIR / "tampered_execution.json").write_text(json.dumps(tampered, indent=2, sort_keys=True) + "\n")
    print(f"wrote fixtures to {FIXTURES_DIR}")


if __name__ == "__main__":
    main()
