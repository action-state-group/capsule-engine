package guard

import (
	"encoding/json"
	"slices"
	"testing"
)

// a-commit-1900 of capsulectl 65f54e5's two-thread sale, with its record
// entry and top level edited by edit.
func commitInput(t *testing.T, edit func(env, record map[string]json.RawMessage)) []byte {
	t.Helper()
	var env, record map[string]json.RawMessage
	if err := json.Unmarshal(readFile(t, liveHistoryVectors, "engine-0ab20965", "check-inputs", "a-commit-1900.json"), &env); err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(env["record"], &record); err != nil {
		t.Fatal(err)
	}
	edit(env, record)
	b, err := json.Marshal(record)
	if err != nil {
		t.Fatal(err)
	}
	env["record"] = b
	if b, err = json.Marshal(env); err != nil {
		t.Fatal(err)
	}
	return b
}

// capsule-engine #136 (AMENDMENT 7): the record entry's proposal_at, the
// accepted offer record's sealed at, is the proposal's time when it is a
// string, which offer_expiry reads; any other value is named in the ignored
// inputs and never read. capsulectl 65f54e5 sends it on a seller's commit.
func TestARecordEntrysProposalAtIsRead(t *testing.T) {
	a, _, err := DecodeCheckInput(commitInput(t, func(_, _ map[string]json.RawMessage) {}))
	if err != nil {
		t.Fatal(err)
	}
	if a.ProposalAt == nil || *a.ProposalAt != "2026-10-10T00:41:06Z" || !slices.Contains(a.present, proposalAtInput) {
		t.Fatalf("proposal_at %v, present %v", a.ProposalAt, a.present)
	}
	e, _ := seller013Engine(t)
	if c := checkOfferExpiry(a, e.offerExpiry); c.Result != Pass {
		t.Errorf("offer_expiry %s", c.Result)
	}
	for _, v := range []string{`1760028866`, `{"at":"2026-10-10T00:41:07Z"}`, `true`} {
		a, _, err := DecodeCheckInput(commitInput(t, func(_, r map[string]json.RawMessage) { r[proposalAtInput] = json.RawMessage(v) }))
		if err != nil {
			t.Fatal(err)
		}
		if a.ProposalAt != nil || !slices.Contains(a.ignoredInputs, proposalAtInput) {
			t.Errorf("proposal_at %s: read %v, ignored %v", v, a.ProposalAt, a.ignoredInputs)
		}
	}
	for _, edit := range []func(_, r map[string]json.RawMessage){
		func(_, r map[string]json.RawMessage) { delete(r, proposalAtInput) },
		func(_, r map[string]json.RawMessage) { r[proposalAtInput] = json.RawMessage(`null`) },
	} {
		a, _, err := DecodeCheckInput(commitInput(t, edit))
		if err != nil {
			t.Fatal(err)
		}
		if a.ProposalAt != nil || slices.Contains(a.ignoredInputs, proposalAtInput) {
			t.Errorf("no proposal_at: read %v, ignored %v", a.ProposalAt, a.ignoredInputs)
		}
	}
}

// capsule-engine #136 (AMENDMENT 6a): the envelope's top-level item_ref is
// the record's; with none, the record entry's own is read. A top-level one
// out of shape is named in the ignored inputs, and the entry's is then not
// read.
func TestTheRecordEntrysOwnItemRefStandsInForATopLevelOne(t *testing.T) {
	const top, own = "6695bf111d75974c476d5b02ce0fba91710f5e20235ee46fd2492c023c911360",
		"1111111111111111111111111111111111111111111111111111111111111111"
	for _, c := range []struct {
		name    string
		edit    func(env, record map[string]json.RawMessage)
		want    string
		ignored bool
	}{
		{"top level only", func(_, _ map[string]json.RawMessage) {}, top, false},
		{"both: the top level's", func(_, r map[string]json.RawMessage) { r[itemRefInput] = json.RawMessage(`"` + own + `"`) }, top, false},
		{"entry only", func(e, r map[string]json.RawMessage) {
			delete(e, itemRefInput)
			r[itemRefInput] = json.RawMessage(`"` + own + `"`)
		}, own, false},
		{"top level null: the entry's", func(e, r map[string]json.RawMessage) {
			e[itemRefInput] = json.RawMessage(`null`)
			r[itemRefInput] = json.RawMessage(`"` + own + `"`)
		}, own, false},
		{"top level out of shape", func(e, r map[string]json.RawMessage) {
			e[itemRefInput] = json.RawMessage(`"AB"`)
			r[itemRefInput] = json.RawMessage(`"` + own + `"`)
		}, "", true},
		{"entry out of shape", func(e, r map[string]json.RawMessage) {
			delete(e, itemRefInput)
			r[itemRefInput] = json.RawMessage(`7`)
		}, "", true},
		{"neither", func(e, _ map[string]json.RawMessage) { delete(e, itemRefInput) }, "", false},
	} {
		a, _, err := DecodeCheckInput(commitInput(t, c.edit))
		if err != nil {
			t.Fatalf("%s: %v", c.name, err)
		}
		got := ""
		if a.ItemRef != nil {
			got = *a.ItemRef
		}
		if got != c.want || slices.Contains(a.ignoredInputs, itemRefInput) != c.ignored {
			t.Errorf("%s: item_ref %q, ignored %v", c.name, got, a.ignoredInputs)
		}
	}
}

// A history entry's item_ref not in its agreed shape (a null included)
// leaves its sale unknown, so single_commitment refuses (n/a history, fails
// closed); the caps window reads only the declared scope, as before.
func TestAHistoryItemRefOutOfShapeLeavesTheSaleUnknown(t *testing.T) {
	for _, v := range []string{`"AB"`, `null`, `7`} {
		raw := commitInput(t, func(e, _ map[string]json.RawMessage) {
			var entries []map[string]json.RawMessage
			if err := json.Unmarshal(e["history"], &entries); err != nil {
				t.Fatal(err)
			}
			entries[0][itemRefInput] = json.RawMessage(v)
			b, err := json.Marshal(entries)
			if err != nil {
				t.Fatal(err)
			}
			e["history"] = b
		})
		a, h, err := DecodeCheckInput(raw)
		if err != nil {
			t.Fatal(err)
		}
		if !h.Complete || !h.saleUnknown() || h.Acts[0].ItemRef != nil {
			t.Fatalf("%s: complete %v, sale unknown %v, item %v", v, h.Complete, h.saleUnknown(), h.Acts[0].ItemRef)
		}
		e, _ := seller013Engine(t)
		d, err := e.CheckWithHistory(a, h)
		if err != nil {
			t.Fatal(err)
		}
		if c, _ := d.Constraint("single_commitment"); c.Result != NotApplicable || c.missing != "history" || d.Outcome != Deny {
			t.Errorf("%s: s11 %s %s, outcome %s", v, c.Result, c.missing, d.Outcome)
		}
	}
}
