package guard

import (
	"encoding/json"
	"testing"
)

// THE PAYEE-FP VECTORS AT ENGINE MAIN (the old-taxonomy ruling,
// 2026-10-09): vectors-6d456fc.json is generate.py rerun at capsule-engine
// 6d456fc. replay and live_target are byte-identical to the 97a2aff file:
// history acts are read under their own taxonomy (rule 2), and Go agrees
// (TestPayeeProfileReplayMatchesTheEngine). Only live_seen_before moves, as
// the ruling says (rule 1, Option A): its two checks are taxonomy 4 records
// decided live, so the engine refuses deal 1's would-allow (reject) and holds
// deal 2's r06 n/a, sealing taxonomy_mismatch evidence.
//
// Go runs the whole check here, taxonomy gate included, and gives every
// field: deal 1's would-allow at taxonomy 4 is refused (reject), as the
// engine's record-taxonomy gate rules; deal 2 is refused where
// nothing else fails and asks where dedupe does (with-profile: escalate).
// r06's not-evaluable evidence is the engine's sealed {constraint_id,
// in_scope, missing_field, taxonomy_mismatch} (capsule-engine#214 group 2).
// vectors-97a2aff.json stays as the historical file the other payee-fp
// tests are pinned to.
func TestPayeeProfileLiveChecksAtEngineMain(t *testing.T) {
	var v payeeFPVectors
	if err := json.Unmarshal(readFile(t, "testdata", "parity", payeeFPDir, "vectors-6d456fc.json"), &v); err != nil {
		t.Fatal(err)
	}
	if v.CapsuleEngineCommit != "6d456fc219473cfe2bfd1d98118f542edf73f5dd" || len(v.LiveSeenBefore) != 3 {
		t.Fatalf("vectors at %s, %d live cases", v.CapsuleEngineCommit, len(v.LiveSeenBefore))
	}
	old, now := payeeFPSections(t, "vectors-97a2aff.json"), payeeFPSections(t, "vectors-6d456fc.json")
	if jcsDigest(t, old.Replay) != jcsDigest(t, now.Replay) || jcsDigest(t, old.LiveTarget) != jcsDigest(t, now.LiveTarget) {
		t.Error("replay or live_target moved between 97a2aff and 6d456fc")
	}
	e, _ := everydayEngine(t)
	outcomes := map[string]string{Allow: "allow", Deny: "deny", Escalate: "escalate"}
	for name, c := range v.LiveSeenBefore {
		first, err := DecodeCheckInputRecord(c.Entries[0])
		if err != nil {
			t.Fatal(err)
		}
		d1, err := e.Check(first, nil)
		if err != nil {
			t.Fatal(err)
		}
		r := RecordFor(first, d1)
		if *r.Decision != c.Deal1Decision {
			t.Errorf("%s: deal 1 Go %s, engine %s", name, *r.Decision, c.Deal1Decision)
		}
		var ev struct {
			MatchedCapsuleID string `json:"matched_capsule_id"`
		}
		_ = json.Unmarshal(c.DedupeEvidence, &ev)
		r.CapsuleID = ev.MatchedCapsuleID
		a, err := DecodeCheckInputRecord(c.Entries[1])
		if err != nil {
			t.Fatal(err)
		}
		d2, err := e.Check(a, []Record{r})
		if err != nil {
			t.Fatal(err)
		}
		sb, _ := d2.Constraint("counterparty_seen_before")
		dd, _ := d2.Constraint("dedupe")
		sbDigest, _, err := sb.EvidenceDigest()
		if err != nil {
			t.Fatal(err)
		}
		ddDigest, _, err := dd.EvidenceDigest()
		if err != nil {
			t.Fatal(err)
		}
		if string(sb.Result) != c.SeenBeforeResult || string(dd.Result) != c.DedupeResult || ddDigest != jcsDigest(t, c.DedupeEvidence) {
			t.Errorf("%s: r06 %s dedupe %s %s; engine r06 %s dedupe %s %s", name, sb.Result, dd.Result, ddDigest,
				c.SeenBeforeResult, c.DedupeResult, c.DedupeEvidence)
		}
		if sbDigest != jcsDigest(t, c.SeenBeforeEvidence) {
			t.Errorf("%s: r06 evidence %s, not the engine's %s", name, sbDigest, c.SeenBeforeEvidence)
		}
		if got := outcomes[d2.Outcome]; got != c.Outcome {
			t.Errorf("%s: outcome Go %s engine %s", name, got, c.Outcome)
		}
	}
}

// payeeFPSectionsJSON are a payee-fp vector file's replay and live_target
// sections, as written.
type payeeFPSectionsJSON struct {
	Replay     json.RawMessage `json:"replay"`
	LiveTarget json.RawMessage `json:"live_target"`
}

func payeeFPSections(t *testing.T, file string) payeeFPSectionsJSON {
	t.Helper()
	var s payeeFPSectionsJSON
	if err := json.Unmarshal(readFile(t, "testdata", "parity", payeeFPDir, file), &s); err != nil {
		t.Fatal(err)
	}
	if len(s.Replay) == 0 || len(s.LiveTarget) == 0 {
		t.Fatalf("%s has no replay or live_target", file)
	}
	return s
}
