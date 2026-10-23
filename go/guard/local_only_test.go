package guard

import (
	"encoding/json"
	"strings"
	"testing"
)

// atTaxonomy is a check-input record entry whose body states the given
// action taxonomy, so the class-keyed checks run on it.
func atTaxonomy(t *testing.T, entry json.RawMessage, version string) json.RawMessage {
	t.Helper()
	var m map[string]json.RawMessage
	if err := json.Unmarshal(entry, &m); err != nil {
		t.Fatal(err)
	}
	input := m["agent_input"]
	var in map[string]json.RawMessage
	if err := json.Unmarshal(input, &in); err != nil {
		t.Fatal(err)
	}
	in["body"] = withMember(t, in["body"], "taxonomy_version", json.RawMessage(`"`+version+`"`))
	raw, err := json.Marshal(in)
	if err != nil {
		t.Fatal(err)
	}
	entry = withMember(t, entry, "agent_input", raw)
	// The header binds the body it carries: re-bind it to the edited one.
	digest, ok := rawDigest(raw)
	if !ok {
		t.Fatal("the edited agent_input does not digest")
	}
	var att map[string]json.RawMessage
	if err := json.Unmarshal(m["model_attestation"], &att); err != nil {
		t.Fatal(err)
	}
	att["compute_attestation"] = withMember(t, att["compute_attestation"], "agent_input_digest", json.RawMessage(`"`+digest+`"`))
	rawAtt, err := json.Marshal(att)
	if err != nil {
		t.Fatal(err)
	}
	return withMember(t, entry, "model_attestation", rawAtt)
}

// The profile-keyed payee target is matched locally and never leaves in a
// reason or a report value (AGENTS.md: local-only payload fields). Each
// profile case is decided in full at the engine's action taxonomy, as a
// live check decides it, so the merchant-reading checks run rather than
// stop at a taxonomy mismatch; every constraint's reason and the whole
// per-rule report are then searched for the prefix.
func TestAProfilePayeeNeverReachesAReasonOrTheReport(t *testing.T) {
	v := payeeFP(t)
	e, _ := everydayEngine(t)
	for _, name := range []string{"with-profile", "without-profile", "bad-profile"} {
		c := v.LiveSeenBefore[name]
		if len(c.Entries) != 2 {
			t.Fatalf("%s: not two entries", name)
		}
		first, err := DecodeCheckInputRecord(atTaxonomy(t, c.Entries[0], e.tax.version))
		if err != nil {
			t.Fatal(err)
		}
		a, err := DecodeCheckInputRecord(atTaxonomy(t, c.Entries[1], e.tax.version))
		if err != nil {
			t.Fatal(err)
		}
		if name == "with-profile" && (a.Target == nil || !strings.HasPrefix(*a.Target, ProfileTargetPrefix)) {
			t.Fatalf("%s: the checked act is not keyed per profile: %v", name, a.Target)
		}
		// With the earlier act, and without it, so the merchant check both
		// passes and fails on the profile target.
		for _, ledger := range [][]Record{{RecordFor(first, Decision{Outcome: Allow})}, nil} {
			assertNoProfilePayee(t, e, name, a, ledger)
		}
	}
}

func assertNoProfilePayee(t *testing.T, e *Engine, name string, a Action, ledger []Record) {
	t.Helper()
	d, err := e.Check(a, ledger)
	if err != nil {
		t.Fatal(err)
	}
	ran := 0
	for _, cs := range d.Constraints {
		if (cs.ID == "counterparty_seen_before" || cs.ID == "caps") && cs.Result != NotApplicable {
			ran++
		}
		if name == "with-profile" && ledger == nil && cs.ID == "counterparty_seen_before" && cs.Result != Fail {
			t.Errorf("%s with no history: counterparty_seen_before %s, want fail on the unseen profile payee", name, cs.Result)
		}
		for _, reason := range []string{cs.failNote, cs.scopeNote} {
			if strings.Contains(reason, ProfileTargetPrefix) {
				t.Errorf("%s: %s's reason names the profile payee: %q", name, cs.ID, reason)
			}
		}
	}
	if ran != 2 {
		t.Errorf("%s: counterparty_seen_before and caps did not both run: %+v", name, d.Constraints)
	}
	report, err := json.Marshal(e.Report(a, d))
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(report), ProfileTargetPrefix) {
		t.Errorf("%s: the report names the profile payee: %s", name, report)
	}
}
