# SPDX-License-Identifier: Apache-2.0
"""Writes the history-spend inputs: check inputs whose history holds an
accepted money-out act whose sealed spend (``spend_minor``) is not its stated
amount (``amount_minor``), or that seals no spend at all, under the capped
class money.purchase (capsule-engine#214, hold A).

What the caps window sums for such an act depends on the fold:

* under a fold that counts executed acts (spend.weekly/3.1.0, everyday
  0.3.8) the engine's record of the executed act counts an accepted act at
  its ``amount_minor`` (report/replay.py ``_executed_spend``), and the
  history's own copy of it is left out;
* under a fold that counts accepted acts only (spend.weekly/3.0.0, the frozen
  everyday 0.3.4 and 0.3.5) the window reads the history's copy, whose amount
  is the spend the deal bridge takes (``action_for_history_entry``), and none
  when the act seals no ``spend_minor``.

The amounts are picked so that the window limit (10000) decides which one
was summed: ``spend-below-amount`` fails on its amount and passes on its
spend, ``spend-above-amount`` the other way, and ``no-spend`` fails when the
act is counted at its amount and passes when it is left out.

``chained-refund``: an accepted money.refund whose capsule chains
``supersedes`` to an accepted 9000 charge in the same history. The engine's
records of a history act carry no chain block, so the refund cites no charge:
it is unlinked and takes nothing back, and the 9000 stays in the total
(which then fails). ``no-spend-negative-transfer``: an accepted act with no
``spend_minor`` and a stated amount of -5000, with the checked record a
money.transfer so that seller 0.1.0 and 0.1.1's caps/1.0.0 (spend.weekly/1.0.0,
no reversal clause) measures it; the act adds nothing to the total.

Each is ``go/guard/testdata/emit/pay-history-under.json`` (accepted acts on
10-06, 10-03 and, outside the 7d window, 09-20; a 1299 purchase checked on
10-07) with its newest act's body edited and bound again by its digest in the
attestation. Run from the repository root:

    python conformance-vectors/guard/v0/inputs/history-spend/generate.py
"""
import copy
import json
from pathlib import Path

from agent_action_capsule import json_digest

ROOT = Path(__file__).resolve().parents[5]
HERE = Path(__file__).resolve().parent
BASE = json.loads((ROOT / "go" / "guard" / "testdata" / "emit" / "pay-history-under.json").read_text())


def newest_act(**body) -> dict:
    """``BASE`` with its newest act's body given ``body`` (a ``None``
    removes the member)."""
    out = copy.deepcopy(BASE)
    entry = out["history"][0]
    shown = entry["agent_input"]["body"]
    for name, value in body.items():
        if value is None:
            shown.pop(name, None)
        else:
            shown[name] = value
    entry["model_attestation"]["compute_attestation"]["agent_input_digest"] = json_digest(entry["agent_input"])
    return out


def chained_refund() -> dict:
    """The second act a 9000 charge, and the newest an accepted
    money.refund (amount 8000, spend 5000) whose capsule chains
    ``supersedes`` to it."""
    out = newest_act(action_class="money.refund", amount_minor=8000, spend_minor=5000)
    charge = out["history"][1]
    charge["agent_input"]["body"]["amount_minor"] = charge["agent_input"]["body"]["spend_minor"] = 9000
    charge["model_attestation"]["compute_attestation"]["agent_input_digest"] = json_digest(charge["agent_input"])
    out["history"][0]["chain"] = {"parent_capsule_id": charge["capsule_id"], "relation": "supersedes"}
    # The charge listed first: a reader that takes the history in the order
    # given, as the Go guard's fold does, meets the charge before the refund.
    out["history"][0], out["history"][1] = out["history"][1], out["history"][0]
    return out


def transfer(out: dict) -> dict:
    """``out`` with the checked record classed money.transfer, the one class
    seller 0.1.0 and 0.1.1's caps/1.0.0 limits."""
    out["record"]["agent_input"]["body"]["action_class"] = "money.transfer"
    out["record"]["model_attestation"]["compute_attestation"]["agent_input_digest"] = json_digest(
        out["record"]["agent_input"])
    return out


CASES = {
    "spend-below-amount": newest_act(amount_minor=9000, spend_minor=3000),
    "spend-above-amount": newest_act(amount_minor=3000, spend_minor=9000),
    "no-spend": newest_act(amount_minor=8000, spend_minor=None),
    "chained-refund": chained_refund(),
    "no-spend-negative-transfer": transfer(newest_act(amount_minor=-5000, spend_minor=None)),
}

for name, envelope in CASES.items():
    (HERE / f"{name}.json").write_text(json.dumps(envelope, indent=1, sort_keys=True) + "\n")
