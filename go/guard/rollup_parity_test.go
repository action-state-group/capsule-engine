package guard

import (
	"bytes"
	"encoding/json"
	"strings"
	"testing"
)

// THE PLUGIN'S VERDICT IS THE ENGINE'S DECISION, on deal check records as
// capsulectl seals them (testdata/parity/generate_rollup_parity.py at the
// pack's commit). A real deal states upfront_amount_minor, the offer count,
// the channel and task_authority_ref only when the user states them; the
// engine reads each gap as n/a, missing field, and still decides. Each
// scenario runs on a ledger holding one accepted earlier action to the
// record's payee; both decisions are compared with Python's constraint by
// constraint, and the reported verdict with Python's outcome.
func TestRollupVerdictIsTheEngineDecision(t *testing.T) {
	var p struct {
		CapsuleEngineCommit string `json:"capsule_engine_commit"`
		Scenarios           []struct {
			Scenario            string             `json:"scenario"`
			Record              json.RawMessage    `json:"record"`
			Outcome             string             `json:"outcome"`
			Constraints         []parityConstraint `json:"constraints"`
			TaskAuthorityRecord json.RawMessage    `json:"task_authority_record"`
			Seed                *parityScenario    `json:"seed"`
		} `json:"scenarios"`
	}
	if err := json.Unmarshal(readFile(t, "testdata", "parity", "rollup-parity.json"), &p); err != nil {
		t.Fatal(err)
	}
	if p.CapsuleEngineCommit != parity(t, fixtures037).CapsuleEngineCommit {
		t.Fatalf("roll-up run at %s, parity run at %s", p.CapsuleEngineCommit, parity(t, fixtures037).CapsuleEngineCommit)
	}
	e, _ := everydayEngine(t)
	verdicts := map[string]CheckReport{}
	for _, s := range p.Scenarios {
		var ledger []Record
		if s.Seed != nil {
			a, err := DecodeAction(bytes.NewReader(s.Seed.Action))
			if err != nil {
				t.Fatalf("%s seed: %v", s.Scenario, err)
			}
			d, err := e.Check(a, nil)
			if err != nil {
				t.Fatalf("%s seed: %v", s.Scenario, err)
			}
			compareToPython(t, s.Scenario+" seed", e, a, d, *s.Seed)
			ledger = append(ledger, RecordFor(a, d))
		}
		a, err := DecodeRecord(s.Record, "")
		if err != nil {
			t.Fatalf("%s: %v", s.Scenario, err)
		}
		d, err := e.CheckWithTaskAuthority(a, MemoryHistory(ledger), s.TaskAuthorityRecord)
		if err != nil {
			t.Fatalf("%s: %v", s.Scenario, err)
		}
		compareToPython(t, s.Scenario, e, a, d, parityScenario{Scenario: s.Scenario, Outcome: s.Outcome, Constraints: s.Constraints})
		verdicts[s.Scenario] = e.Report(a, d)
	}
	// The cases the file exists for, named so a regenerated file that lost
	// one fails here; and each field the deal left out is a not_applicable
	// finding that names it.
	for name, want := range map[string]struct {
		decision string
		unstated []string
	}{
		"all-stated":                {Allow, nil},
		"no-deposit":                {Allow, []string{"r22-unusual-counterparty-behaviour"}},
		"no-offer-field":            {Allow, []string{"r10-materially-different-offer"}},
		"no-channel":                {Allow, []string{"r21-unexpected-channel-change"}},
		"no-task-authority-untyped": {Allow, []string{"r26-stay-within-task-bounds"}},
		"as-capsulectl-emits": {Allow, []string{"r10-materially-different-offer", "r21-unexpected-channel-change",
			"r22-unusual-counterparty-behaviour", "r26-stay-within-task-bounds"}},
		// From taxonomy 3 money.purchase has an approver: both ask.
		"unseen-payee": {Escalate, nil},
		"over-limit":   {Escalate, nil},
		// The engine decides on the spend (its deal bridge's amount): under
		// the limit allows whatever amount_minor says, over it asks, and
		// money moving in is no spend.
		"spend-under-amount": {Allow, nil},
		"spend-over-amount":  {Escalate, nil},
		"money-in":           {Allow, nil},
	} {
		rep, ok := verdicts[name]
		if !ok {
			t.Errorf("%s: no such scenario", name)
			continue
		}
		if rep.Decision != want.decision {
			t.Errorf("%s: %s, want %s", name, rep.Decision, want.decision)
		}
		for _, rule := range want.unstated {
			var got RuleResult
			for _, r := range rep.Rules {
				if r.Rule == rule {
					got = r
				}
			}
			if got.Verdict != VerdictNotApplicable || !strings.HasPrefix(got.Reason, "the deal states no ") {
				t.Errorf("%s: %s is %s %q, want not_applicable naming the field", name, rule, got.Verdict, got.Reason)
			}
		}
	}
}
