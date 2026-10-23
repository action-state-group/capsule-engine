"""Each case: a change to the deal_claims (or the record) of the vendored
a-commit-1900 record-deal-claims envelope, decided by capsule-engine's
deal_claim_statements and action_for_check_input. Run with PYTHONPATH at a
capsule-engine checkout; argv[1] the envelope, argv[2] the output."""
import copy, json, sys
from capsule_engine.report.replay import deal_claim_statements, action_for_check_input
env = json.load(open(sys.argv[1]))
base = env["record"]
first = base["deal_claims"][0]
def entry(**change):
    e = {**copy.deepcopy(first), **change}
    return {k: v for k, v in e.items() if v is not None}
CASES = {
    "as given": base["deal_claims"],
    "absent": None,
    "empty list": [],
    "no class": [entry(**{"class": None})],
    "empty class": [entry(**{"class": ""})],
    "class not a string": [entry(**{"class": 7})],
    "a class outside the set": [entry(**{"class": "warranty_terms"})],
    "delivery_promise": [entry(**{"class": "delivery_promise"})],
    "the buyer's": [entry(source_kind="counterparty")],
    "no source_kind": [entry(source_kind=None)],
    "digest upper case": [entry(record_digest="AB" * 32)],
    "digest short": [entry(record_digest="ab" * 31)],
    "no capsule_id": [entry(capsule_id=None)],
    "capsule_id upper case": [entry(capsule_id=first["capsule_id"].upper())],
    "no commitment": [entry(text_commitment=None)],
    "commitment short": [entry(text_commitment="ab" * 31)],
    "at not a time": [entry(at="yesterday")],
    "at with no zone": [entry(at="2026-10-10T00:41:06")],
    "no at": [entry(at=None)],
    "at not a string": [entry(at=1760057000)],
    "at after the check": [entry(at="2030-01-01T00:00:00Z")],
    "at in the check's own second": [entry(at=base["timestamp"])],
    "at with an offset": [entry(at=first["at"].replace("Z", "+00:00"))],
    "given twice": [entry(), entry()],
    "one bad, one good": [entry(), entry(**{"class": ""})],
    "one bad, then one good": [entry(**{"class": ""}), entry()],
    "two claims": [entry(), entry(capsule_id="ab" * 32, **{"class": "delivery_date"})],
    "a string": "condition",
    "a mapping": {"class": "condition"},
    "a number": 5,
    "a boolean": True,
    "a null entry": [None],
    "a list entry": [["condition"]],
    "null": "NULL",
}
out = []
for name, claims in CASES.items():
    record = copy.deepcopy(base)
    if claims is None:
        record.pop("deal_claims", None)
    elif claims == "NULL":
        record["deal_claims"] = None
    else:
        record["deal_claims"] = claims
    got = deal_claim_statements(record)
    action = action_for_check_input(record)
    out.append({"case": name, "deal_claims": record.get("deal_claims", "<absent>") if "deal_claims" in record else "<absent>",
                "statements": [dict(s) for s in got.statements], "ignored": got.ignored,
                "ignored_inputs": list(action.ignored_inputs)})
json.dump({"generator": "gen_deal_claims_cases.py", "cases": out}, open(sys.argv[2], "w"), indent=1, sort_keys=True)
print(len(out), "cases")
