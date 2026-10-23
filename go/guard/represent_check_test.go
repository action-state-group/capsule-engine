package guard

import (
	"encoding/json"
	"maps"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"github.com/action-state-group/capsule-engine/go/canon"
)

// representCheckVectors are capsulectl v0.1.0-rc20's represent checks on a
// seller's sale whose task authority orders condition, with five live-only
// edits of one whose representing cannot be read, and the engine's live and
// replay decisions of them under asg/seller/0.1.4 at capsule-engine 0c93a58
// (#205; PROVENANCE.json). expected_promise.json holds s04's and s10's
// checks' result and evidence.
var representCheckVectors = filepath.Join("testdata", "represent-check-vectors")

// malformedRepresenting are the edited check inputs: a represent check whose
// class cannot be read.
var malformedRepresenting = []string{"class-empty", "class-missing", "class-number", "no-representing", "representing-list"}

type promiseCell struct {
	Result   string          `json:"result"`
	Reason   string          `json:"reason"`
	Evidence json.RawMessage `json:"evidence"`
}

// CROSS-IMPLEMENTATION: s04's and s10's checks on every represent check, live
// and on the sale bundle's replay, with NO known difference from the engine
// in result and evidence (the reasons are this plugin's own wording). The
// bridge carries representing.class, so the checks decide instead of
// reading a missing class as not applicable: an ordered or plain class
// passes, authenticity fails s10, a class that needs the user's word fails
// s04, and a represent check whose class cannot be read fails both as not
// recognised. Replayed, each check is decided with the sale's task
// authority, bound by the digest it cites.
func TestRepresentCheckPromiseAgreesWithTheEngine(t *testing.T) {
	want := decodeJSON[struct {
		Live   map[string]map[string]promiseCell `json:"live"`
		Replay map[string]map[string]promiseCell `json:"replay"`
	}](t, readFile(t, representCheckVectors, "expected_promise.json"))
	e, _ := seller014Engine(t)
	sale := readSaleIn(t, representCheckVectors)
	var diffs []string
	compare := func(at string, d Decision, cells map[string]promiseCell) {
		for _, id := range slices.Sorted(maps.Keys(cells)) {
			c, ok := d.Constraint(id)
			if !ok {
				t.Fatalf("%s: no %s", at, id)
			}
			digest, _, err := c.EvidenceDigest()
			if err != nil {
				t.Fatal(err)
			}
			differ(&diffs, at, id, string(c.Result)+" "+digest, cells[id].Result+" "+jsonDigest(t, cells[id].Evidence))
		}
	}
	for _, name := range slices.Sorted(maps.Keys(want.Live)) {
		input := "check-inputs/" + name + ".json"
		a, h, err := DecodeCheckInput(readFile(t, representCheckVectors, input))
		if err != nil {
			t.Fatalf("%s: %v", name, err)
		}
		malformed := slices.ContainsFunc(malformedRepresenting, func(s string) bool { return strings.HasSuffix(name, s) })
		if got := strOrNone(a.RepresentationClass); malformed != (got == malformedRepresentationClass) {
			t.Errorf("%s: representation_class %s", name, got)
		}
		d, err := e.CheckWithHistory(a, h)
		if err != nil {
			t.Fatal(err)
		}
		compare("live "+name, d, want.Live[name])
	}
	for _, name := range slices.Sorted(maps.Keys(want.Replay)) {
		id := capsuleIn(t, representCheckVectors, "check-inputs/"+name+".json")
		a, h, err := sale.Check(id)
		if err != nil {
			t.Fatalf("%s: %v", name, err)
		}
		if h.TaskAuthorityRecord == nil {
			t.Errorf("replay %s: the sale's task authority is not bound", name)
		}
		d, err := e.CheckSale(a, replayLedger(t, e, sale, id), h)
		if err != nil {
			t.Fatal(err)
		}
		compare("replay "+name, d, want.Replay[name])
	}
	if len(want.Live) != 10 || len(want.Replay) != 5 {
		t.Errorf("%d live and %d replayed checks", len(want.Live), len(want.Replay))
	}
	if len(diffs) > 0 {
		slices.Sort(diffs)
		t.Errorf("Go and the engine differ at\n%s", strings.Join(diffs, "\n"))
	}
}

// A represent check's class is carried only from representing.class as a
// non-empty string; a check of another action carries none when its class
// cannot be read, and the class it states when it can.
func TestRepresentationClassOf(t *testing.T) {
	for name, tc := range map[string]struct {
		body string
		want string
	}{
		"represent, class":            {`{"action":"represent","representing":{"class":"warranty"}}`, `"warranty"`},
		"represent, class null":       {`{"action":"represent","representing":{"class":null}}`, `"malformed_representing"`},
		"represent, representing str": {`{"action":"represent","representing":"warranty"}`, `"malformed_representing"`},
		"represent, class false":      {`{"action":"represent","representing":{"class":false}}`, `"malformed_representing"`},
		"offer, class":                {`{"action":"offer","representing":{"class":"price"}}`, `"price"`},
		"offer, no representing":      {`{"action":"offer"}`, ``},
		"offer, class empty":          {`{"action":"offer","representing":{"class":""}}`, ``},
		"action not a string":         {`{"action":["represent"]}`, ``},
	} {
		var body map[string]json.RawMessage
		if err := json.Unmarshal([]byte(tc.body), &body); err != nil {
			t.Fatal(err)
		}
		got, ok := representationClassOf(body)
		if string(got) != tc.want || ok != (tc.want != "") {
			t.Errorf("%s: %s %v, want %s", name, got, ok, tc.want)
		}
	}
}

// BundleReplay gives the disclosed task-authority/v0 record whose digest
// the action cites, and nothing for a capsule that is not a bundle, an
// action citing none, a digest no record has, or a record of another type
// with that digest.
func TestBundleReplayTaskAuthority(t *testing.T) {
	authority := json.RawMessage(`{"type":"task-authority/v0","body":{"outcome_id":"x/1.0.0"}}`)
	other := json.RawMessage(`{"type":"proposed-action/v0","body":{}}`)
	digest := func(raw json.RawMessage) *string {
		v, err := canon.FromJSON(raw)
		if err != nil {
			t.Fatal(err)
		}
		d, err := canon.Digest(v)
		if err != nil {
			t.Fatal(err)
		}
		return &d
	}
	bundle, err := json.Marshal(map[string]any{"records": []any{}, "disclosures": map[string]any{
		"a": map[string]json.RawMessage{"agent_input": authority}, "b": map[string]json.RawMessage{"agent_input": other}}})
	if err != nil {
		t.Fatal(err)
	}
	for name, tc := range map[string]struct {
		raw  []byte
		ref  *string
		want string
	}{
		"bound":            {bundle, digest(authority), string(authority)},
		"no ref":           {bundle, nil, ""},
		"another digest":   {bundle, new(strings.Repeat("0", 64)), ""},
		"not that type":    {bundle, digest(other), ""},
		"not a bundle":     {authority, digest(authority), ""},
		"records not list": {[]byte(`{"records":{},"disclosures":{}}`), digest(authority), ""},
		// The engine reads only the exact member name: a later "Disclosures"
		// leaves "disclosures" as it is.
		"case alias after": {append(bundle[:len(bundle)-1:len(bundle)-1], []byte(`,"Disclosures":{}}`)...), digest(authority), string(authority)},
		"case alias only":  {[]byte(`{"records":[],"Disclosures":{"a":{"agent_input":` + string(authority) + `}}}`), digest(authority), ""},
	} {
		if got := BundleReplay(tc.raw, Action{TaskAuthorityRef: tc.ref}).taskAuthority; string(got) != tc.want {
			t.Errorf("%s: %s, want %s", name, got, tc.want)
		}
	}
}

// A history act's class of statement is read as a check's is: the engine's
// replay reads a history act through the same bridge, so an accepted act
// stating one counts for s05. capsulectl seals none on an act.
func TestAHistoryActCarriesTheClassItsBodyStates(t *testing.T) {
	raw := realFyiAct(t)
	for name, tc := range map[string]struct {
		edits bodyEdits
		want  string
	}{
		"none":            {bodyEdits{}, "None"},
		"a class":         {bodyEdits{"representing": json.RawMessage(`{"class":"condition"}`)}, "condition"},
		"an empty class":  {bodyEdits{"representing": json.RawMessage(`{"class":""}`)}, "None"},
		"a represent act": {bodyEdits{"action": json.RawMessage(`"represent"`), "representing": json.RawMessage(`[]`)}, malformedRepresentationClass},
	} {
		r, err := DecodeHistoryAct(withBody(t, raw, tc.edits))
		if err != nil {
			t.Fatalf("%s: %v", name, err)
		}
		if got := strOrNone(r.RepresentationClass); got != tc.want {
			t.Errorf("%s: class %s, want %s", name, got, tc.want)
		}
	}
}
