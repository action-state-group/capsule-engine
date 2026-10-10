# SPDX-License-Identifier: Apache-2.0
"""A documented fixture step: give a check input the ``deal_claims`` capsulectl
will pass beside the checked record (AMENDMENT 9).

capsule-cli 65f54e528a78, which wrote this fixture, does not pass them yet.
Each entry is built from the thread's own bundle exactly as AMENDMENT 9 states
it, from the sealed claim record's own values: ``capsule_id``,
``record_digest`` (SHA-256 of the claim record's JCS bytes), ``class``,
``source_kind``, ``at`` (the record's sealed ``at``) and ``text_commitment``.
It covers the checked record's own deal's claims with ``source_kind``
``agent`` and a ``class``, sealed before the checked step, in seal order, and
is left out when there are none.

The step refuses an input that already carries ``deal_claims``: once
capsulectl passes them, rebuild the fixture and delete this step.
"""
from __future__ import annotations

import copy

from agent_action_capsule import json_digest

__all__ = ["with_deal_claims"]

MEMBER = "deal_claims"


def with_deal_claims(envelope: dict, bundle: dict) -> dict:
    """``envelope`` with ``record.deal_claims`` built from ``bundle``, the
    checked thread's own copy."""
    record = envelope["record"]
    if MEMBER in record:
        raise SystemExit(f"the check input already carries record.{MEMBER}: capsulectl now passes it, so rebuild "
                         "the fixture and delete tests/fixtures/live-history/add_deal_claims.py")
    deal = record["agent_input"]["chain_id"]
    claims = []
    for sealed in bundle["records"]:
        if sealed["capsule_id"] == record["capsule_id"]:
            break
        shown = (bundle["disclosures"].get(sealed["capsule_id"]) or {}).get("agent_input") or {}
        block, body = shown.get("x-deal-v0") or {}, shown.get("body") or {}
        if (block.get("record_type") == "claim" and block.get("deal_id") == deal
                and body.get("source_kind") == "agent" and body.get("class")):
            claims.append({"capsule_id": sealed["capsule_id"], "record_digest": json_digest(shown),
                           "class": body["class"], "source_kind": "agent", "at": block["at"],
                           "text_commitment": body["text_commitment"]})
    else:
        raise SystemExit(f"the checked record {record['capsule_id']} is not in the thread's bundle")
    given = copy.deepcopy(envelope)
    if claims:
        given["record"][MEMBER] = claims
    return given
