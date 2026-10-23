package guard

import (
	"bytes"
	"encoding/json"
	"testing"
)

// gateRules are the eight rules asg/everyday/0.3.7 measures by one
// action_class_gate selector each, with that selector.
var gateRules = map[string]string{
	"r01-research-and-prepare":            "non_consequential",
	"r04-prepare-without-committing":      "non_consequential",
	"r11-home-address-to-an-individual":   "personal_disclosure",
	"r12-personal-contact-to-a-new-party": "personal_disclosure",
	"r15-public-posting":                  "public_posting",
	"r16-booking-with-a-commitment":       "booking_create",
	"r17-cancellation":                    "booking_cancel",
	"r18-delete-persistent-data":          "destructive_mutation",
}

func reportOf(t *testing.T, e *Engine, a Action, ledger []Record) (Decision, map[string]RuleResult) {
	t.Helper()
	d, err := e.Check(a, ledger)
	if err != nil {
		t.Fatal(err)
	}
	rules := map[string]RuleResult{}
	for _, r := range e.Report(a, d).Rules {
		rules[r.Rule] = r
	}
	return d, rules
}

// The pack names each gate rule's selector, as loaded.
func TestEveryGateRuleNamesItsSelector(t *testing.T) {
	_, p := everydayEngine(t)
	for id, sel := range gateRules {
		if r, ok := p.Rule(id); !ok || r.Check != "action_class_gate" || r.Selector != sel {
			t.Errorf("%s: %+v, want action_class_gate selector %s", id, r, sel)
		}
	}
}

// A class with no taxonomy row fails the gate closed, and every gate rule
// fails with it: none of them can be ruled out (obligation_results.py).
func TestAnUnmappedClassFailsEveryGateRule(t *testing.T) {
	e, _ := everydayEngine(t)
	a := Action{Verb: "do", Operator: "op", Developer: "dev", ActionClass: strp("household.unlisted"), ActionID: "do/x",
		ActionType: "decide", Timestamp: "2026-10-07T10:00:00Z"}
	d, rules := reportOf(t, e, a, nil)
	for id := range gateRules {
		if r := rules[id]; r.Verdict != VerdictFail {
			t.Errorf("%s: %s, want fail", id, r.Verdict)
		}
	}
	if d.Outcome != Deny {
		t.Errorf("outcome %s, want deny", d.Outcome)
	}
}

// TAXONOMY 3: money.purchase has approver_role account_holder, so a first
// purchase at a merchant with no accepted history and an over-limit
// purchase ask (escalate); a repeat of an accepted purchase is still
// refused (dedupe is not escalatable).
func TestPurchasesAskUnderTaxonomy3(t *testing.T) {
	e := shippedCapsEngine(t)
	known := []Record{prior("op", "shop/known", "accept", nil)}

	first := purchase("shop/new")
	if d, rules := reportOf(t, e, first, nil); d.Outcome != Escalate || rules["r06-new-merchant"].Verdict != VerdictFail {
		t.Errorf("first purchase at an unseen merchant: %s, r06 %+v; want escalate", d.Outcome, rules["r06-new-merchant"])
	}

	over := purchase("shop/known")
	over.AmountMinor = i64p(3000)
	if d, rules := reportOf(t, e, over, known); d.Outcome != Escalate || rules["r05-spending-limits"].Verdict != VerdictFail ||
		rules["r06-new-merchant"].Verdict != VerdictPass {
		t.Errorf("over-limit purchase at a known merchant: %s, r05 %+v; want escalate", d.Outcome, rules["r05-spending-limits"])
	}

	// The same purchase again, after the first was accepted and recorded.
	repeat := purchase("shop/known")
	accepted := RecordFor(repeat, Decision{Outcome: Allow})
	accepted.CapsuleID = "c-accepted"
	d, err := e.Check(repeat, []Record{accepted})
	if err != nil {
		t.Fatal(err)
	}
	if dd, _ := d.Constraint("dedupe"); d.Outcome != Deny || dd.Result != Fail {
		t.Errorf("repeat of an accepted purchase: %s, dedupe %s; want deny", d.Outcome, dd.Result)
	}
}

// ASK-declared gate rules ask (capsule-engine #104 c29e738, taxonomy 4
// 5300e0b): an action_class_gate failure escalates when every selector it
// failed on is bound only to ASK-declared rules and the action's class has
// an approver_role. From taxonomy 4 every class an ASK-declared gate rule
// covers names account_holder, so all six ask: r16 (booking.create), r17
// (booking.cancel), r18 (data.delete), r15 (communication.publish), and r11
// and r12 (disclosure.personal, one selector, so a share fails both). In
// each case the gate is the only failure and the rule fails.
func TestASKDeclaredGateRulesAskOnlyWhereTheClassHasAnApprover(t *testing.T) {
	e, p := everydayEngine(t)
	for id, class := range map[string]string{
		"r11-home-address-to-an-individual":   "disclosure.personal",
		"r12-personal-contact-to-a-new-party": "disclosure.personal",
		"r15-public-posting":                  "communication.publish",
		"r16-booking-with-a-commitment":       "booking.create",
		"r17-cancellation":                    "booking.cancel",
		"r18-delete-persistent-data":          "data.delete",
	} {
		if r, _ := p.Rule(id); r.DefaultDisposition != "ASK" {
			t.Fatalf("%s is declared %s, not ASK", id, r.DefaultDisposition)
		}
		if _, hasApprover := e.tax.approver(class); !hasApprover {
			t.Fatalf("%s: %s has no approver", id, class)
		}
		a := Action{Verb: "act", Operator: "op", Developer: "dev", ActionClass: strp(class), ActionID: "act/" + id,
			ActionType: "decide", Timestamp: "2026-10-07T10:00:00Z"}
		d, rules := reportOf(t, e, a, nil)
		var fails []string
		for _, con := range d.Constraints {
			if con.Result == Fail {
				fails = append(fails, con.ID)
			}
		}
		if len(fails) != 1 || fails[0] != "action_class_gate" || rules[id].Verdict != VerdictFail {
			t.Fatalf("%s (%s): failing constraints %v, rule %+v; want the gate alone, failing this rule", id, class, fails, rules[id])
		}
		if d.Outcome != Escalate {
			t.Errorf("%s (%s): outcome %s, want escalate", id, class, d.Outcome)
		}
	}
}

// PER-RULE SELECTORS (0.3.2): A booking (booking.create, 558.80, the
// caps-v5 parity scenario booking-over-limit) fails r16 and no other gate rule.
// Under 0.3.1 every rule citing action_class_gate took the gate's one result,
// so the booking also failed r11, r12, r15, r17 and r18.
func TestABookingOverItsLimitFailsOnlyItsOwnGateRule(t *testing.T) {
	var p struct {
		Scenarios []parityScenario `json:"scenarios"`
	}
	if err := json.Unmarshal(readFile(t, "testdata", "parity", "caps-v5-parity.json"), &p); err != nil {
		t.Fatal(err)
	}
	var booking Action
	for _, s := range p.Scenarios {
		if s.Scenario == "booking-over-limit" {
			a, err := DecodeAction(bytes.NewReader(s.Action))
			if err != nil {
				t.Fatal(err)
			}
			booking = a
		}
	}
	if booking.ActionClass == nil || *booking.ActionClass != "booking.create" {
		t.Fatalf("no booking-over-limit scenario: %+v", booking)
	}
	_, rules := reportOf(t, shippedCapsEngine(t), booking, nil)
	for id := range gateRules {
		want := VerdictNotApplicable
		if id == "r16-booking-with-a-commitment" {
			want = VerdictFail
		}
		if r := rules[id]; r.Verdict != want {
			t.Errorf("%s: %s (%s), want %s", id, r.Verdict, r.Reason, want)
		}
	}
	if r := rules["r11-home-address-to-an-individual"]; r.Reason != "action class booking.create did not match selector personal_disclosure" {
		t.Errorf("r11 reason %q", r.Reason)
	}
}
