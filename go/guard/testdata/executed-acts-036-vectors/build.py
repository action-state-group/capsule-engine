#!/usr/bin/env python3
"""Build the executed-acts 0.3.6 vector envelopes from corpus.json.

Deterministic: python3 build.py <this dir> rewrites <this dir>/inputs/ byte for byte.
It is ../g3-executed-acts-vectors/build.py over the same templates (read from there), with two
more edits a sequence may name: an act's "disposition" (the decision its capsule seals, or null
for none; the disposition is outside agent_input, so no digest moves), and the sequence's
"check_body" (members of the checked record's body replaced, its agent_input_digest recomputed).
same_action_id_as / same_capsule_id_as reuse another act's action_id / capsule_id, as in G3.
"""
import copy, datetime, hashlib, json, os, shutil, sys

D = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.abspath(__file__))
T = os.path.join(D, "..", "g3-executed-acts-vectors", "templates")


def jcs(v):
    return json.dumps(v, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def load(path):
    with open(path) as f:
        return json.load(f)


def seal(cap):
    cap.pop("signature", None)
    cap["model_attestation"] = {"compute_attestation": {"agent_input_digest": hashlib.sha256(jcs(cap["agent_input"])).hexdigest()}}


corpus = load(os.path.join(D, "corpus.json"))
template_check = load(os.path.join(T, "check.json"))
at = datetime.datetime.strptime(template_check["record"]["timestamp"], "%Y-%m-%dT%H:%M:%SZ")


def ident(seq, act_id):
    return hashlib.sha256(f"executed-acts-036/{seq}/{act_id}".encode()).hexdigest()


def build(seq, act, check, by_id):
    cap = copy.deepcopy(load(os.path.join(T, act["template"] + ".json")))
    ai = cap["agent_input"]
    body = ai["body"]["unchecked"] if act["template"] == "unchecked" else ai["body"]
    for k, v in act.get("body", {}).items():
        if v is None:
            body.pop(k, None)
        else:
            body[k] = v
    cid_of = act.get("same_capsule_id_as", act["id"])
    aid_of = act.get("same_action_id_as", act["id"])
    deal = "deal-e36" + ident(seq, aid_of)[:12]
    seq_no = cap["action_id"].rsplit("/", 1)[1]
    ts = (at - datetime.timedelta(hours=act["hours_ago"])).strftime("%Y-%m-%dT%H:%M:%SZ")
    cap["capsule_id"] = ident(seq, cid_of)
    cap["action_id"] = by_id[aid_of]["action_id"] if aid_of in by_id else f"{deal}/{seq_no}"
    cap["operator"] = check["record"]["operator"]
    cap["timestamp"] = ts
    if "x-deal-v0" in ai:
        ai["x-deal-v0"]["deal_id"] = deal
        ai["x-deal-v0"]["at"] = ts
    else:
        ai["chain_id"] = deal
        ai["at"] = ts
    if "disposition" in act:
        if act["disposition"] is None:
            cap.pop("disposition", None)
        else:
            cap["disposition"]["decision"] = act["disposition"]
    seal(cap)
    return cap


out = os.path.join(D, "inputs")
shutil.rmtree(out, ignore_errors=True)
for s in corpus["sequences"]:
    os.makedirs(os.path.join(out, s["name"]))
    check = copy.deepcopy(template_check)
    if s.get("check_body"):
        check["record"]["agent_input"]["body"].update(s["check_body"])
        seal(check["record"])
    by_id, built = {}, []
    for a in s["acts"]:
        built.append(build(s["name"], a, check, by_id))
        by_id.setdefault(a["id"], built[-1])
    for k in range(len(built) + 1):
        hist = sorted(zip(s["acts"][:k], built[:k], range(k)), key=lambda t: (t[0]["hours_ago"], -t[2]))
        env = copy.deepcopy(check)
        env["history"] = [b for _, b, _ in hist]
        with open(os.path.join(out, s["name"], f"step-{k}.input.json"), "wb") as f:
            f.write(jcs(env) + b"\n")
