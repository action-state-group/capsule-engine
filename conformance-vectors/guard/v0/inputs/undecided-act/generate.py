# SPDX-License-Identifier: Apache-2.0
"""Writes the undecided-act inputs: check inputs whose history holds an act
with no recorded decision, which a caps fold that counts accepted acts only
(spend.weekly/3.0.0, keyed by operator; spend.weekly/1.0.0, keyed by
developer) cannot sum when its spend cannot be read (capsule-engine#214 hold
B; Sherlock2, ops u504). Both engines hold the window then, and only then:
an act outside the window or under another key, a decided act, one naming
two deals, one that moved money in, one taken without a check, a statement,
and one whose spend reads as 0 or more do not hold it. They are decided under
the frozen packs that cite those folds (``"frozen": true`` cases); under a
fold that counts executed acts the engine's own unreadable-spend rule holds
instead.

Each is ``go/guard/testdata/emit/pay-history-under.json`` (accepted acts on
10-06, 10-03 and, outside the 7d window, 09-20; checked on 10-07) with
history acts edited, each edited ``agent_input`` bound again by its digest in
the attestation. The ``transfer-*`` inputs also class the checked record
``money.transfer``, the one class seller 0.1.0 and 0.1.1's caps/1.0.0
limits. Run from the repository root:

    python conformance-vectors/guard/v0/inputs/undecided-act/generate.py
"""
import copy
import json
from pathlib import Path

from agent_action_capsule import json_digest

ROOT = Path(__file__).resolve().parents[5]
HERE = Path(__file__).resolve().parent
BASE = json.loads((ROOT / "go" / "guard" / "testdata" / "emit" / "pay-history-under.json").read_text())
CLAIM = json.loads((ROOT / "go" / "guard" / "testdata" / "statement-window-vectors" / "inputs"
                    / "statement-in-history" / "step-2.input.json").read_text())["history"][0]


def rebound(capsule: dict) -> dict:
    capsule["model_attestation"]["compute_attestation"]["agent_input_digest"] = json_digest(capsule["agent_input"])
    return capsule


def undecided(entry: dict, **body) -> dict:
    """``entry`` with no recorded decision and no ``spend_minor``, its body
    then given ``body`` (a ``None`` removes the member)."""
    entry.pop("disposition", None)
    shown = entry["agent_input"]["body"]
    shown.pop("spend_minor", None)
    for name, value in body.items():
        if value is None:
            shown.pop(name, None)
        else:
            shown[name] = value
    return rebound(entry)


def envelope(*indexes: int, **body) -> dict:
    out = copy.deepcopy(BASE)
    for index in indexes:
        undecided(out["history"][index], **body)
    return out


def transfer(out: dict) -> dict:
    out["record"]["agent_input"]["body"]["action_class"] = "money.transfer"
    rebound(out["record"])
    return out


def edited(edit) -> dict:
    out = copy.deepcopy(BASE)
    out["history"][0] = edit(out["history"][0])
    return out


def other_operator() -> dict:
    out = envelope(0)
    out["history"][0]["operator"] = "another-operator"
    return out


def other_developer() -> dict:
    out = transfer(envelope(0))
    out["history"][0]["developer"] = "another-developer"
    return out


def decided(entry: dict) -> dict:
    undecided(entry)
    entry["disposition"] = {"decision": "reject"}
    return entry


def two_deals(entry: dict) -> dict:
    undecided(entry)
    entry["agent_input"]["chain_id"] = "another-deal"
    return rebound(entry)


def unchecked(entry: dict) -> dict:
    undecided(entry)
    entry["agent_input"]["body"] = {"action_class": "money.purchase", "taxonomy_version": "6",
                                    "unchecked": entry["agent_input"]["body"]}
    return rebound(entry)


def attempted(entry: dict) -> dict:
    undecided(entry)
    entry["agent_input"] = {"type": "action-outcome/v0", "body": {
        "action_class": "money.purchase", "taxonomy_version": "6", "attempted": entry["agent_input"]["body"]}}
    return rebound(entry)


def statement() -> dict:
    out = copy.deepcopy(BASE)
    out["history"].insert(0, {**copy.deepcopy(CLAIM), "timestamp": "2026-10-07T00:00:00Z", "operator": "deal"})
    return out


def short() -> dict:
    out = envelope(0)
    out["history_scope"]["days"] = 5
    return out


def with_taxonomy() -> dict:
    out = envelope(0)
    out["history"][1]["agent_input"]["body"]["taxonomy_version"] = "1"
    rebound(out["history"][1])
    return out


def no_check_time() -> dict:
    out = envelope(0, 2)
    del out["record"]["timestamp"]
    return out


def unbound() -> dict:
    out = envelope(0)
    out["history"][0]["model_attestation"]["compute_attestation"]["agent_input_digest"] = "0" * 64
    return out


CASES = {
    "held": lambda: envelope(0),
    "held-two": lambda: envelope(0, 1),
    "held-spend-negative": lambda: envelope(0, spend_minor=-1),
    "held-spend-a-string": lambda: envelope(0, spend_minor="3000"),
    "held-spend-an-object": lambda: envelope(0, spend_minor={"minor": 3000}),
    "held-spend-true": lambda: envelope(0, spend_minor=True),
    "held-before-taxonomy": with_taxonomy,
    "held-no-check-time": no_check_time,
    "held-unbound": unbound,
    "short-before-undecided": short,
    "outside-the-window": lambda: envelope(2),
    "other-operator": other_operator,
    "spend-0": lambda: envelope(0, spend_minor=0),
    "spend-readable": lambda: envelope(0, spend_minor=3000),
    "money-in": lambda: envelope(0, direction="in"),
    "decided": lambda: edited(decided),
    "names-two-deals": lambda: edited(two_deals),
    "unchecked": lambda: edited(unchecked),
    "outcome-attempted": lambda: edited(attempted),
    "statement": statement,
    "transfer-decided": lambda: transfer(copy.deepcopy(BASE)),
    "transfer-held": lambda: transfer(envelope(0)),
    "transfer-other-developer": other_developer,
}

for name, build in CASES.items():
    (HERE / f"{name}.json").write_text(json.dumps(build(), indent=1, sort_keys=True) + "\n")
