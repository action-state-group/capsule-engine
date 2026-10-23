package guard

import (
	"os"
	"testing"

	"github.com/action-state-group/capsule-engine/go/pack"
)

// A purchase whose caps window the history cannot decide is never allowed on
// it (engine.py check, CheckOutcome.asks_when_unevaluated; capsule-engine #214
// groups 3 and 4): with nothing else failing, a class with an approver
// escalates. The report labels it the escalation, as GuardDecision.verdict
// does (asked_unevaluated_checks, capsule-engine #217): it asks for
// approval, and it is never allowed.
func TestAHeldCapsWindowIsNeverAllowed(t *testing.T) {
	p, err := pack.LoadEveryday038()
	if err != nil {
		t.Fatal(err)
	}
	e, err := NewEngine(p)
	if err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{"pay-history-incomplete", "pay-history-5-days", "pay-history-taxonomy-1-acts"} {
		raw, err := os.ReadFile("testdata/emit/" + name + ".json")
		if err != nil {
			t.Fatal(err)
		}
		a, h, err := DecodeCheckInput(raw)
		if err != nil {
			t.Fatal(err)
		}
		d, err := e.CheckWithHistory(a, h)
		if err != nil {
			t.Fatal(err)
		}
		caps, _ := d.Constraint("caps")
		if caps.Result != NotApplicable || caps.Pending() == "" {
			t.Fatalf("%s: caps %s %q, want held", name, caps.Result, caps.Pending())
		}
		if d.anyFailed() {
			t.Fatalf("%s: a check failed; the case is about caps alone", name)
		}
		if d.Outcome != Escalate {
			t.Errorf("%s: outcome %s, want escalate", name, d.Outcome)
		}
		if r := e.Report(a, d); r.Decision != Escalate || r.Refused || r.RefusedFor != "" {
			t.Errorf("%s: report decision %s refused=%v, want escalate, not refused", name, r.Decision, r.Refused)
		}
		// On a record in no single deal the engine refuses whatever caps
		// asks (#127), so the label stays not_evaluable, never the
		// escalation, and nothing is refused for the deal: the class asks.
		inNoDeal := a
		inNoDeal.dealIDConflict = true
		if r := e.Report(inNoDeal, d); r.Decision != VerdictNotEvaluable || r.Refused || r.RefusedFor != "" {
			t.Errorf("%s in no deal: report decision %s refused=%v for %q, want not_evaluable, not refused", name, r.Decision, r.Refused, r.RefusedFor)
		}
	}
}

// With no approver for the class, the same held window is refused, and the
// report carries the refusal, labelled not_evaluable (#216, unchanged by
// #217).
func TestAHeldCapsWindowWithNoApproverIsRefused(t *testing.T) {
	p, err := pack.LoadEveryday038()
	if err != nil {
		t.Fatal(err)
	}
	e, err := NewEngine(p)
	if err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile("testdata/emit/pay-history-incomplete.json")
	if err != nil {
		t.Fatal(err)
	}
	a, h, err := DecodeCheckInput(raw)
	if err != nil {
		t.Fatal(err)
	}
	class := "external_commitment.other" // capped, with no approver_role
	a.ActionClass = &class
	d, err := e.CheckWithHistory(a, h)
	if err != nil {
		t.Fatal(err)
	}
	if caps, _ := d.Constraint("caps"); caps.Result != NotApplicable || caps.Pending() == "" || d.anyFailed() {
		t.Fatalf("caps %s %q, failed=%v: want held and nothing failed", caps.Result, caps.Pending(), d.anyFailed())
	}
	if d.Outcome != Deny {
		t.Errorf("outcome %s, want deny", d.Outcome)
	}
	if r := e.Report(a, d); r.Decision != VerdictNotEvaluable || !r.Refused || r.RefusedFor != RefusedHeldCaps {
		t.Errorf("report decision %s refused=%v for %q, want not_evaluable, refused for %q", r.Decision, r.Refused, r.RefusedFor, RefusedHeldCaps)
	}
	// The same held window on a record in no single deal is refused for the
	// deal (#127): RefusedFor names the cause a caller shows. A check that
	// fails closed is covered in sale_acceptance_test.go.
	inNoDeal := a
	inNoDeal.dealIDConflict = true
	if r := e.Report(inNoDeal, d); !r.Refused || r.RefusedFor != RefusedDealConflict {
		t.Errorf("record in no deal: refused=%v for %q, want refused for %q", r.Refused, r.RefusedFor, RefusedDealConflict)
	}
}

// Only the caps check's own held window counts: a caps n/a the taxonomy
// comparison set (refused on its own) carries no caps facts.
func TestOnlyTheCapsCheckHoldsItsWindow(t *testing.T) {
	cs := []Constraint{{ID: "caps", Result: NotApplicable, pending: "held", caps: &CapsFacts{}}}
	if !capsHeld(cs) {
		t.Fatal("a caps n/a with its facts and a pending reason is held")
	}
	if capsHeld([]Constraint{notEvaluable("caps", "m", "taxonomy")}) {
		t.Fatal("a caps n/a set by the taxonomy comparison carries no facts and is not a held window")
	}
}
