# Bridge + price_floor vectors for capsule-engine #129 (stated_amount_minor). Run from a capsule-engine checkout at 8522efc.
import json, sys
from agent_action_capsule import json_digest
from tests.test_price_floor_committed_bounds import AUTHORITY, OPENING, CLASSES, FLOOR_MINOR, _action
from capsule_engine.guards.checks import check_price_floor
from capsule_engine.report.replay import action_for_record

def bridged(body):
    disclosed = {"x-deal-v0": {"record_type": "check"},
                 "body": {"action": "commit", "action_class": "agreement.accept", "taxonomy_version": "6", **body}}
    record = {"operator": "deal", "developer": "capsulectl-deal", "action_id": "deal/1",
              "model_attestation": {"compute_attestation": {"agent_input_digest": json_digest(disclosed)}}}
    a = action_for_record(record, disclosed)
    return {"amount_minor": a.amount_minor, "stated_amount_minor": a.stated_amount_minor}

bodies = {"sale-at-1900-spend-0": {"spend_minor": 0, "amount_minor": 190000},
          "purchase-spend-only": {"spend_minor": 2000},
          "stated-wrong-shape": {"spend_minor": 0, "amount_minor": "190000"},
          "refund-direction-in": {"direction": "in", "spend_minor": 500, "amount_minor": 500}}
floor_cases = {"stated-at-floor": (0, FLOOR_MINOR), "stated-below-floor": (0, FLOOR_MINOR - 1),
               "no-stated-spend-below": (FLOOR_MINOR - 1, None), "no-stated-spend-at": (FLOOR_MINOR, None)}
out = {"floor_minor": FLOOR_MINOR, "bridge": {k: bridged(v) for k, v in bodies.items()}, "price_floor": {}}
for k, (spend, stated) in floor_cases.items():
    c = check_price_floor(_action(spend, stated_amount_minor=stated), AUTHORITY, OPENING, action_classes=CLASSES).constraint
    out["price_floor"][k] = {"amount_minor": spend, "stated_amount_minor": stated, "result": c.result, "evidence": c.evidence}
json.dump({"bodies": bodies, "floor_cases": {k: list(v) for k, v in floor_cases.items()}}, open(sys.argv[1] + "/corpus.json", "w"), sort_keys=True, indent=1)
json.dump(out, open(sys.argv[1] + "/python-decisions.json", "w"), sort_keys=True, indent=1)
