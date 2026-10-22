package guard

import (
	"bytes"
	"encoding/json"
	"fmt"
	"testing"

	"github.com/action-state-group/capsule-engine/go/pack"
)

// capsV5Engine is the engine over the frozen asg/everyday/0.3.5, which
// cites caps/5.0.0 over spend.weekly/3.0.0 (capsule-engine #93's merge
// commit on; the shipped pack until 0.3.6).
func capsV5Engine(t *testing.T) *Engine {
	t.Helper()
	p, err := pack.LoadEveryday035()
	if err != nil {
		t.Fatal(err)
	}
	e, err := NewEngine(p)
	if err != nil {
		t.Fatal(err)
	}
	if w, ok := p.Wicket("caps"); !ok || w.ID != "caps/5.0.0" || e.capsFold.id != "spend.weekly/3.0.0" || e.perActionReads != "spend_authorized_minor" {
		t.Fatalf("asg/everyday/0.3.5 does not cite caps/5.0.0 over spend.weekly/3.0.0")
	}
	return e
}

// shippedCapsEngine is the engine over the shipped pack, asg/everyday/0.3.7 (0.3.6's caps):
// caps/5.1.0 over spend.weekly/3.1.0, caps/5.0.0's limits and per-action
// reading over a fold that also counts an executed act.
func shippedCapsEngine(t *testing.T) *Engine {
	t.Helper()
	e, p := everydayEngine(t)
	if w, ok := p.Wicket("caps"); !ok || w.ID != "caps/5.1.0" || e.capsFold.id != "spend.weekly/3.1.0" || e.perActionReads != "spend_authorized_minor" {
		t.Fatalf("the shipped pack does not cite caps/5.1.0 over spend.weekly/3.1.0")
	}
	return e
}

type differentialCorpus struct {
	Fold     string `json:"fold"`
	KeyValue string `json:"key_value"`
	AsOf     string `json:"as_of"`
	Cases    []struct {
		Name    string            `json:"name"`
		Expect  string            `json:"expect"`
		Records []json.RawMessage `json:"records"`
	} `json:"cases"`
}

type pythonTotals struct {
	CapsuleEngineCommit string `json:"capsule_engine_commit"`
	Fold                string `json:"fold"`
	FoldDigest          string `json:"fold_digest"`
	Totals              map[string]struct {
		WeeklySpendMinor int64 `json:"weekly_spend_minor"`
		Reversals        struct {
			LinkedCount   int64 `json:"linked_count"`
			LinkedMinor   int64 `json:"linked_minor"`
			UnlinkedCount int64 `json:"unlinked_count"`
		} `json:"reversals"`
	} `json:"totals"`
}

// THE GATE FOR spend.weekly/3.0.0: over one record corpus, the Go
// fold and capsule-engine's Python fold (python-totals.json, run at the
// commit it names on the vendored definition) compute the same
// weekly_spend_minor and the same reversal summary, case by case: a linked
// cancel takes its charge back out, an unlinked one does nothing, a dry run
// is left out, refunds stop at what their charge has left. The records go
// through ReadLedger, so the Go side reads them as it reads a real ledger.
func TestSpendWeeklyV3MatchesPythonOnOneCorpus(t *testing.T) {
	testFoldMatchesPython(t, capsV5Engine(t), "corpus.json", "python-totals.json")
}

// THE GATE FOR spend.weekly/3.1.0 (capsule-engine #138, a710636c): the same
// corpus, which 3.1.0 totals as 3.0.0 does, and the cases 3.1.0 adds
// (corpus-3.1.0.json): the engine's record of an executed act counts, its
// copy of a history act (live_history disposition) is left out, an
// executed record with no amount or a dry run is not counted, and an
// executed cancel is an unlinked reversal. The records go through
// ReadLedger, so asg_payload.live_history is read as sealed.
func TestSpendWeeklyV31MatchesPythonOnOneCorpus(t *testing.T) {
	testFoldMatchesPython(t, shippedCapsEngine(t), "corpus-3.1.0.json", "python-totals-3.1.0.json")
}

func testFoldMatchesPython(t *testing.T, e *Engine, corpusFile, totalsFile string) {
	var corpus differentialCorpus
	if err := json.Unmarshal(readFile(t, "testdata", "fold-differential", corpusFile), &corpus); err != nil {
		t.Fatal(err)
	}
	var py pythonTotals
	if err := json.Unmarshal(readFile(t, "testdata", "fold-differential", totalsFile), &py); err != nil {
		t.Fatal(err)
	}
	if e.capsFold.id != corpus.Fold || e.capsFold.digest != py.FoldDigest {
		t.Fatalf("Go fold %s at %s; Python ran %s at %s", e.capsFold.id, e.capsFold.digest, py.Fold, py.FoldDigest)
	}
	anchor, err := parseInstant(corpus.AsOf)
	if err != nil {
		t.Fatal(err)
	}
	if len(corpus.Cases) < 4 || len(py.Totals) != len(corpus.Cases) {
		t.Fatalf("%d cases, %d Python totals", len(corpus.Cases), len(py.Totals))
	}
	for _, c := range corpus.Cases {
		var lines bytes.Buffer
		for _, r := range c.Records {
			if err := json.Compact(&lines, r); err != nil {
				t.Fatal(err)
			}
			lines.WriteByte('\n')
		}
		records, err := ReadLedger(&lines)
		if err != nil {
			t.Fatalf("%s: %v", c.Name, err)
		}
		got, rev := e.capsFold.evaluate(records, corpus.KeyValue, anchor)
		want, ok := py.Totals[c.Name]
		if !ok {
			t.Fatalf("%s: no Python total", c.Name)
		}
		gotRev := [3]int64{rev.linkedCount, rev.linkedMinor, rev.unlinkedCount}
		wantRev := [3]int64{want.Reversals.LinkedCount, want.Reversals.LinkedMinor, want.Reversals.UnlinkedCount}
		if got != want.WeeklySpendMinor || gotRev != wantRev {
			t.Errorf("%s (%s): Go %d %v, Python %d %v", c.Name, c.Expect, got, gotRev, want.WeeklySpendMinor, wantRev)
		}
		t.Log(fmt.Sprintf("%-40s Go %5d  Python %5d", c.Name, got, want.WeeklySpendMinor))
	}
}

// caps.py require_per_action_reads: only spend_authorized_minor, or none.
func TestPerActionReadsIsOneOfTheEngines(t *testing.T) {
	for v, ok := range map[string]bool{"": true, "spend_authorized_minor": true, "spend_minor": false, "amount_minor": false} {
		if err := requirePerActionReads(v); (err == nil) != ok {
			t.Errorf("%q: %v", v, err)
		}
	}
}
