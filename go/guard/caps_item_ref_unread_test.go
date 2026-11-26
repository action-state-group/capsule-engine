package guard

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/action-state-group/capsule-engine/go/pack"
)

// capsHistoryIncomplete7d is caps.py history_incomplete_reason for the 7d
// window.
const capsHistoryIncomplete7d = "the history supplied is incomplete (more acts than it holds, or some could not be read), so the 7d total cannot be computed"

// A history that declares itself complete but holds an act whose item_ref
// cannot be read, or that is absent or null, may be missing acts, as
// live_history.py history_ledger reads it, so the caps window is not
// evaluable and the pay is never allowed (capsule-engine #328, ops u503). A
// per-action limit that fails still fails first, as caps.py decides; an
// empty history and a readable item_ref stay complete. The shared vectors
// (item-ref-unread-vectors.json) pin each case to Python's decision.
func TestCapsReadsAnUnreadableHistoryAsIncomplete(t *testing.T) {
	p, err := pack.LoadEveryday0310()
	if err != nil {
		t.Fatal(err)
	}
	e, err := NewEngine(p)
	if err != nil {
		t.Fatal(err)
	}
	cases := []struct {
		name    string
		result  Result
		pending string
	}{
		{"item-ref-null", NotApplicable, capsHistoryIncomplete7d},
		{"item-ref-not-hex", NotApplicable, capsHistoryIncomplete7d},
		// Incomplete is named before a scope too short for the window.
		{"item-ref-null-days-short", NotApplicable, capsHistoryIncomplete7d},
		{"history-null", NotApplicable, capsHistoryIncomplete7d},
		{"history-absent", NotApplicable, capsHistoryIncomplete7d},
		{"item-ref-null-per-action-over", Fail, ""},
		{"item-ref-readable", Pass, ""},
		{"history-empty", Pass, ""},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			raw, err := os.ReadFile(filepath.Join("testdata", "item-ref-unread-vectors", c.name+".json"))
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
			caps, ok := d.Constraint("caps")
			if !ok {
				t.Fatal("no caps constraint")
			}
			if caps.Result != c.result || caps.Pending() != c.pending {
				t.Errorf("caps %s %q, want %s %q", caps.Result, caps.Pending(), c.result, c.pending)
			}
			if c.result != Pass && d.Outcome == Allow {
				t.Errorf("outcome %s on caps %s", d.Outcome, caps.Result)
			}
		})
	}
}
