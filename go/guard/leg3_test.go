package guard

import (
	"bytes"
	"testing"
)

// compareToPython checks one Go decision against Python's: outcome, the
// constraint list in order, each result, and each evidence digest this port
// rebuilds.
//
// The verdict the plugin reports (Report's decision) must be the engine's
// outcome: a rule the engine reads as n/a never turns its decision into
// another one.
func compareToPython(t *testing.T, name string, e *Engine, a Action, d Decision, py parityScenario) {
	t.Helper()
	compareVerdictToPython(t, name, e, a, d, py.Outcome)
	if len(d.Constraints) != len(py.Constraints) {
		t.Fatalf("%s: Go %d constraints, Python %d", name, len(d.Constraints), len(py.Constraints))
	}
	// A check this port does not run yet is left not_evaluable in scope;
	// Python's result for it is recorded, not compared. When Python fails
	// one, its verdict rests on it, and the Go decision must carry a
	// not-yet-run rule (so it is never reported as allow) instead of
	// matching the verdict.
	unportedFail := false
	for i, pc := range py.Constraints {
		unportedFail = unportedFail || (notYetRun(d.Constraints[i]) && pc.Result == "fail")
	}
	if unportedFail {
		if !anyNotYetRun(d) {
			t.Errorf("%s: Python fails a check this port does not run, and Go carries no not-yet-run rule", name)
		}
	} else if d.Outcome != py.Outcome {
		t.Errorf("%s: Go %s, Python %s", name, d.Outcome, py.Outcome)
	}
	for i, pc := range py.Constraints {
		c := d.Constraints[i]
		if notYetRun(c) {
			if c.ID != pc.ID {
				t.Errorf("%s: constraint %d Go %s, Python %s", name, i, c.ID, pc.ID)
			}
			continue
		}
		if c.ID != pc.ID || string(c.Result) != pc.Result {
			t.Errorf("%s: constraint %d Go %s=%s, Python %s=%s", name, i, c.ID, c.Result, pc.ID, pc.Result)
			continue
		}
		digest, ok, err := c.EvidenceDigest()
		if err != nil {
			t.Fatal(err)
		}
		if ok && (pc.EvidenceDigest == nil || digest != *pc.EvidenceDigest) {
			t.Errorf("%s: %s evidence digest Go %s, Python %v", name, c.ID, digest, pc.EvidenceDigest)
		}
	}
}

// compareRulesToPython holds each rule's verdict in the Go report against
// Python's packs.obligation_results for the same decision. A rule Go leaves
// not_evaluable (its check is not run yet) is recorded, not compared, except
// a rule measured by one selector: those are ported and always compared.
func compareRulesToPython(t *testing.T, name string, e *Engine, a Action, d Decision, py parityScenario) {
	t.Helper()
	if len(py.Obligations) == 0 {
		t.Fatalf("%s: the parity file records no per-rule results", name)
	}
	verdict := map[string]string{"pass": VerdictPass, "fail": VerdictFail, "n/a": VerdictNotApplicable}
	rules := map[string]RuleResult{}
	for _, r := range e.Report(a, d).Rules {
		rules[r.Rule] = r
	}
	for _, o := range py.Obligations {
		r, ok := rules[o.ID]
		rule, _ := e.pack.Rule(o.ID)
		if !ok {
			t.Errorf("%s: no Go rule %s", name, o.ID)
			continue
		}
		if r.Verdict == VerdictNotEvaluable && rule.Selector == "" {
			continue
		}
		if want := verdict[o.Result]; r.Verdict != want {
			t.Errorf("%s: rule %s Go %s (%s), Python %s", name, o.ID, r.Verdict, r.Reason, o.Result)
		}
	}
}

// LEG 3, per record: each scenario evaluated by Go against exactly the
// records Python's engine had before it (the vendored mini_ledger.jsonl
// prefix), compared with the live Python run at the pinned commit and with
// the record mini_ledger.jsonl holds for it.
func TestLeg3ParityPerRecord(t *testing.T) {
	for _, fx := range fixtureSets {
		t.Run(fx.name, func(t *testing.T) { testLeg3ParityPerRecord(t, fx) })
	}
}

func testLeg3ParityPerRecord(t *testing.T, fx fixtureSet) {
	e, _ := fx.engine(t)
	ledger := miniLedger(t, fx)
	py := parity(t, fx)
	actions := scenarioActions(t, fx)
	if len(actions) != len(ledger)-1 {
		t.Fatalf("%d scenarios, %d ledger records after install", len(actions), len(ledger)-1)
	}
	for i, a := range actions {
		s := py.Scenarios[i]
		if s.CapsuleID != ledger[i+1].CapsuleID || ledger[i+1].ActionID != a.ActionID {
			t.Fatalf("%s: live Python run and vendored ledger disagree on record %d", s.Scenario, i+1)
		}
		d, err := e.CheckWithTaskAuthority(a, MemoryHistory(ledger[:i+1]), s.TaskAuthorityRecord)
		if err != nil {
			t.Fatalf("%s: %v", s.Scenario, err)
		}
		compareToPython(t, s.Scenario, e, a, d, s)
		compareRulesToPython(t, s.Scenario, e, a, d, s)
	}
}

// LEG 3, chained: the Go engine replaying on its own records reaches the same
// verdict and per-constraint results as Python for every scenario.
func TestLeg3ParityChained(t *testing.T) {
	for _, fx := range fixtureSets {
		t.Run(fx.name, func(t *testing.T) { testLeg3ParityChained(t, fx) })
	}
}

func testLeg3ParityChained(t *testing.T, fx fixtureSet) {
	e, _ := fx.engine(t)
	got := goReplay(t, e, fx)
	for _, s := range parity(t, fx).Scenarios {
		a, err := DecodeAction(bytes.NewReader(s.Action))
		if err != nil {
			t.Fatal(err)
		}
		d := got[a.ActionID]
		if len(d.Constraints) != len(s.Constraints) {
			t.Fatalf("%s: Go %d constraints, Python %d", s.Scenario, len(d.Constraints), len(s.Constraints))
		}
		compareVerdictToPython(t, s.Scenario, e, a, d, s.Outcome)
		unportedFail := false
		for i, pc := range s.Constraints {
			unportedFail = unportedFail || (notYetRun(d.Constraints[i]) && pc.Result == "fail")
		}
		if unportedFail && !anyNotYetRun(d) || !unportedFail && d.Outcome != s.Outcome {
			t.Errorf("%s: Go %s, Python %s (fails a check this port does not run: %v)", s.Scenario, d.Outcome, s.Outcome, unportedFail)
		}
		for i, pc := range s.Constraints {
			if c := d.Constraints[i]; notYetRun(c) {
				continue
			} else if c.ID != pc.ID || string(c.Result) != pc.Result {
				t.Errorf("%s: Go %s=%s, Python %s=%s", s.Scenario, c.ID, c.Result, pc.ID, pc.Result)
			}
		}
	}
}

// compareVerdictToPython: the plugin's reported verdict for the decision is
// the engine's outcome (behavioural parity at the decision level).
func compareVerdictToPython(t *testing.T, name string, e *Engine, a Action, d Decision, outcome string) {
	t.Helper()
	if got := e.Report(a, d).Decision; got != outcome {
		t.Errorf("%s: the plugin reports %s, the engine decided %s", name, got, outcome)
	}
}
