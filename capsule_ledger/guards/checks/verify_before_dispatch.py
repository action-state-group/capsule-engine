# SPDX-License-Identifier: Apache-2.0
"""verify_before_dispatch check: the cited mandate capsule must exist in the
ledger and independently re-verify.

Catches the "approved, then altered" class (dev-persona doc): a Class 1
``verify()`` recomputes the mandate's ``capsule_id`` from its stored bytes
(``ledger.verify``, via T2's ``LedgerStore``); if the mandate's content
changed after it was cited, that recomputation no longer matches the id on
record and verification fails -- this check never recomputes or trusts a
digest itself, it only reads the verifier's own ``ok``.
"""
from __future__ import annotations

from ...ledger.api import LedgerAPI
from ..action import Action
from ..capsule import ConstraintOutcome
from .base import CheckOutcome

__all__ = ["check_verify_before_dispatch"]


def check_verify_before_dispatch(action: Action, ledger: LedgerAPI) -> CheckOutcome:
    cited = action.cited_mandate_capsule_id
    if cited is None:
        return CheckOutcome(
            constraint=ConstraintOutcome(
                id="verify_before_dispatch",
                result="n/a",
                reason="action cites no mandate capsule",
                check_type="policy",
                method="agent_action_capsule.verify",
            )
        )

    record = ledger.fetch(cited)
    if record is None:
        return CheckOutcome(
            constraint=ConstraintOutcome(
                id="verify_before_dispatch",
                result="fail",
                reason=f"cited mandate {cited[:16]}… not found in ledger",
                evidence={"cited_capsule_id": cited, "reason_kind": "not_found"},
                check_type="policy",
                method="agent_action_capsule.verify",
            )
        )

    result = ledger.verify(record.capsule_id)
    if result is None or not result.ok:
        codes = [f.code for f in result.findings] if result is not None else []
        return CheckOutcome(
            constraint=ConstraintOutcome(
                id="verify_before_dispatch",
                result="fail",
                reason=f"cited mandate {cited[:16]}… failed verification: {codes}",
                evidence={"cited_capsule_id": cited, "finding_codes": codes, "reason_kind": "verification_failed"},
                check_type="policy",
                method="agent_action_capsule.verify",
            )
        )

    return CheckOutcome(
        constraint=ConstraintOutcome(
            id="verify_before_dispatch",
            result="pass",
            reason=f"cited mandate {cited[:16]}… verifies",
            evidence={"cited_capsule_id": cited},
            check_type="policy",
            method="agent_action_capsule.verify",
        ),
        chain_parent=record.capsule_id,
        chain_relation="confirms",
    )
