package guard

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"slices"
	"testing"

	"github.com/action-state-group/capsule-engine/go/canon"
	"github.com/action-state-group/capsule-engine/go/pack"
)

// A TYPED CHECK IS KEYED ON THE PAYEE ITS BODY SEALS (capsule-engine #133,
// b1c7fb4f). capsulectl moves a check's counterparty into a typed
// proposed-action/v0's body.counterparty (fp_alg hmac-sha256-chain-key); the
// engine's bridge reads it there as it reads an x-deal-v0 check's block, so
// the target is payee-fp:hmac-sha256-chain-key:<hex>. An x-deal-v0 check
// still reads its block only, never a body.counterparty, and a well-formed
// counterparty_profile still replaces either.
//
// Held to the engine side's vectors (testdata/typed-counterparty-vectors) under the
// frozen asg/everyday/0.3.4 (python-decisions.json, b99a0e9), and to the
// same generator's runs at c878edf0 under the frozen 0.3.5
// (python-decisions-0.3.5.json) and at a710636c under 0.3.6 (now frozen)
// (python-decisions-0.3.6.json): per replayed check the target, ignored
// inputs, r06 (result, fold key, missing field), dedupe (result, repeat),
// outcome, verdict, decision, failing constraints and failing rules; per
// live envelope the target and ignored inputs.

const typedCounterpartyDir = "typed-counterparty-vectors"

type typedStep struct {
	Capsule    map[string]json.RawMessage `json:"capsule"`
	AgentInput json.RawMessage            `json:"agent_input"`
}

type typedCorpus struct {
	ReplayCases map[string][]typedStep `json:"replay_cases"`
	LiveCases   map[string]struct {
		Record json.RawMessage `json:"record"`
	} `json:"live_cases"`
}

type typedRow struct {
	Target        *string  `json:"target"`
	IgnoredInputs []string `json:"ignored_inputs"`
	R06           struct {
		Result       string  `json:"result"`
		FoldKey      *string `json:"fold_key"`
		MissingField *string `json:"missing_field"`
	} `json:"r06"`
	Dedupe struct {
		Result string  `json:"result"`
		Repeat *string `json:"repeat"`
	} `json:"dedupe"`
	Outcome            string   `json:"outcome"`
	Verdict            string   `json:"verdict"`
	Decision           string   `json:"decision"`
	FailingConstraints []string `json:"failing_constraints"`
	FailingRules       []string `json:"failing_rules"`
}

type typedDecisions struct {
	CapsuleEngineCommit  string                `json:"capsule_engine_commit"`
	PackID               string                `json:"pack_id"`
	PackDefinitionDigest string                `json:"pack_definition_digest"`
	CorpusSHA256         string                `json:"corpus_sha256"`
	Replay               map[string][]typedRow `json:"replay"`
	Live                 map[string]struct {
		Target        *string  `json:"target"`
		IgnoredInputs []string `json:"ignored_inputs"`
	} `json:"live"`
}

// evidenceMembers is a constraint's evidence as JSON members.
func evidenceMembers(t *testing.T, c Constraint) map[string]json.RawMessage {
	t.Helper()
	s, err := canon.JCS(c.evidence)
	if err != nil {
		t.Fatal(err)
	}
	var m map[string]json.RawMessage
	if err := json.Unmarshal([]byte(s), &m); err != nil {
		t.Fatal(err)
	}
	return m
}

// optString is a JSON string member, or nil when absent or null.
func optString(raw json.RawMessage) *string {
	var s *string
	_ = json.Unmarshal(raw, &s)
	return s
}

// typedRowFor is what the vectors record of Go's decision d on a.
func typedRowFor(t *testing.T, e *Engine, a Action, d Decision) typedRow {
	t.Helper()
	var row typedRow
	row.Target, row.IgnoredInputs = a.Target, append([]string{}, a.ignoredInputs...)
	r06, ok := d.Constraint("counterparty_seen_before")
	dd, ok2 := d.Constraint("dedupe")
	if !ok || !ok2 {
		t.Fatal("no counterparty_seen_before or dedupe")
	}
	ev := evidenceMembers(t, r06)
	var foldKey struct {
		Value *string `json:"value"`
	}
	_ = json.Unmarshal(ev["fold_key"], &foldKey)
	row.R06.Result, row.R06.FoldKey, row.R06.MissingField = string(r06.Result), foldKey.Value, optString(ev["missing_field"])
	row.Dedupe.Result, row.Dedupe.Repeat = string(dd.Result), optString(evidenceMembers(t, dd)["repeat"])
	row.Outcome, row.Verdict = d.Outcome, d.Outcome
	row.Decision = map[string]string{Allow: "accept", Deny: "reject", Escalate: "needs_input"}[d.Outcome]
	row.FailingConstraints, row.FailingRules = []string{}, []string{}
	for _, c := range d.Constraints {
		if c.Result == Fail {
			row.FailingConstraints = append(row.FailingConstraints, c.ID)
		}
	}
	for _, r := range e.Report(a, d).Rules {
		if r.Verdict == VerdictFail {
			row.FailingRules = append(row.FailingRules, r.Rule)
		}
	}
	slices.Sort(row.FailingConstraints)
	slices.Sort(row.FailingRules)
	return row
}

// shownRows is rows as JSON, in the vectors' member order.
func shownRows(t *testing.T, rows []typedRow) string {
	t.Helper()
	b, err := json.Marshal(rows)
	if err != nil {
		t.Fatal(err)
	}
	return string(b)
}

func TestTypedCheckIsKeyedOnTheSealedPayee(t *testing.T) {
	var prov struct {
		Files map[string]struct {
			SHA256 string `json:"sha256"`
		} `json:"files"`
	}
	if err := json.Unmarshal(readFile(t, "testdata", typedCounterpartyDir, "PROVENANCE.json"), &prov); err != nil {
		t.Fatal(err)
	}
	if len(prov.Files) != 6 {
		t.Fatalf("provenance names %d files", len(prov.Files))
	}
	for name, f := range prov.Files {
		if sum := sha256.Sum256(readFile(t, "testdata", typedCounterpartyDir, name)); hex.EncodeToString(sum[:]) != f.SHA256 {
			t.Fatalf("%s is not the vendored bytes", name)
		}
	}
	corpusBytes := readFile(t, "testdata", typedCounterpartyDir, "corpus.json")
	corpus := decodeJSON[typedCorpus](t, corpusBytes)
	corpusSum := sha256.Sum256(corpusBytes)
	for file, load := range map[string]func() (pack.Pack, error){
		"python-decisions.json":       pack.LoadEveryday034,
		"python-decisions-0.3.5.json": pack.LoadEveryday035,
		"python-decisions-0.3.6.json": pack.LoadEveryday036,
	} {
		py := decodeJSON[typedDecisions](t, readFile(t, "testdata", typedCounterpartyDir, file))
		p, err := load()
		if err != nil {
			t.Fatal(err)
		}
		if py.PackID != p.ID || py.PackDefinitionDigest != p.DefinitionDigest || py.CorpusSHA256 != hex.EncodeToString(corpusSum[:]) {
			t.Fatalf("%s: under %s %s, vendored %s %s", file, py.PackID, py.PackDefinitionDigest, p.ID, p.DefinitionDigest)
		}
		e, err := NewEngine(p)
		if err != nil {
			t.Fatal(err)
		}
		if len(corpus.ReplayCases) != 7 || len(py.Replay) != 7 || len(corpus.LiveCases) != 4 || len(py.Live) != 4 {
			t.Fatalf("%s: %d/%d replay, %d/%d live", file, len(corpus.ReplayCases), len(py.Replay), len(corpus.LiveCases), len(py.Live))
		}
		for name, steps := range corpus.ReplayCases {
			records := make([]json.RawMessage, len(steps))
			for i, s := range steps {
				records[i] = s.AgentInput
			}
			blocks := companionBlocks(t, records)
			var ledger []Record
			var got []typedRow
			for i, s := range steps {
				capsule, err := json.Marshal(s.Capsule)
				if err != nil {
					t.Fatal(err)
				}
				entry := withMember(t, capsule, "agent_input", s.AgentInput)
				if block, ok := blocks[jcsDigest(t, s.AgentInput)]; ok {
					entry = withMember(t, entry, profileInput, block)
				}
				a, err := DecodeCheckInputRecord(entry)
				if err != nil {
					t.Fatalf("%s %s #%d: %v", file, name, i+1, err)
				}
				if a.statesNoAct {
					// The companion: the replay gives it no decision.
					continue
				}
				d, err := e.Check(a, ledger)
				if err != nil {
					t.Fatal(err)
				}
				got = append(got, typedRowFor(t, e, a, d))
				r := RecordFor(a, d)
				r.DryRun = ptr(true)
				ledger = append(ledger, r)
			}
			if g, w := shownRows(t, got), shownRows(t, py.Replay[name]); g != w {
				t.Errorf("%s %s:\nGo     %s\nengine %s", file, name, g, w)
			}
		}
		for name, c := range corpus.LiveCases {
			a, err := DecodeCheckInputRecord(c.Record)
			if err != nil {
				t.Fatalf("%s live %s: %v", file, name, err)
			}
			want := py.Live[name]
			if !eqPtr(a.Target, want.Target) || !slices.Equal(append([]string{}, a.ignoredInputs...), want.IgnoredInputs) {
				t.Errorf("%s live %s: target %s ignored %v; engine %s %v", file, name, shown(a.Target), a.ignoredInputs, shown(want.Target), want.IgnoredInputs)
			}
		}
	}
}

// GO-ONLY (no engine vector exercises it): which block a record's target is
// read from, in _checked_body's order. An x-deal-v0 check reads its block
// even when it also states type proposed-action/v0; a typed record reads its
// body, whatever x-deal-v0 block it carries that is not a check's.
func TestTheSealedCounterpartyFollowsTheBridgeOrder(t *testing.T) {
	block, bodyCP := json.RawMessage(`{"fp_alg":"hmac-sha256-deal-key","ids":{"payee":"b"}}`), json.RawMessage(`{"fp_alg":"hmac-sha256-chain-key","ids":{"payee":"a"}}`)
	body := map[string]json.RawMessage{"counterparty": bodyCP}
	for name, c := range map[string]struct {
		agentInput string
		xdeal      map[string]json.RawMessage
		want       json.RawMessage
		source     string
	}{
		"x-deal-v0 check":                      {`{}`, map[string]json.RawMessage{"record_type": json.RawMessage(`"check"`), "counterparty": block}, block, counterpartyBlock},
		"x-deal-v0 check, typed too":           {`{"type":"proposed-action/v0"}`, map[string]json.RawMessage{"record_type": json.RawMessage(`"check"`), "counterparty": block}, block, counterpartyBlock},
		"typed, no block":                      {`{"type":"proposed-action/v0"}`, nil, bodyCP, typedCounterparty},
		"typed, a block that is not a check's": {`{"type":"proposed-action/v0"}`, map[string]json.RawMessage{"record_type": json.RawMessage(`"note"`), "counterparty": block}, bodyCP, typedCounterparty},
		"another typed record":                 {`{"type":"deal-report/v0"}`, nil, nil, counterpartyBlock},
	} {
		got, source := sealedCounterparty(json.RawMessage(c.agentInput), body, c.xdeal)
		if string(got) != string(c.want) || source != c.source {
			t.Errorf("%s: %s from %s, want %s from %s", name, got, source, c.want, c.source)
		}
	}
}
