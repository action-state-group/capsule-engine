package guard

import (
	"encoding/json"
	"fmt"
	"slices"
	"sort"
	"strings"
	"testing"

	"github.com/action-state-group/capsule-engine/go/pack"
)

type a4Corpus struct {
	ReplayCases map[string][]struct {
		Capsule    map[string]json.RawMessage `json:"capsule"`
		AgentInput json.RawMessage            `json:"agent_input"`
	} `json:"replay_cases"`
	LiveCases map[string]json.RawMessage `json:"live_cases"`
}

type a4Row struct {
	DealID         *string `json:"deal_id"`
	DealIDConflict *struct {
		Fields []string `json:"fields"`
	} `json:"deal_id_conflict"`
	Outcome            string   `json:"outcome"`
	Verdict            string   `json:"verdict"`
	Decision           string   `json:"decision"`
	FailingConstraints []string `json:"failing_constraints"`
	DedupeRepeat       *string  `json:"dedupe_repeat"`
	FailingRules       []string `json:"failing_rules"`
}

type a4Decisions struct {
	CapsuleEngineCommit  string                        `json:"capsule_engine_commit"`
	PackDefinitionDigest string                        `json:"pack_definition_digest"`
	Replay               map[string]map[string][]a4Row `json:"replay"`
	Live                 map[string]a4Row              `json:"live"`
}

// a4Got is what the plugin decides for one record, in the vectors' terms.
type a4Got struct {
	DealID             string
	Conflict           bool
	Verdict            string
	FailingConstraints string
	FailingRules       string
}

// AMENDMENT 4's engine half (capsule-engine #127, 763f5a9): every replay
// case under the everyday pack, the plugin's own configuration. Each record
// is read as check --emit reads it (DecodeCheckInputRecord), checked on the
// ledger of the engine's own earlier decisions in the case, and reported as
// the plugin reports it; the engine's deal_id, conflict marker, verdict,
// failing constraints and failing rules are required.
//
// python-decisions.json is the engine side's, under asg/everyday/0.3.4, held to the
// frozen 0.3.4. python-decisions-0.3.5.json is the same generator over the
// same corpus at capsule-engine c878edf0 (#135's merge), under 0.3.5, now
// frozen: a typed check that seals no payee fails r06 there (no payee
// named). python-decisions-0.3.6.json is the same at a710636c (#138's
// merge), under 0.3.6 (now frozen): every decision equal to 0.3.5's.
func TestDealIDA4EngineVectors(t *testing.T) {
	for file, load := range map[string]func() (pack.Pack, error){
		"python-decisions.json":       pack.LoadEveryday034,
		"python-decisions-0.3.5.json": pack.LoadEveryday035,
		"python-decisions-0.3.6.json": pack.LoadEveryday036,
	} {
		t.Run(file, func(t *testing.T) { testDealIDA4EngineVectors(t, file, load) })
	}
}

func testDealIDA4EngineVectors(t *testing.T, file string, load func() (pack.Pack, error)) {
	corpus := decodeJSON[a4Corpus](t, readFile(t, "testdata", "deal-id-a4-vectors", "corpus.json"))
	py := decodeJSON[a4Decisions](t, readFile(t, "testdata", "deal-id-a4-vectors", file))
	p, err := load()
	if err != nil {
		t.Fatal(err)
	}
	if py.PackDefinitionDigest != p.DefinitionDigest {
		t.Fatalf("vectors for %s, the pack digests to %s", py.PackDefinitionDigest, p.DefinitionDigest)
	}
	e, err := NewEngine(p)
	if err != nil {
		t.Fatal(err)
	}
	var disagree []string
	names := make([]string, 0, len(corpus.ReplayCases))
	for name := range corpus.ReplayCases {
		names = append(names, name)
	}
	sort.Strings(names)
	for _, name := range names {
		var ledger []Record
		for i, step := range corpus.ReplayCases[name] {
			want := py.Replay[name]["everyday"][i]
			entry := map[string]json.RawMessage{}
			for k, v := range step.Capsule {
				entry[k] = v
			}
			entry["agent_input"] = step.AgentInput
			raw, err := json.Marshal(entry)
			if err != nil {
				t.Fatal(err)
			}
			a, err := DecodeCheckInputRecord(raw)
			if err != nil {
				t.Fatalf("%s #%d: %v", name, i+1, err)
			}
			got := a4Got{}
			if a.DealID != nil {
				got.DealID = *a.DealID
			}
			var rep CheckReport
			var d Decision
			if len(a.Unevaluable()) > 0 {
				rep = e.ReportUnevaluable(a)
			} else {
				if d, err = e.Check(a, MemoryHistory(ledger)); err != nil {
					t.Fatal(err)
				}
				rep = e.Report(a, d)
			}
			got.Verdict = rep.Decision
			// The marker is a row of its own, beside the rules' rows.
			got.Conflict = slices.ContainsFunc(rep.Rules, func(r RuleResult) bool {
				return r.Rule == DealIDConflictRow && r.Verdict == VerdictNotEvaluable && r.Reason == DealIDConflict
			})
			var fc, fr []string
			for _, c := range d.Constraints {
				if c.Result == Fail {
					fc = append(fc, c.ID)
				}
			}
			for _, r := range rep.Rules {
				if r.Verdict == VerdictFail && r.Rule != DealIDConflictRow {
					fr = append(fr, r.Rule)
				}
			}
			slices.Sort(fc)
			slices.Sort(fr)
			got.FailingConstraints, got.FailingRules = strings.Join(fc, ","), strings.Join(fr, ",")
			// The plugin seals nothing: the engine's would-allow refused as
			// deny with verdict not_evaluable is the plugin's not_evaluable.
			engine := a4Got{Conflict: want.DealIDConflict != nil && slices.Equal(want.DealIDConflict.Fields, []string{"x-deal-v0.deal_id", "chain_id"}), Verdict: want.Verdict,
				FailingConstraints: strings.Join(want.FailingConstraints, ","), FailingRules: strings.Join(want.FailingRules, ",")}
			if want.DealID != nil {
				engine.DealID = *want.DealID
			}
			if got != engine {
				disagree = append(disagree, fmt.Sprintf("%s #%d: plugin %+v; engine %+v", name, i+1, got, engine))
			}
			rec := RecordFor(a, Decision{Outcome: want.Outcome})
			rec.DryRun = ptr(true)
			ledger = append(ledger, rec)
		}
	}
	for _, d := range disagree {
		t.Error(d)
	}
}

// GO-ONLY (no engine vector exercises it): a record whose x-deal-v0 block
// has no deal_id key names its deal by chain_id, as the engine's _deal_id
// reads it (x-deal-v0.deal_id when the key is present, else chain_id); with
// the key present the block's value is the deal, even an empty one, and an
// unequal chain_id of any type is a conflict.
func TestDealIDKeyPresence(t *testing.T) {
	for name, c := range map[string]struct {
		input    string
		deal     string
		source   string
		conflict bool
	}{
		"block without deal_id, chain_id":  {`{"x-deal-v0":{"seq":1},"chain_id":"deal-a"}`, "deal-a", chainIDMember, false},
		"no block, chain_id":               {`{"chain_id":"deal-a"}`, "deal-a", chainIDMember, false},
		"deal_id only":                     {`{"x-deal-v0":{"deal_id":"deal-a"}}`, "deal-a", dealIDMember, false},
		"both equal":                       {`{"x-deal-v0":{"deal_id":"deal-a"},"chain_id":"deal-a"}`, "deal-a", dealIDMember, false},
		"both different":                   {`{"x-deal-v0":{"deal_id":"deal-a"},"chain_id":"deal-b"}`, "", "", true},
		"empty deal_id, chain_id":          {`{"x-deal-v0":{"deal_id":""},"chain_id":"deal-a"}`, "", "", true},
		"deal_id, null chain_id":           {`{"x-deal-v0":{"deal_id":"deal-a"},"chain_id":null}`, "", "", true},
		"null deal_id, null chain_id":      {`{"x-deal-v0":{"deal_id":null},"chain_id":null}`, "", "", false},
		"empty deal_id alone":              {`{"x-deal-v0":{"deal_id":""}}`, "", "", false},
		"numeric deal_id, string chain_id": {`{"x-deal-v0":{"deal_id":7},"chain_id":"7"}`, "", "", true},
		"nothing":                          {`{"body":{}}`, "", "", false},
	} {
		deal, source, conflict := dealOf(json.RawMessage(c.input))
		if deal != c.deal || source != c.source || conflict != c.conflict {
			t.Errorf("%s: %q %q %v, want %q %q %v", name, deal, source, conflict, c.deal, c.source, c.conflict)
		}
	}
}

// sameJSON is Python's == over decoded JSON: key order and 1 against 1.0
// do not matter; a type does.
func TestSameJSON(t *testing.T) {
	for _, c := range []struct {
		a, b string
		same bool
	}{
		{`"deal-a"`, `"deal-a"`, true}, {`1`, `1.0`, true}, {`{"x":1,"y":2}`, `{"y":2,"x":1}`, true}, {`null`, `null`, true},
		{`"7"`, `7`, false}, {`""`, `null`, false}, {`"deal-a"`, `"deal-b"`, false},
	} {
		if got := sameJSON(json.RawMessage(c.a), json.RawMessage(c.b)); got != c.same {
			t.Errorf("%s == %s: %v", c.a, c.b, got)
		}
	}
}
