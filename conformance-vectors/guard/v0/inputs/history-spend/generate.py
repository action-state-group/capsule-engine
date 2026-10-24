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

``chained-refund``: an accepted money.refund (not stated as money in) whose
capsule chains ``supersedes`` to an accepted 9000 charge that sealed a spend of
9000 in the same history. Nothing is freed: the refund does not state money
in, and a charge never counts below its spend, so the 9000 stays in the total
(which then fails). ``no-spend-negative-transfer``: an accepted act with no
``spend_minor`` and a stated amount of -5000, with the checked record a
money.transfer so that seller 0.1.0 and 0.1.1's caps/1.0.0 (spend.weekly/1.0.0,
no reversal clause) measures it; the act adds nothing to the total.

The refund cases are capsule-engine#234 (ruling A on #214 hold A: a partial
charge counts at its stated amount "until a cancel or refund is on record").
``partial-charge`` is an accepted 9000 charge that sealed a spend of 3000, so
the 7d total is 12299 and fails. A money-in refund whose capsule chains
``supersedes`` to it lowers it by the amount returned, never below the 3000:
``refund-linked-partial`` (2000: 10299, fails), ``refund-linked-full`` (6000:
6299, passes) and ``refund-linked-over`` (9000, floored at the spend: the same
6299); a partial cancel stating ``cancelled_amount_minor`` frees the same
(``cancel-linked-partial``, ``cancel-linked-full``). Each of ``refund-unlinked``, ``refund-wrong-parent`` (cites no charge in
the history), ``refund-wrong-relation``, ``refund-not-money-in`` and
``refund-before-charge`` frees nothing and fails as ``partial-charge`` does;
``refund-of-charge-outside-window`` cites a partial charge sealed before the
7d window, so nothing in the total is lowered.

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


def rebind(entry: dict) -> None:
    """``entry``'s attestation bound again to its edited ``agent_input``."""
    entry["model_attestation"]["compute_attestation"]["agent_input_digest"] = json_digest(entry["agent_input"])


def partial_charge(*, charge_at: str = "2026-10-06T09:00:00Z") -> dict:
    """``BASE`` with an accepted money.purchase of 9000 sealing a spend of
    3000 (a partial charge, which counts at its stated 9000: ruling A on
    #214 hold A), sealed at ``charge_at``. With the 2000 act and the
    1299 check the 7d total is 12299, over the 10000 limit."""
    out = copy.deepcopy(BASE)
    entry = out["history"][0]
    entry["timestamp"] = charge_at
    entry["agent_input"]["x-deal-v0"]["at"] = charge_at
    entry["agent_input"]["body"]["amount_minor"] = 9000
    entry["agent_input"]["body"]["spend_minor"] = 3000
    rebind(entry)
    return out


def refund_of(out: dict, parent: str | None, returned: int, *, relation: str = "supersedes", direction: str = "in",
              at: str = "2026-10-06T12:00:00Z", name: str = "refund", cancel: bool = False) -> dict:
    """``out`` with an accepted money.refund added to its history: it
    returns ``returned`` (the money moved ``direction``) and its capsule
    chains ``relation`` to the capsule ``parent`` (no chain when ``None``)."""
    entry = copy.deepcopy(out["history"][0])
    entry["capsule_id"] = ("a" if name == "refund" else "b") * 64
    entry["action_id"] = f"deal-0000000000000009/{name}"
    entry["timestamp"] = at
    entry.pop("chain", None)
    if parent is not None:
        entry["chain"] = {"parent_capsule_id": parent, "relation": relation}
    body = entry["agent_input"]["body"]
    body.update(action_class="money.refund", amount_minor=returned, spend_minor=0, direction=direction)
    if cancel:
        # A partial cancel states what it returns as cancelled_amount_minor, and no amount_minor.
        body.update(action_class="booking.cancel", cancelled_amount_minor=returned)
        del body["amount_minor"]
    entry["agent_input"]["x-deal-v0"]["at"] = at
    rebind(entry)
    out["history"].insert(0, entry)
    return out


def linked_refund(returned: int, *, cancel: bool = False) -> dict:
    """The partial charge, and a refund (a booking.cancel with ``cancel``) of
    ``returned`` chaining to it."""
    out = partial_charge()
    return refund_of(out, out["history"][0]["capsule_id"], returned, cancel=cancel)


def refund_of_old_charge() -> dict:
    """A 7000 charge in the window, and a refund of the partial charge of
    9000 (spend 4000) sealed before the window, returning 5000 and chaining
    to it: the charge is not in the window, so there is nothing to take
    back."""
    out = newest_act(amount_minor=7000, spend_minor=7000)
    old = out["history"][2]
    old["agent_input"]["body"]["amount_minor"], old["agent_input"]["body"]["spend_minor"] = 9000, 4000
    rebind(old)
    return refund_of(out, old["capsule_id"], 5000, at="2026-10-06T12:00:00Z")


CASES = {
    "spend-below-amount": newest_act(amount_minor=9000, spend_minor=3000),
    "spend-above-amount": newest_act(amount_minor=3000, spend_minor=9000),
    "no-spend": newest_act(amount_minor=8000, spend_minor=None),
    "chained-refund": chained_refund(),
    "no-spend-negative-transfer": transfer(newest_act(amount_minor=-5000, spend_minor=None)),
    "partial-charge": partial_charge(),
    "refund-linked-partial": linked_refund(2000),
    "refund-linked-full": linked_refund(6000),
    "refund-linked-over": linked_refund(9000),
    "cancel-linked-partial": linked_refund(2000, cancel=True),
    "cancel-linked-full": linked_refund(6000, cancel=True),
    "refund-unlinked": refund_of(partial_charge(), None, 6000),
    "refund-wrong-parent": refund_of(partial_charge(), "9" * 64, 6000),
    "refund-wrong-relation": refund_of(partial_charge(), partial_charge()["history"][0]["capsule_id"], 6000,
                                       relation="follows"),
    "refund-not-money-in": refund_of(partial_charge(), partial_charge()["history"][0]["capsule_id"], 6000,
                                     direction="out"),
    "refund-before-charge": refund_of(partial_charge(), partial_charge()["history"][0]["capsule_id"], 6000,
                                      at="2026-10-05T12:00:00Z"),
    "refund-of-charge-outside-window": refund_of_old_charge(),
}

for name, envelope in CASES.items():
    (HERE / f"{name}.json").write_text(json.dumps(envelope, indent=1, sort_keys=True) + "\n")
