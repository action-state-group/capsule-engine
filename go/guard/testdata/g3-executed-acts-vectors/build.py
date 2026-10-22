#!/usr/bin/env python3
"""Build the G3 executed-acts vector envelopes from corpus.json and templates/.

Deterministic: python3 build.py <this dir> rewrites <this dir>/inputs/ byte for byte.
Each act is a real capsulectl capsule (templates/, see README) with its header's capsule_id,
action_id, operator and timestamp, its deal id, and the body members the corpus names
replaced; agent_input_digest is recomputed (SHA-256 over the JCS bytes of agent_input), and
the signature is dropped (the checker does not verify one; these are not signed records).
"""
import copy, datetime, hashlib, json, os, shutil, sys

D = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.abspath(__file__))


def jcs(v):
    # JCS for the values these records carry: objects, arrays, strings, integers, booleans, null.
    return json.dumps(v, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def load(name):
    with open(os.path.join(D, name)) as f:
        return json.load(f)


corpus = load("corpus.json")
check = load("templates/check.json")
at = datetime.datetime.strptime(check["record"]["timestamp"], "%Y-%m-%dT%H:%M:%SZ")


def ident(seq, act_id):
    return hashlib.sha256(f"g3-executed-acts/{seq}/{act_id}".encode()).hexdigest()


def build(seq, act, by_id):
    cap = copy.deepcopy(load(f"templates/{act['template']}.json"))
    ai = cap["agent_input"]
    body = ai["body"]["unchecked"] if act["template"] == "unchecked" else ai["body"]
    for k, v in act.get("body", {}).items():
        if v is None:
            body.pop(k, None)
        else:
            body[k] = v
    cid_of = act.get("same_capsule_id_as", act["id"])
    aid_of = act.get("same_action_id_as", act["id"])
    deal = "deal-g3" + ident(seq, aid_of)[:12]
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
    cap.pop("signature", None)
    cap["model_attestation"] = {"compute_attestation": {"agent_input_digest": hashlib.sha256(jcs(ai)).hexdigest()}}
    return cap


out = os.path.join(D, "inputs")
shutil.rmtree(out, ignore_errors=True)
for s in corpus["sequences"]:
    os.makedirs(os.path.join(out, s["name"]))
    by_id, built = {}, []
    for a in s["acts"]:
        built.append(build(s["name"], a, by_id))
        by_id.setdefault(a["id"], built[-1])
    for k in range(len(built) + 1):
        hist = sorted(zip(s["acts"][:k], built[:k], range(k)), key=lambda t: (t[0]["hours_ago"], -t[2]))
        env = copy.deepcopy(check)
        env["history"] = [b for _, b, _ in hist]
        with open(os.path.join(out, s["name"], f"step-{k}.input.json"), "wb") as f:
            f.write(jcs(env) + b"\n")
