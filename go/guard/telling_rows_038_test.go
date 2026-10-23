package guard

import "testing"

// THE TELLING ROWS OF THE DEFAULT PACK: capsule-engine 0c93a58's decision
// table for asg/everyday/0.3.8 adds three whole-pack rows that carry what the
// producer's telling memory reports. On the engine's own fixtures (the
// fixtures038 set), Go decides each as the table and the live Python run
// do: a first telling to a merchant fails r11 and r12 and asks; a repeat of
// the classes told before passes both gate rules out (n/a) and is allowed; a
// class new to a merchant told before fails r11 alone and asks.
func TestTheDefaultPacksTellingRowsDecideAsTheEngine(t *testing.T) {
	e, p := fixtures038.engine(t)
	if p.ID != "asg/everyday/0.3.8" {
		t.Fatalf("fixtures038 runs %s", p.ID)
	}
	// telling is what each row is held to: r11's and r12's verdicts and the
	// decision.
	type telling struct{ r11, r12, decision string }
	want := map[string]telling{
		"first-telling-to-a-merchant":         {VerdictFail, VerdictFail, Escalate},
		"repeat-telling-to-a-merchant":        {VerdictNotApplicable, VerdictNotApplicable, Allow},
		"new-class-to-a-merchant-told-before": {VerdictFail, VerdictNotApplicable, Escalate},
	}
	table := map[string]string{}
	for _, row := range decisionTable(t, fixtures038) {
		table[row.Scenario] = row.Verdict
	}
	ledger := miniLedger(t, fixtures038)
	py := parity(t, fixtures038)
	actions := scenarioActions(t, fixtures038)
	seen := 0
	for i, a := range actions {
		s := py.Scenarios[i]
		w, ok := want[s.Scenario]
		if !ok {
			continue
		}
		seen++
		if table[s.Scenario] != w.decision || s.Outcome != w.decision {
			t.Errorf("%s: the table says %s and the engine %s, this test %s", s.Scenario, table[s.Scenario], s.Outcome, w.decision)
		}
		d, rules := reportOf(t, e, a, ledger[:i+1])
		got := telling{rules["r11-home-address-to-an-individual"].Verdict, rules["r12-personal-contact-to-a-new-party"].Verdict, d.Outcome}
		if got != w {
			t.Errorf("%s: Go gives %+v, want %+v", s.Scenario, got, w)
		}
	}
	if seen != len(want) {
		t.Fatalf("%d of the %d telling rows are in the 0.3.8 fixtures", seen, len(want))
	}
}
