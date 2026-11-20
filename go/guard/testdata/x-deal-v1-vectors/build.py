#!/usr/bin/env python3
"""Build the x-deal-v1 guard vectors (capsule-engine#301).

Deterministic: python3 build.py <this dir> rewrites <this dir>/inputs/ byte for byte.
Every envelope is ../g3-executed-acts-vectors/templates/check.json with a history built
from its pay.json act, so the same purchase is sealed as x-deal-v0 (the control) and as
x-deal-v1, and the two must decide alike. The v1 records follow evidencebook v0.18.1
dealprofile/x-deal-v1 (PROFILE.md sections 2, 4, 5 and 7): a settle's leg travels as the
Capsule's top-level settlement member, its body states no amount or class.
"""
import copy, hashlib, json, os, shutil, sys

D = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.abspath(__file__))
T = os.path.join(D, "..", "g3-executed-acts-vectors", "templates")


def jcs(v):
    return json.dumps(v, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def load(name):
    with open(os.path.join(T, name)) as f:
        return json.load(f)


def seal(cap):
    cap.pop("signature", None)
    cap["model_attestation"] = {"compute_attestation": {"agent_input_digest": hashlib.sha256(jcs(cap["agent_input"])).hexdigest()}}


def h(name):
    return hashlib.sha256(name.encode()).hexdigest()


def ref(name):
    return {"type": "deal-record", "digest_alg": "SHA-256", "digest": h(name)}


DEAL = "deal-6a1f0c93b27d4e58"
AT = "2026-10-09T00:31:26Z"


def v1_block(record_type, seq=None, **extra):
    block = {"profile": "x-deal-v1", "canonicalization": "jcs", "deal_id": DEAL, "record_type": record_type, "at": AT}
    if seq is not None:
        block.update({"seq": seq, "prev": ref("prev%d" % seq), "agreement_ref": ref("agreement"), "seen": h("seen")})
    block.update(extra)
    return block


def v0_pay(n):
    cap = load("pay.json")
    cap["capsule_id"] = h("pay-%s" % n)
    cap["timestamp"] = AT
    cap["agent_input"]["x-deal-v0"]["deal_id"] = DEAL
    cap["agent_input"]["x-deal-v0"]["at"] = AT
    cap["action_id"] = DEAL + "/6"
    seal(cap)
    return cap


def v1_settle(n, leg="payer", settlement=True, reverses=None, block=None, value="2000"):
    cap = load("pay.json")
    cap["capsule_id"] = h("settle-%s" % n)
    cap["timestamp"] = AT
    cap["action_id"] = DEAL + "/2"
    body = {"leg": leg, "statements": [{"row": "paid_as_agreed", "value": "met"}]}
    if leg == "payer":
        body["check"] = ref("check")
    if reverses:
        body["reverses"] = ref(reverses)
    cap["agent_input"] = {"x-deal-v1": block or v1_block("settle", 2), "body": body}
    cap.pop("counterparty_profile", None)
    if settlement:
        cap["settlement"] = {"version": "0", "leg": leg, "sealer_role": leg,
                             "amount": {"value": value, "assetCode": "USD", "assetScale": 2}}
    seal(cap)
    return cap


def with_block(cap, mutate):
    cap = copy.deepcopy(cap)
    mutate(cap["agent_input"])
    seal(cap)
    return cap


def envelope(history, check=None):
    env = load("check.json")
    if check is not None:
        env["record"] = check
    env["history"] = history
    return env


def v1_check():
    check = load("check.json")["record"]
    ai = check["agent_input"]
    old = ai.pop("x-deal-v0")
    block = v1_block("check")
    block["counterparty"] = old["counterparty"]
    ai["x-deal-v1"] = block
    ai["body"].update({"payment_ref": "pay-ref-7731", "nonce": h("nonce")})
    seal(check)
    return check


def two_blocks(ai):
    ai["x-deal-v0"] = {"profile": "x-deal-v0", "deal_id": DEAL, "record_type": "action"}


def unknown_version(ai):
    ai["x-deal-v2"] = ai.pop("x-deal-v1")
    ai["x-deal-v2"]["profile"] = "x-deal-v2"


def extra_block(ai):
    ai["x-deal-v2"] = {"profile": "x-deal-v2"}


def named_other(ai):
    ai["x-deal-v1"]["profile"] = "x-deal-v0"


base = v1_settle("base")
cases = {
    "v0-pay-history": envelope([v0_pay("a")]),
    "v1-pay-history": envelope([base]),
    "v1-payee-leg-in": envelope([v1_settle("payee", leg="payee")]),
    "v1-refund-received": envelope([v1_settle("refund", leg="payee", reverses="payment")]),
    "v1-perform-history": envelope([with_block(base, lambda ai: ai["x-deal-v1"].update(record_type="perform"))]),
    "v1-check-record": envelope([v0_pay("a")], check=v1_check()),
    "v1-check-in-history": envelope([v1_check()]),
    "v1-pay-over-safe": envelope([v1_settle("over", value="9007199254740992")]),
    "v1-check-two-blocks": envelope([v0_pay("a")], check=with_block(v1_check(), two_blocks)),
    "v1-check-extra-block": envelope([v0_pay("a")], check=with_block(v1_check(), extra_block)),
    "v1-check-unknown-version": envelope([v0_pay("a")], check=with_block(v1_check(), unknown_version)),
    "v1-check-block-names-other": envelope([v0_pay("a")], check=with_block(v1_check(), named_other)),
    "v1-pay-no-settlement": envelope([v1_settle("nosettle", settlement=False)]),
    "v1-pay-two-blocks": envelope([with_block(base, two_blocks)]),
    "v1-pay-unknown-version": envelope([with_block(base, unknown_version)]),
    "v1-pay-block-names-other": envelope([with_block(base, named_other)]),
}
out = os.path.join(D, "inputs")
shutil.rmtree(out, ignore_errors=True)
os.makedirs(out)
for name, env in cases.items():
    with open(os.path.join(out, name + ".input.json"), "wb") as f:
        f.write(jcs(env) + b"\n")
