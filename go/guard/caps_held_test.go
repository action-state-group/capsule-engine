package guard

import (
	"os"
	"testing"

	"github.com/action-state-group/capsule-engine/go/pack"
)

// A purchase whose caps window the history cannot decide is never allowed on
// it (engine.py check, CheckOutcome.asks_when_unevaluated; capsule-engine #214
// groups 3 and 4): with nothing else failing, a class with an approver
// escalates. The report labels it not_evaluable, as GuardDecision.verdict
// does (asked_unevaluated_checks), so a caller reading the report sees the
// same label as before and one reading the outcome never sees an allow.
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
		if r := e.Report(a, d); r.Decision != VerdictNotEvaluable || r.Refused {
			t.Errorf("%s: report decision %s refused=%v, want not_evaluable, not refused", name, r.Decision, r.Refused)
		}
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
