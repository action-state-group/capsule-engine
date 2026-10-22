package guard

import (
	"encoding/json"
	"testing"

	"github.com/action-state-group/capsule-engine/go/pack"
)

// approvalStatement is a seller's delivery-date statement (the S2 corpus's
// delivery-date-citing-an-approval-not-supplied), with authorized_by set to
// cited, or left out when cited is "".
func approvalStatement(t *testing.T, cited string) Action {
	t.Helper()
	raw := `{"verb":"tell","operator":"seller-s2","developer":"seller-assistant@v1","action_class":"communication.send",` +
		`"action_id":"tell/s2-2","timestamp":"2026-10-09T11:02:00Z","equivalence_key":"tell/s2-2",` +
		`"target":"cp-fp:1111111111111111111111111111111111111111111111111111111111111111","representation_class":"delivery_date"`
	if cited != "" {
		raw += `,"authorized_by":"` + cited + `"`
	}
	a, err := decodeAction(json.RawMessage(raw+"}"), true)
	if err != nil {
		t.Fatal(err)
	}
	return a
}

const citedApproval = "16f79ba926924ec021c14b910b81ad96600cd5fff34105cab6cbe124a08e48b6"

// s04 (promise_requires_approval) on an engine given no approval records, as
// the plugin's is: a statement outside the task authority that cites an
// approval is not evaluated, with the reason, and the report never allows
// it; one that cites none still fails. The engine's default is unchanged:
// a cited approval that is not supplied fails.
func TestPromiseRequiresApprovalWithoutApprovalRecords(t *testing.T) {
	p, err := pack.LoadSeller013()
	if err != nil {
		t.Fatal(err)
	}
	for _, c := range []struct {
		name, cited string
		without     bool
		result      Result
		pending     string
	}{
		{"cites an approval, no records", citedApproval, true, NotApplicable, NoApprovalRecord},
		{"cites nothing, no records", "", true, Fail, ""},
		{"cites an approval, engine default", citedApproval, false, Fail, ""},
	} {
		e, err := NewEngine(p)
		if err != nil {
			t.Fatal(err)
		}
		if c.without {
			e.WithoutApprovalRecords()
		}
		a := approvalStatement(t, c.cited)
		d, err := e.Check(a, nil)
		if err != nil {
			t.Fatal(err)
		}
		con, _ := d.Constraint("promise_requires_approval")
		if con.Result != c.result || con.Pending() != c.pending {
			t.Errorf("%s: %s %q", c.name, con.Result, con.Pending())
		}
		rep := e.Report(a, d)
		if rep.Decision == Allow {
			t.Errorf("%s: allowed", c.name)
		}
		for _, r := range rep.Rules {
			if r.Rule != "s04-statement-that-needs-the-users-word" {
				continue
			}
			want := VerdictFail
			if c.pending != "" {
				want = VerdictNotEvaluable
			}
			if r.Verdict != want || (c.pending != "" && r.Reason != c.pending) {
				t.Errorf("%s: s04 %s %q", c.name, r.Verdict, r.Reason)
			}
		}
	}
}
