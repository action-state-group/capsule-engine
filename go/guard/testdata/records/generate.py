# Generates neutral deal-record fixtures whose keys and value types match a
# capsule-cli v0.1.0-rc9 pay record (the capsule header with its disclosed
# agent_input), and the variants the --record tests read. Every
# agent_input_digest is sha256 over the JCS form, as capsulectl writes it.
#   python3 generate.py go/guard/testdata/records
import json, hashlib, sys
out = sys.argv[1]
def h(c, n=64): return (c * n)[:n]
def jcs(v): return json.dumps(v, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
def xdeal(record_type, seq, ids):
    x = {"at": "2026-10-07T21:39:00Z", "baseline_ref": {"digest": h("1"), "digest_alg": "sha-256", "type": "deal-record"},
         "canonicalization": "jcs", "counterparty": {"fp_alg": "hmac-sha256-deal-key", "ids": ids},
         "deal_id": "deal-0000000000000001", "prev": {"digest": h("2"), "digest_alg": "sha-256", "type": "deal-record"},
         "producer": {"commit": h("3", 40), "name": "capsulectl", "version": "v0.1.0-rc9"},
         "profile": "x-deal-v0", "record_type": record_type, "seq": seq}
    return x
def capsule(capsule_id, action_type, effect, ai):
    return {"action_id": "deal-0000000000000001/" + ai["x-deal-v0"]["record_type"], "action_type": action_type,
            "assurance": {"attestation_mode": "self_attested", "effect_mode": effect, "ledger_mode": "chained"},
            "canonicalization_id": "jcs", "capsule_id": capsule_id, "developer": "capsulectl-deal", "format_version": "4",
            "key_id": h("4"), "model_attestation": {"compute_attestation": {"agent_input_digest": hashlib.sha256(jcs(ai)).hexdigest()}},
            "operator": "deal", "signature": h("5", 362), "spec_version": "draft-mih-scitt-agent-action-capsule-05",
            "timestamp": "2026-10-07T21:40:00Z", "chain": {"parent_capsule_id": h("e"), "relation": "follows"}, "agent_input": ai}
check_ai = {"body": {"action": "pay", "action_class": "money.purchase", "amount_minor": 1299, "currency": "USD",
                     "description_commitment": h("6"), "recourse": {"rail": "card", "refundable": True}, "spend_minor": 1299,
                     "taxonomy_version": "6", "terms": {"currency": "USD", "item": "Example item", "price_minor": 1299, "quantity": 1}},
            "x-deal-v0": xdeal("check", 4, {"domain": h("7"), "name": h("8"), "payee": h("9")})}
action_ai = {"body": {"action": "pay", "action_class": "money.purchase", "amount_minor": 1299, "currency": "USD", "direction": "out",
                      "rail": "card", "reference_commitment": h("a"), "spend_minor": 1299, "taxonomy_version": "6"},
             "x-deal-v0": dict(xdeal("action", 6, {"payee": h("9")}), refs=[{"digest": h("b"), "digest_alg": "sha-256", "rel": "checked_by", "type": "deal-record"}])}
check = capsule(h("c"), "fyi", "not_applicable", check_ai)
action = capsule(h("d"), "decide", "dispatched_unconfirmed", action_ai)
action["disposition"] = {"approver": "human", "decision": "accept", "human_disposed": True, "verdict_class": "executed"}
action["effect"] = {"effect_attestation": "example-receipt", "irreversibility_class": "one_way_recoverable", "status": "dispatched", "type": "send_payment"}
for name, c in [("pay-check-record.json", check), ("pay-action-record.json", action)]:
    open(f"{out}/{name}", "w").write(json.dumps(c, indent=2, sort_keys=True) + "\n")
bundle = {"records": [{k: v for k, v in c.items() if k != "agent_input"} for c in (check, action)],
          "disclosures": {c["capsule_id"]: {"agent_input": c["agent_input"]} for c in (check, action)}}
open(f"{out}/pay-records-bundle.json", "w").write(json.dumps(bundle, indent=2, sort_keys=True) + "\n")

def variant(name, edit):
    ai = json.loads(json.dumps(check_ai))
    edit(ai["body"])
    c = capsule(h("c"), "fyi", "not_applicable", ai)
    open(f"{out}/{name}", "w").write(json.dumps(c, indent=2, sort_keys=True) + "\n")
variant("pay-check-record-no-action.json", lambda b: b.pop("action"))
# A pre-authorisation with a buffer (caps/5.0.0): the capture is 454 and
# the most the payment may take is 954 (or 3000). The x-deal body seals
# authorized_max_minor beside spend_authorized_minor.
def authorised(maximum):
    def edit(b):
        b.update(amount_minor=454, spend_minor=454, spend_authorized_minor=maximum, authorized_max_minor=maximum)
        b["terms"].update(price_minor=454)
    return edit
def capture(amount):
    def edit(b):
        b.update(amount_minor=amount, spend_minor=amount)
        b["terms"].update(price_minor=amount)
    return edit
# A second 20.00 purchase in a week.
variant("pay-check-record-2000.json", capture(2000))
variant("pay-check-record-authorised-954.json", authorised(954))
variant("pay-check-record-authorised-3000.json", authorised(3000))
variant("pay-check-record-taxonomy-2.json", lambda b: b.update(taxonomy_version="2"))
# A check sealed under taxonomy 3, 4 or 5 (a capsulectl before it stamped
# 6): the record being checked must carry the engine's version, so it is a
# mismatch.
variant("pay-check-record-taxonomy-3.json", lambda b: b.update(taxonomy_version="3"))
variant("pay-check-record-taxonomy-4.json", lambda b: b.update(taxonomy_version="4"))
variant("pay-check-record-taxonomy-5.json", lambda b: b.update(taxonomy_version="5"))
variant("pay-check-record-no-taxonomy-version.json", lambda b: b.pop("taxonomy_version"))
# The body edited after sealing: the header's digest no longer matches.
tampered = json.loads(json.dumps(check))
tampered["agent_input"]["body"]["amount_minor"] = 99
open(f"{out}/pay-check-record-tampered.json", "w").write(json.dumps(tampered, indent=2, sort_keys=True) + "\n")

# History acts and external-check-input/v0 envelopes (check --emit).
import os
emit_dir = os.path.join(out, "..", "emit")
os.makedirs(emit_dir, exist_ok=True)
def act(n, ts, amount, *, spend=None, unchecked=False, direction="out", payee=h("9"), taxonomy="6"):
    body = {"action": "pay", "action_class": "money.purchase", "amount_minor": amount, "currency": "USD", "direction": direction,
            "rail": "card", "reference_commitment": h("a"), "spend_minor": amount if spend is None else spend, "taxonomy_version": taxonomy}
    if unchecked:
        body = {"action_class": "money.purchase", "taxonomy_version": taxonomy,
                "unchecked": {"action": "pay", "amount_minor": amount, "currency": "USD", "direction": direction, "rail": "card"}}
    ai = {"body": body, "x-deal-v0": dict(xdeal("action", n, {"payee": payee}), at=ts)}
    c = capsule(format(n, "x") * 64, "fyi" if unchecked else "decide", "dispatched_unconfirmed", ai)
    c["timestamp"] = ts
    c["action_id"] = "deal-%016x/action" % n
    if not unchecked:
        c["disposition"] = {"approver": "human", "decision": "accept", "human_disposed": True, "verdict_class": "executed"}
        c["effect"] = dict(action["effect"])
    return c
def envelope(name, record, history, days=31, complete=True):
    e = {"schema": "external-check-input/v0", "record": record, "history": history,
         "history_scope": {"days": days, "max_records": 1000, "complete": complete}}
    open(os.path.join(emit_dir, name), "w").write(json.dumps(e, indent=2, sort_keys=True) + "\n")
under = [act(1, "2026-10-06T09:00:00Z", 3000), act(2, "2026-10-03T18:30:00Z", 2000), act(3, "2026-09-20T12:00:00Z", 9000)]
over = [act(4, "2026-10-06T09:00:00Z", 5000), act(5, "2026-10-02T09:00:00Z", 4000)]
booking_ai = json.loads(json.dumps(check_ai))
booking_ai["body"].update(action_class="booking.create", amount_minor=55880, spend_minor=55880)
booking_ai["body"]["terms"].update(price_minor=55880, item="Example booking")
booking = capsule(h("f"), "fyi", "not_applicable", booking_ai)
def load(name): return json.load(open(os.path.join(out, name)))
envelope("pay-no-history.json", check, [])
# complete and 31 days: the history is the profile's spending, even empty.
envelope("pay-history-empty-complete.json", check, [], days=31, complete=True)
# empty, but not complete: the window cannot be evaluated.
envelope("pay-history-empty-incomplete.json", check, [], days=31, complete=False)
envelope("pay-history-under.json", check, under)
envelope("pay-history-over.json", check, over)
envelope("booking-55880.json", booking, under)
envelope("pay-taxonomy-2.json", load("pay-check-record-taxonomy-2.json"), under)
envelope("pay-tampered.json", load("pay-check-record-tampered.json"), under)
envelope("pay-history-incomplete.json", check, under, complete=False)
envelope("pay-history-5-days.json", check, under, days=5)
envelope("pay-history-unchecked.json", check, [act(6, "2026-10-06T09:00:00Z", 3000, unchecked=True)] + under)
bad = act(7, "2026-10-06T09:00:00Z", 3000)
bad["agent_input"]["body"]["amount_minor"] = 30
envelope("pay-history-tampered.json", check, [bad] + under)
# capsule-cli #142: an optional task_authority_record, here capsule-cli's own
# synthetic task-authority/v0 (emit/capsule-cli/02-task-authority.json); the
# checker reads no rule from it, so the result is pay-history-under's.
task_authority = json.load(open(os.path.join(emit_dir, "capsule-cli", "02-task-authority.json")))["record"]
with_ta = {"schema": "external-check-input/v0", "record": check, "history": under, "task_authority_record": task_authority,
           "history_scope": {"days": 31, "max_records": 1000, "complete": True}}
open(os.path.join(emit_dir, "pay-history-under-with-task-authority.json"), "w").write(json.dumps(with_ta, indent=2, sort_keys=True) + "\n")
envelope("pay-second-in-week.json", load("pay-check-record-2000.json"), [act(9, "2026-10-05T12:00:00Z", 2000)])
envelope("pay-authorised-954.json", load("pay-check-record-authorised-954.json"), under)
envelope("pay-authorised-3000.json", load("pay-check-record-authorised-3000.json"), under)
# Golden re-pinned (refund-never-blocks-spend-window): a money-in act is bridged to amount and spend 0 as the engine does, so this is pay-history-under's result.
envelope("pay-history-refund.json", check, [act(8, "2026-10-06T09:00:00Z", 3000, spend=0, direction="in")] + under)
# task_authority (r26): the check body cites capsule-cli's golden
# task-authority/v0 record (parity/task-authority-golden.json, its JCS bytes)
# by typed ref, and the envelope supplies that record: the plan at its body
# allows pay, so r26 passes. Supplied with another record instead, the ref
# does not bind it, and r26 is not evaluable.
golden_bytes = open(os.path.join(out, "..", "parity", "task-authority-golden.json"), "rb").read()
golden = json.loads(golden_bytes)
assert hashlib.sha256(golden_bytes).hexdigest() == hashlib.sha256(jcs(golden)).hexdigest()
ta_ai = json.loads(json.dumps(check_ai))
ta_ai["body"]["task_authority_ref"] = {"type": "record", "digest_alg": "SHA-256", "digest": hashlib.sha256(golden_bytes).hexdigest()}
ta_check = capsule(h("c"), "fyi", "not_applicable", ta_ai)
other = dict(golden, seq=3)
for name, supplied in (("pay-task-authority-bound.json", golden), ("pay-task-authority-other-record.json", other)):
    e = {"schema": "external-check-input/v0", "record": ta_check, "history": under, "task_authority_record": supplied,
         "history_scope": {"days": 31, "max_records": 1000, "complete": True}}
    open(os.path.join(emit_dir, name), "w").write(json.dumps(e, indent=2, sort_keys=True) + "\n")
# r06 (counterparty_seen_before): the history acts above all paid the check's
# payee (ids.payee h("9")), so the check is a repeat there. Here every
# earlier act in the window paid another payee: the check's payee is
# first-time.
other_merchant = [act(n, ts, amount, payee=h("b")) for n, ts, amount in
                  ((10, "2026-10-06T09:00:00Z", 3000), (11, "2026-10-03T18:30:00Z", 2000))]
envelope("pay-history-other-merchant.json", check, other_merchant)
# Taxonomy in the history: the check is sealed under taxonomy 6, the earlier
# acts under 2, 3, 4 or 5 (every act sealed before capsule-cli moved on).
# Versions 2 to 6 name the same classes (only approver_role changed), so they
# count; under 1, whose class names were never checked against 6, they do
# not.
for version in ("2", "3", "4", "5", "1"):
    envelope("pay-history-taxonomy-%s-acts.json" % version, check,
             [act(n, ts, amount, taxonomy=version) for n, ts, amount in
              ((12, "2026-10-06T09:00:00Z", 3000), (13, "2026-10-03T18:30:00Z", 2000), (14, "2026-09-20T12:00:00Z", 9000))])
