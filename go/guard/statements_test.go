package guard

import (
	"bytes"
	"encoding/json"
	"strings"
	"testing"
)

// claimEntryAt is a-offer-1900's one history entry, buyer A's condition
// claim, with its capsule header's timestamp set to at ("" removes it). The
// timestamp is outside the agent_input the capsule binds, so the claim
// still binds.
func claimEntryAt(t *testing.T, at string) (json.RawMessage, Action) {
	t.Helper()
	var in struct {
		Record       json.RawMessage   `json:"record"`
		InputHistory []json.RawMessage `json:"history"`
	}
	if err := json.Unmarshal(readFile(t, seller013Vectors, "go-vectors", "check-inputs", "a-offer-1900.claims-in-history.json"), &in); err != nil {
		t.Fatal(err)
	}
	a, err := DecodeCheckInputRecord(in.Record)
	if err != nil {
		t.Fatal(err)
	}
	var entry map[string]json.RawMessage
	if err := json.Unmarshal(in.InputHistory[0], &entry); err != nil {
		t.Fatal(err)
	}
	if at == "" {
		delete(entry, "timestamp")
	} else {
		entry["timestamp"] = json.RawMessage(`"` + at + `"`)
	}
	raw, err := json.Marshal(entry)
	if err != nil {
		t.Fatal(err)
	}
	return raw, a
}

// Live, a claim counts only when sealed at or before the check's time (the
// check is sealed at 00:41:06): in its own second it counts, a second later
// it does not, and with its time or the check's unreadable it does not
// (live_history.py); an unreadable claim time also leaves the history
// incomplete, as the engine reads it. Given twice, it is written once.
func TestALiveClaimCountsOnlyWhenSealedByTheCheck(t *testing.T) {
	for _, c := range []struct {
		at                string
		checkAt           string
		written, complete bool
	}{
		{"2026-10-10T00:41:05Z", "", true, true},
		{"2026-10-10T00:41:06Z", "", true, true},
		{"2026-10-10T00:41:07Z", "", false, true},
		{"yesterday", "", false, false},
		{"", "", false, false},
		{"2026-10-10T00:41:05Z", "not a time", false, true},
	} {
		entry, a := claimEntryAt(t, c.at)
		if c.checkAt != "" {
			a.Timestamp = c.checkAt
		}
		h := InputHistory{Complete: true}
		for range 2 {
			if err := h.AddEntry(entry, a); err != nil {
				t.Fatalf("%q: %v", c.at, err)
			}
		}
		if got := len(h.Acts); got != map[bool]int{true: 1, false: 0}[c.written] {
			t.Errorf("claim at %q, check at %q: %d records written", c.at, a.Timestamp, got)
		}
		if h.Complete != c.complete {
			t.Errorf("claim at %q: complete %v", c.at, h.Complete)
		}
		for _, r := range h.Acts {
			if r.Stated == nil || r.Decision != nil || r.AmountMinor != nil || r.Target != nil || r.ActionClass != nil {
				t.Errorf("claim at %q written as %+v", c.at, r)
			}
		}
	}
}

// A statement in the history is not an act: the spend window of a pack with
// a caps definition is not held by it (it carries no decision and no spend),
// and dedupe never matches it.
func TestALiveClaimIsNeverAnAct(t *testing.T) {
	entry, _ := claimEntryAt(t, "2026-10-10T00:41:05Z")
	e, _ := everydayEngine(t)
	a, err := decodeAction([]byte(`{"verb":"pay","operator":"synthetic-seller","developer":"d","timestamp":"2026-10-10T00:42:00Z","amount_minor":100,"target":"t","action_class":"money.purchase"}`), true)
	if err != nil {
		t.Fatal(err)
	}
	h := InputHistory{Days: 31, Complete: true}
	if err := h.AddEntry(entry, a); err != nil {
		t.Fatal(err)
	}
	if len(h.Acts) != 1 {
		t.Fatalf("%d records written", len(h.Acts))
	}
	if why, _ := e.windowCoverage(a, h); why != "" {
		t.Errorf("a statement holds the window: %s", why)
	}
	c, err := e.constraintFor("dedupe", a, MemoryHistory(h.Acts), checkInputs{})
	if err != nil || c.Result != Pass {
		t.Errorf("dedupe over a statement: %s %v", c.Result, err)
	}
}

// required_disclosure/1.1.0 with no target counts the deal's statements;
// with neither a target nor a deal it is n/a, the target missing; under
// 1.0.0 a statement in the deal never counts.
func TestRequiredStatementsByDealAndTarget(t *testing.T) {
	entry, a := claimEntryAt(t, "2026-10-10T00:41:05Z")
	stated, ok := HistoryStatement(entry)
	if !ok {
		t.Fatal("no statement")
	}
	ledger := []Record{stated}
	e13, _ := seller013Engine(t)
	e12, _ := seller012Engine(t)
	noTarget := a
	noTarget.Target = nil
	neither := noTarget
	neither.DealID = nil
	otherDeal := a
	otherDeal.DealID = new("deal-elsewhere")
	otherOperator := a
	otherOperator.Operator = "another-seller"
	for _, c := range []struct {
		name string
		e    *Engine
		a    Action
		want Result
	}{
		{"deal and target", e13, a, Pass},
		{"deal, no target", e13, noTarget, Pass},
		{"neither", e13, neither, NotApplicable},
		{"another deal", e13, otherDeal, Fail},
		{"another operator", e13, otherOperator, Fail},
		{"1.0.0", e12, a, Fail},
	} {
		got, err := c.e.constraintFor("required_disclosure", c.a, MemoryHistory(ledger), checkInputs{})
		if err != nil {
			t.Fatal(err)
		}
		if got.Result != c.want {
			t.Errorf("%s: %s, want %s", c.name, got.Result, c.want)
		}
	}
}

// A claim whose disclosed agent_input is not the one its capsule binds
// states nothing (replay.py _bound): the vector's agent condition claim with
// its class edited after sealing.
func TestAnUnboundClaimIsNoStatement(t *testing.T) {
	v := loadStatementVectors(t)
	c := v.Reader[0]
	if c.Case != "agent condition" {
		t.Fatalf("reader case %q", c.Case)
	}
	if _, ok := StatementOf(c.Capsule, c.AgentInput); !ok {
		t.Fatal("the bound claim is no statement")
	}
	edited := json.RawMessage(strings.Replace(string(c.AgentInput), `"condition"`, `"warranty"`, 1))
	if s, ok := StatementOf(c.Capsule, edited); ok {
		t.Errorf("an unbound claim states %+v", s.Stated)
	}
}

// A ledger capsule carrying asg_payload.stated, as the engine's replay
// writes a statement to its ledger, is read as that statement: an offer in
// its deal then passes required_disclosure/1.1.0.
func TestALedgerStatementIsRead(t *testing.T) {
	v := loadStatementVectors(t)
	line := v.Reader[0].Statement
	ledger, err := readCapsules(bytes.NewReader(line))
	if err != nil || len(ledger) != 1 || ledger[0].Stated == nil {
		t.Fatalf("read %+v %v", ledger, err)
	}
	if s := *ledger[0].Stated; s != (Statement{Class: "condition", SourceKind: "agent", DealID: "deal-a", Claim: "e72c8d70" + strings.Repeat("c", 56)}) {
		t.Errorf("stated %+v", s)
	}
	e, _ := seller013Engine(t)
	a := Action{Operator: "synthetic-seller", ActionClass: new("marketplace.offer"), DealID: new("deal-a")}
	c, err := e.constraintFor("required_disclosure", a, MemoryHistory(ledger), checkInputs{})
	if err != nil || c.Result != Pass {
		t.Errorf("required_disclosure %s %v", c.Result, err)
	}
}
