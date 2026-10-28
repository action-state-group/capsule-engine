package guard

import (
	"testing"
)

// LEG 1, the authority: decision_table.yaml row for row, checks, n/a
// populations and verdict, against the Go engine replaying the scenarios on
// its own. Any divergence here is a hard failure; it is never fixed by
// regenerating a fixture.
func TestLeg1DecisionTableRowForRow(t *testing.T) {
	for _, fx := range fixtureSets {
		t.Run(fx.name, func(t *testing.T) { testLeg1DecisionTableRowForRow(t, fx) })
	}
}

func testLeg1DecisionTableRowForRow(t *testing.T, fx fixtureSet) {
	e, _ := fx.engine(t)
	got := goReplay(t, e, fx)
	rows := decisionTable(t, fx)
	if len(rows) != len(got) {
		t.Fatalf("table has %d rows, replay produced %d decisions", len(rows), len(got))
	}
	for _, row := range rows {
		d, ok := got[row.ActionID]
		if !ok {
			t.Errorf("%s: no Go decision for %s", row.Scenario, row.ActionID)
			continue
		}
		if unportedFailed(e, row.Checks) {
			// The engine's verdict rests on a check this port does not run
			// yet: the port must leave it not_evaluable, never pass it.
			if !anyNotYetRun(d) {
				t.Errorf("%s: the table fails a check this port does not run, and the Go decision has no not-yet-run rule", row.Scenario)
			}
		} else if d.Outcome != row.Verdict {
			t.Errorf("%s: verdict %s, table %s", row.Scenario, d.Outcome, row.Verdict)
		}
		if len(d.Constraints) != len(row.Checks) {
			t.Errorf("%s: %d constraints, table names %d", row.Scenario, len(d.Constraints), len(row.Checks))
		}
		for id, want := range row.Checks {
			c, ok := d.Constraint(id)
			if ok && notYetRun(c) {
				continue // recorded by the engine, not run by this port
			}
			if !ok || string(c.Result) != want {
				t.Errorf("%s: %s = %q, table %q", row.Scenario, id, c.Result, want)
				continue
			}
			decl, declared := row.NA[id]
			if c.Result != NotApplicable {
				if declared {
					t.Errorf("%s: %s declares a population but is %s", row.Scenario, id, c.Result)
				}
				continue
			}
			if !declared {
				t.Errorf("%s: %s is n/a with no declared population", row.Scenario, id)
				continue
			}
			if string(c.Population()) != decl.Population || c.MissingField() != decl.MissingField {
				t.Errorf("%s: %s population %s/%q, table %s/%q", row.Scenario, id, c.Population(), c.MissingField(), decl.Population, decl.MissingField)
			}
		}
	}
}

// No dead rules: every check the pack's measured obligations cite has at least one pass and one
// fail row, in the table and in what the Go engine produced.
func TestLeg1NoDeadRules(t *testing.T) {
	for _, fx := range fixtureSets {
		t.Run(fx.name, func(t *testing.T) { testLeg1NoDeadRules(t, fx) })
	}
}

func testLeg1NoDeadRules(t *testing.T, fx fixtureSet) {
	e, _ := fx.engine(t)
	got := goReplay(t, e, fx)
	_, p := fx.engine(t)
	for _, check := range measuredChecks(p) {
		if _, unported := e.unported[check]; unported {
			continue // not run by this port yet: it has no pass or fail here
		}
		var tablePass, tableFail, goPass, goFail bool
		for _, row := range decisionTable(t, fx) {
			tablePass = tablePass || row.Checks[check] == "pass"
			tableFail = tableFail || row.Checks[check] == "fail"
		}
		for _, d := range got {
			c, _ := d.Constraint(check)
			goPass = goPass || c.Result == Pass
			goFail = goFail || c.Result == Fail
		}
		if !(tablePass && tableFail && goPass && goFail) {
			t.Errorf("%s: table pass=%v fail=%v, Go pass=%v fail=%v", check, tablePass, tableFail, goPass, goFail)
		}
	}
}

// Population a scored as a correct decline and population c never scored are
// both in the table; check each is exercised by name.
func TestLeg1PopulationsAAndCAreBothExercised(t *testing.T) {
	for _, fx := range fixtureSets {
		t.Run(fx.name, func(t *testing.T) { testLeg1PopulationsAAndCAreBothExercised(t, fx) })
	}
}

func testLeg1PopulationsAAndCAreBothExercised(t *testing.T, fx fixtureSet) {
	var a, c bool
	for _, row := range decisionTable(t, fx) {
		for _, decl := range row.NA {
			a = a || (decl.Population == "a" && decl.MissingField != "")
			c = c || decl.Population == "c"
		}
	}
	if !a || !c {
		t.Fatalf("table exercises population a=%v c=%v", a, c)
	}
}

func ptr[T any](v T) *T { return &v }

// Population b, an n/a with no evidence, is unproducible: over the replay and
// over every combination of present and absent fields for every class the
// pack names, each n/a carries a population (a or c) and a digestable
// evidence object.
func TestLeg1PopulationBIsUnproducible(t *testing.T) {
	for _, fx := range fixtureSets {
		t.Run(fx.name, func(t *testing.T) { testLeg1PopulationBIsUnproducible(t, fx) })
	}
}

func testLeg1PopulationBIsUnproducible(t *testing.T, fx fixtureSet) {
	e, _ := fx.engine(t)
	var decisions []Decision
	for _, d := range goReplay(t, e, fx) {
		decisions = append(decisions, d)
	}
	ledger := miniLedger(t, fx)
	classes := []*string{nil, ptr("money.transfer"), ptr("money.purchase"), ptr("booking.create"), ptr("comms.external"), ptr("info.query"), ptr("unknown.class")}
	for _, class := range classes {
		for mask := 0; mask < 1<<6; mask++ {
			a := Action{Verb: "make_payment", Operator: "o", Developer: "household-assistant-a@v1", ActionClass: class,
				ActionID: "make_payment/matrix", ActionType: "decide", Timestamp: "2026-08-10T11:00:00Z"}
			if mask&1 != 0 {
				a.AmountMinor = ptr(int64(100))
			}
			if mask&2 != 0 {
				a.Target = ptr("utility/water-co")
			}
			if mask&4 != 0 {
				a.Rail = ptr("p2p")
			}
			if mask&8 != 0 {
				a.CounterpartyAccountRef = ptr("acct-ref-water-9")
			}
			if mask&16 != 0 {
				a.OutgoingContent = ptr("code 123456")
			}
			if mask&32 != 0 {
				a.Recurrence = ptr("monthly")
			}
			d, err := e.Check(a, MemoryHistory(ledger))
			if err != nil {
				t.Fatal(err)
			}
			decisions = append(decisions, d)
		}
	}
	for _, d := range decisions {
		for _, c := range d.Constraints {
			if c.Result != NotApplicable || notYetRun(c) {
				continue // a check this port does not run yet is not the engine's n/a
			}
			digest, ok, err := c.EvidenceDigest()
			if err != nil || !ok || digest == "" {
				t.Fatalf("%s n/a without evidence", c.ID)
			}
			if p := c.Population(); p != PopulationA && p != PopulationC {
				t.Fatalf("%s n/a with population %q", c.ID, p)
			}
			if (c.Population() == PopulationA) != (c.MissingField() != "") {
				t.Fatalf("%s: population %s with missing field %q", c.ID, c.Population(), c.MissingField())
			}
		}
	}
}
