package guard

import (
	"bytes"
	"encoding/json"
	"testing"
)

// recipient_seen_before/1.0.0 (rule r13) against a live Python run of the
// same engine at the same commit (testdata/parity/
// generate_recipient_seen_before.py). The scenarios run chained from the
// fixture ledger's install record, as Python's did; every constraint's
// result and evidence digest must match, and recipient_seen_before itself
// must be RUN by this port (compareToPython alone would skip a not-yet-run
// constraint).
func TestRecipientSeenBeforeParity(t *testing.T) {
	var p struct {
		CapsuleEngineCommit string `json:"capsule_engine_commit"`
		Scenarios           []struct {
			parityScenario
			DryRun bool `json:"dry_run"`
		} `json:"scenarios"`
	}
	if err := json.Unmarshal(readFile(t, "testdata", "parity", "recipient-seen-before-parity.json"), &p); err != nil {
		t.Fatal(err)
	}
	if p.CapsuleEngineCommit != parity(t, fixtures037).CapsuleEngineCommit {
		t.Fatalf("recipient_seen_before run at %s, parity run at %s", p.CapsuleEngineCommit, parity(t, fixtures037).CapsuleEngineCommit)
	}
	e, _ := everydayEngine(t)
	ledger := miniLedger(t, fixtures037)[:1]
	got := map[string]Constraint{}
	for _, s := range p.Scenarios {
		a, err := DecodeAction(bytes.NewReader(s.Action))
		if err != nil {
			t.Fatalf("%s: %v", s.Scenario, err)
		}
		d, err := e.Check(a, MemoryHistory(ledger))
		if err != nil {
			t.Fatalf("%s: %v", s.Scenario, err)
		}
		compareToPython(t, s.Scenario, e, a, d, s.parityScenario)
		c, ok := d.Constraint("recipient_seen_before")
		if !ok || notYetRun(c) {
			t.Fatalf("%s: recipient_seen_before is not run by this port (%+v)", s.Scenario, c)
		}
		// compareToPython compares the digest only when Go has one: here it
		// must, so the evidence is held to Python's, not just the result.
		if _, ok, err := c.EvidenceDigest(); err != nil || !ok {
			t.Errorf("%s: recipient_seen_before has no evidence digest to compare (%v)", s.Scenario, err)
		}
		got[s.Scenario] = c
		r := RecordFor(a, d)
		if s.DryRun {
			r.DryRun = boolp(true)
		}
		ledger = append(ledger, r)
	}
	// The four outcomes and what the fold counts, named so a regenerated
	// file that lost one fails here rather than passing on fewer rows.
	for name, want := range map[string]struct {
		result     Result
		population Population
		missing    string
	}{
		"out-of-scope-purchase":             {NotApplicable, PopulationC, ""},
		"message-no-target":                 {NotApplicable, PopulationA, "target"},
		"message-first-time":                {Fail, "", ""},
		"message-after-refused":             {Fail, "", ""},
		"message-repeat":                    {Pass, "", ""},
		"message-repeat-communication-send": {Pass, "", ""},
		"message-after-dry-run":             {Fail, "", ""},
		"message-other-operators-recipient": {Fail, "", ""},
	} {
		c, ok := got[name]
		if !ok {
			t.Errorf("%s: no such scenario", name)
			continue
		}
		if c.Result != want.result {
			t.Errorf("%s: %s, want %s", name, c.Result, want.result)
		}
		if want.result == NotApplicable && (c.Population() != want.population || c.MissingField() != want.missing) {
			t.Errorf("%s: population %s/%q, want %s/%q", name, c.Population(), c.MissingField(), want.population, want.missing)
		}
	}
}
