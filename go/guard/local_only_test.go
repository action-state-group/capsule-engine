package guard

import (
	"encoding/json"
	"strings"
	"testing"
)

// The profile-keyed payee target is matched locally and never leaves in a
// reason or a report value (AGENTS.md: local-only payload fields). Each
// profile case is decided in full, as a live check decides it, and every
// constraint's reason and the whole per-rule report are searched for the
// prefix.
func TestAProfilePayeeNeverReachesAReasonOrTheReport(t *testing.T) {
	v := payeeFP(t)
	e, _ := everydayEngine(t)
	for _, name := range []string{"with-profile", "without-profile", "bad-profile"} {
		c := v.LiveSeenBefore[name]
		if len(c.Entries) != 2 {
			t.Fatalf("%s: not two entries", name)
		}
		first, err := DecodeCheckInputRecord(c.Entries[0])
		if err != nil {
			t.Fatal(err)
		}
		a, err := DecodeCheckInputRecord(c.Entries[1])
		if err != nil {
			t.Fatal(err)
		}
		if name == "with-profile" && (a.Target == nil || !strings.HasPrefix(*a.Target, ProfileTargetPrefix)) {
			t.Fatalf("%s: the checked act is not keyed per profile: %v", name, a.Target)
		}
		d, err := e.Check(a, []Record{RecordFor(first, Decision{Outcome: Allow})})
		if err != nil {
			t.Fatal(err)
		}
		for _, cs := range d.Constraints {
			for _, reason := range []string{cs.failNote, cs.scopeNote} {
				if strings.Contains(reason, ProfileTargetPrefix) {
					t.Errorf("%s: %s's reason names the profile payee: %q", name, cs.ID, reason)
				}
			}
		}
		report, err := json.Marshal(e.Report(a, d))
		if err != nil {
			t.Fatal(err)
		}
		if strings.Contains(string(report), ProfileTargetPrefix) {
			t.Errorf("%s: the report names the profile payee: %s", name, report)
		}
	}
}
