# SPDX-License-Identifier: Apache-2.0
"""The three launch reference checks (dev-persona doc: "policy that runs like CI")."""
from .base import CheckOutcome
from .caps import check_caps
from .dedupe import check_dedupe
from .verify_before_dispatch import check_verify_before_dispatch

__all__ = [
    "CheckOutcome",
    "check_caps",
    "check_dedupe",
    "check_verify_before_dispatch",
]
