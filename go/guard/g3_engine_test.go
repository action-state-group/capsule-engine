package guard

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// THE G3 VECTORS AGAINST THE ENGINE (capsule-engine #138, a710636c,
// asg/everyday/0.3.6, now frozen; 0.3.7 and the default 0.3.8 cite its caps
// and fold unchanged, TestEveryday037Is036WithThePlatform and
// TestEveryday038Is037WithToldBefore): every step of the 17 sequences, decided by capsule-
// engine's live check path (engine_results.py, engine-results.json) and by
// this port on the same envelope, as the plugin decodes it. Per step: caps'
// result and sealed evidence digest, the decision's outcome, and every
// rule's own result. The consuming plugin's tests hold its card halves
// (go-results.json) to the same file.
func TestG3VectorsMatchTheEngine(t *testing.T) {
	testExecutedActsMatchTheEngine(t, "g3-executed-acts-vectors", 17, 76)
}

// The executed acts the G3 sequences do not reach (executed-acts-036-
// vectors, corpus.json says why each): a history act sealed escalate or
// reject counts at its sealed spend, or holds the window when that cannot be
// read; a cancel or refund among the executed or unchecked acts adds
// nothing, a reversal citing no charge; money in counts 0; a per-action
// failure stands beside an unreadable act, its evidence naming
// window_unreadable_count; and a repeat of an act is dropped in the engine's
// order (oldest first, before the window is read). The same engine run, the
// same comparison.
func TestExecutedActsBeyondG3MatchTheEngine(t *testing.T) {
	testExecutedActsMatchTheEngine(t, "executed-acts-036-vectors", 15, 36)
}

// PROVENANCE.json pins every file of executed-acts-036-vectors by its
// sha256, and the engine commit that decided them.
func TestExecutedActs036VectorsAreThePinnedBytes(t *testing.T) {
	prov := decodeJSON[struct {
		Commit string `json:"capsule_engine_commit"`
		Files  map[string]struct {
			SHA256 string `json:"sha256"`
		} `json:"files"`
	}](t, readFile(t, "testdata", "executed-acts-036-vectors", "PROVENANCE.json"))
	if prov.Commit != "a710636c29814ce67e838c673026fd2063d913f8" || len(prov.Files) != 44 {
		t.Fatalf("provenance at %s, %d files", prov.Commit, len(prov.Files))
	}
	for name, f := range prov.Files {
		if sum := sha256.Sum256(readFile(t, "testdata", "executed-acts-036-vectors", name)); hex.EncodeToString(sum[:]) != f.SHA256 {
			t.Errorf("%s is not the pinned bytes", name)
		}
	}
}

func testExecutedActsMatchTheEngine(t *testing.T, set string, sequences, wantSteps int) {
	dir := filepath.Join("testdata", set)
	var py struct {
		Commit    string `json:"capsule_engine_commit"`
		Pack      string `json:"pack"`
		Digest    string `json:"pack_digest"`
		Sequences map[string][]struct {
			Step    int    `json:"step"`
			Outcome string `json:"outcome"`
			Caps    struct {
				Result         string `json:"result"`
				EvidenceDigest string `json:"evidence_digest"`
			} `json:"caps"`
			Rules map[string]string `json:"rules"`
		} `json:"sequences"`
	}
	if err := json.Unmarshal(readFile(t, "testdata", set, "engine-results.json"), &py); err != nil {
		t.Fatal(err)
	}
	e, p := everyday036Engine(t)
	// The engine's live check path (tests/test_executed_acts_window.py
	// _decide_envelope) builds its GuardEngine from the pack's limits with no
	// activated policy, so its caps evidence names no limit_sources.
	e.limitSources = false
	if py.Commit != "a710636c29814ce67e838c673026fd2063d913f8" || py.Pack != p.ID || py.Digest != p.DefinitionDigest {
		t.Fatalf("engine results at %s for %s %s, the pack %s %s", py.Commit, py.Pack, py.Digest, p.ID, p.DefinitionDigest)
	}
	verdict := map[string]string{"pass": VerdictPass, "fail": VerdictFail, "n/a": VerdictNotApplicable}
	steps := 0
	for name, seq := range py.Sequences {
		for _, s := range seq {
			steps++
			raw, err := os.ReadFile(filepath.Join(dir, "inputs", name, fmt.Sprintf("step-%d.input.json", s.Step)))
			if err != nil {
				t.Fatal(err)
			}
			a, h := g3Envelope(t, raw)
			d, err := e.CheckWithHistory(a, h)
			if err != nil {
				t.Fatalf("%s step %d: %v", name, s.Step, err)
			}
			caps := capsOf(t, d)
			digest, _, err := caps.EvidenceDigest()
			if err != nil {
				t.Fatal(err)
			}
			if string(caps.Result) != s.Caps.Result || digest != s.Caps.EvidenceDigest || d.Outcome != s.Outcome {
				t.Errorf("%s step %d: Go caps %s %s, outcome %s; engine caps %s %s, outcome %s", name, s.Step, caps.Result, digest,
					d.Outcome, s.Caps.Result, s.Caps.EvidenceDigest, s.Outcome)
			}
			rules := map[string]RuleResult{}
			for _, r := range e.Report(a, d).Rules {
				rules[r.Rule] = r
			}
			for id, want := range s.Rules {
				r, ok := rules[id]
				// r05's window half is not_evaluable where the engine's
				// rule is n/a in scope (the card states it so).
				if ok && id == "r05-spending-limits" && want == "n/a" && r.Verdict == VerdictNotEvaluable {
					continue
				}
				if !ok || r.Verdict != verdict[want] {
					t.Errorf("%s step %d: rule %s Go %s (%s), engine %s", name, s.Step, id, r.Verdict, r.Reason, want)
				}
			}
		}
	}
	if len(py.Sequences) != sequences || steps != wantSteps {
		t.Fatalf("%d sequences, %d steps; the vectors hold %d and %d", len(py.Sequences), steps, sequences, wantSteps)
	}
}

// g3Envelope decodes an external-check-input/v0 envelope as the plugin's
// check --emit does: the record, and the history acts with their scope.
func g3Envelope(t *testing.T, raw []byte) (Action, History) {
	t.Helper()
	var in struct {
		Record       json.RawMessage   `json:"record"`
		History      []json.RawMessage `json:"history"`
		HistoryScope struct {
			Days     int  `json:"days"`
			Complete bool `json:"complete"`
		} `json:"history_scope"`
		ItemRef json.RawMessage `json:"item_ref"`
	}
	if err := json.Unmarshal(raw, &in); err != nil {
		t.Fatal(err)
	}
	a, err := DecodeCheckInputRecord(in.Record)
	if err != nil {
		t.Fatal(err)
	}
	a = WithItemRef(a, in.ItemRef)
	h := History{Days: in.HistoryScope.Days, Complete: in.HistoryScope.Complete}
	for _, act := range in.History {
		r, err := DecodeHistoryAct(act)
		if err != nil {
			t.Fatal(err)
		}
		h.Acts = append(h.Acts, r)
	}
	return a, h
}

// caps/5.1.0 OVER A LEDGER of capsule-engine's own records of executed acts
// (executed-acts-036-vectors/ledger_cases.py at a710636c,
// ledger-results.json): an executed record flagged spend_unreadable in the
// window leaves caps n/a in scope naming spend_minor, with WindowUnreadable;
// outside the window or under another operator it does not; an executed
// record counts its amount; the engine's copy of a history act
// (live_history disposition) is left out beside its executed record; and a
// per-action failure stands beside an unreadable record. The records go
// through ReadLedger and Check, as a --ledger check reads them; caps'
// result, reason and sealed evidence digest must be the engine's.
func TestExecutedRecordsOnALedgerMatchTheEngine(t *testing.T) {
	var py struct {
		Commit string `json:"capsule_engine_commit"`
		Pack   string `json:"pack"`
		Digest string `json:"pack_digest"`
		Cases  map[string]struct {
			Records []json.RawMessage `json:"records"`
			Action  json.RawMessage   `json:"action"`
			Caps    struct {
				Result         string `json:"result"`
				Reason         string `json:"reason"`
				EvidenceDigest string `json:"evidence_digest"`
			} `json:"caps"`
		} `json:"cases"`
	}
	if err := json.Unmarshal(readFile(t, "testdata", "executed-acts-036-vectors", "ledger-results.json"), &py); err != nil {
		t.Fatal(err)
	}
	e, p := everyday036Engine(t)
	e.limitSources = false
	if py.Commit != "a710636c29814ce67e838c673026fd2063d913f8" || py.Pack != p.ID || py.Digest != p.DefinitionDigest || len(py.Cases) != 6 {
		t.Fatalf("ledger results at %s for %s %s, %d cases", py.Commit, py.Pack, py.Digest, len(py.Cases))
	}
	for name, c := range py.Cases {
		var lines bytes.Buffer
		for _, r := range c.Records {
			if err := json.Compact(&lines, r); err != nil {
				t.Fatal(err)
			}
			lines.WriteByte('\n')
		}
		ledger, err := ReadLedger(&lines)
		if err != nil {
			t.Fatal(err)
		}
		a, err := DecodeAction(bytes.NewReader(c.Action))
		if err != nil {
			t.Fatal(err)
		}
		d, err := e.Check(a, ledger)
		if err != nil {
			t.Fatal(err)
		}
		caps := capsOf(t, d)
		digest, _, err := caps.EvidenceDigest()
		if err != nil {
			t.Fatal(err)
		}
		if string(caps.Result) != c.Caps.Result || digest != c.Caps.EvidenceDigest {
			t.Errorf("%s: Go caps %s %s; engine %s %s", name, caps.Result, digest, c.Caps.Result, c.Caps.EvidenceDigest)
		}
		if c.Caps.Result == "n/a" && caps.Pending() != c.Caps.Reason {
			t.Errorf("%s: Go reason %q, engine %q", name, caps.Pending(), c.Caps.Reason)
		}
	}
}

// KNOWN DIFFERENCE, as the engine was at a710636c (scope-results.json,
// scope_cases.py): caps.py did not read history_scope.complete, so on the
// plugin's two envelopes declaring an incomplete history the engine's live
// check evaluated the 7-day window, where this port leaves it not evaluable:
// the history may hold fewer acts than were carried out. The engine reads it
// now, and at HEAD both sides hold caps n/a on those envelopes; what still
// differs is the outcome one of them maps it to, pinned in the shared vectors
// (conformance-vectors/guard/v0/KNOWN-DIFFERENCES.json, capsule-engine #214
// group 4). This test keeps the frozen a710636c results as they were.
func TestKnownDifferenceTheEngineCountsAnIncompleteHistory(t *testing.T) {
	var py struct {
		Commit string `json:"capsule_engine_commit"`
		Cases  map[string]struct {
			Verdict string `json:"r05_7d_verdict"`
			Value   *int64 `json:"r05_7d_value"`
		} `json:"cases"`
	}
	if err := json.Unmarshal(readFile(t, "testdata", "executed-acts-036-vectors", "scope-results.json"), &py); err != nil {
		t.Fatal(err)
	}
	e, _ := everyday036Engine(t)
	if len(py.Cases) != 2 {
		t.Fatalf("%d cases", len(py.Cases))
	}
	for name, c := range py.Cases {
		if c.Verdict != "pass" || c.Value == nil {
			t.Errorf("%s: the engine (%s) now gives the window %s: the known difference may be closed; retire this pin and hold "+
				"the engine's window to this port's", name, py.Commit, c.Verdict)
		}
		a, h := g3Envelope(t, readFile(t, "testdata", "emit", name+".json"))
		if h.Complete {
			t.Fatalf("%s: the envelope declares a complete history", name)
		}
		d, err := e.CheckWithHistory(a, h)
		if err != nil {
			t.Fatal(err)
		}
		if caps := capsOf(t, d); caps.Result != NotApplicable || !strings.HasPrefix(caps.Pending(), "the history supplied is incomplete") {
			t.Errorf("%s: this port's caps %s %q, want not evaluable on the incomplete history", name, caps.Result, caps.Pending())
		}
	}
}
