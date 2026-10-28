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

// shippedCapsEngine is the engine over the shipped pack, asg/everyday/0.3.8 (0.3.6's caps):
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
// through DecodeCapsule, so the Go side reads them as it reads a stored
// capsule.
func TestSpendWeeklyV3MatchesPythonOnOneCorpus(t *testing.T) {
	testFoldMatchesPython(t, capsV5Engine(t), "corpus.json", "python-totals.json")
}

// THE GATE FOR spend.weekly/3.1.0 (capsule-engine #138, a710636c): the same
// corpus, which 3.1.0 totals as 3.0.0 does, and the cases 3.1.0 adds
// (corpus-3.1.0.json): the engine's record of an executed act counts, its
// copy of a history act (live_history disposition) is left out, an
// executed record with no amount or a dry run is not counted, and an
// executed cancel is an unlinked reversal. The records go through
// DecodeCapsule, so asg_payload.live_history is read as sealed.
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
		records, err := readCapsules(&lines)
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

// caps/5.0.0 over spend.weekly/3.0.0, decision by decision, against a live
// Python run at the commit it names (testdata/parity/generate_caps_v5.py).
// Four configurations:
//   - pack: the frozen 0.3.5, compared in full (outcome, every constraint's
//     result and evidence digest, per_action_basis and limit_sources byte
//     for byte, and each rule's own result). An authorised maximum above
//     the capture is read; one below it, or none, falls back to the
//     capture; 3000 trips the 2500 limit. Each purchase here is from a new
//     merchant, so counterparty_seen_before fails it and, money.purchase
//     having an approver from taxonomy 3, it escalates, as in Python.
//   - pack_036: the same actions under 0.3.6's caps, which the shipped 0.3.8 cites (caps/5.1.0 over
//     spend.weekly/3.1.0), compared in full the same way: the same results,
//     the evidence naming the 3.1.0 fold.
//   - bare_2500 and per_action_500: GuardEngines with caps alone for
//     money.purchase (2500 or 500 per action, 10000 per 7 days), mirrored by
//     the shipped engine with those limits and no limit_sources; caps alone
//     is compared, and each step enters the ledger with Python's outcome, so
//     both see the same history. bare_2500 fills the window (the total sums
//     captures: 9500 then a 454 capture with 954 authorised is 9954, under
//     10000); per_action_500 denies 954 authorised on a 454 capture.
func TestCapsV5MatchesPythonDecisions(t *testing.T) {
	var p struct {
		CapsuleEngineCommit string `json:"capsule_engine_commit"`
		Scenarios           []struct {
			Config string `json:"config"`
			parityScenario
		} `json:"scenarios"`
	}
	if err := json.Unmarshal(readFile(t, "testdata", "parity", "caps-v5-parity.json"), &p); err != nil {
		t.Fatal(err)
	}
	if p.CapsuleEngineCommit != "a710636c29814ce67e838c673026fd2063d913f8" {
		t.Fatalf("caps/5.0.0 parity run at %s", p.CapsuleEngineCommit)
	}
	bare := func(perAction int64) *Engine {
		e := capsV5Engine(t)
		e.capsMinor = map[string]int64{"money.purchase": 10000}
		e.perActionMinor = map[string]int64{"money.purchase": perAction}
		e.limitSources = false
		return e
	}
	engines := map[string]*Engine{"pack": capsV5Engine(t), "pack_036": shippedCapsEngine(t), "bare_2500": bare(2500), "per_action_500": bare(500)}
	ledgers := map[string][]Record{}
	outcomes := map[string]Decision{}
	for _, s := range p.Scenarios {
		e, ok := engines[s.Config]
		if !ok {
			t.Fatalf("%s: config %q", s.Scenario, s.Config)
		}
		a, err := DecodeAction(bytes.NewReader(s.Action))
		if err != nil {
			t.Fatalf("%s: %v", s.Scenario, err)
		}
		d, err := e.Check(a, MemoryHistory(ledgers[s.Config]))
		if err != nil {
			t.Fatalf("%s: %v", s.Scenario, err)
		}
		if s.Config == "pack" || s.Config == "pack_036" {
			compareToPython(t, s.Scenario, e, a, d, s.parityScenario)
			compareRulesToPython(t, s.Scenario, e, a, d, s.parityScenario)
		} else {
			caps, _ := d.Constraint("caps")
			var py *parityConstraint
			for i := range s.Constraints {
				if s.Constraints[i].ID == "caps" {
					py = &s.Constraints[i]
				}
			}
			digest, _, err := caps.EvidenceDigest()
			if err != nil {
				t.Fatal(err)
			}
			if py == nil || string(caps.Result) != py.Result || py.EvidenceDigest == nil || digest != *py.EvidenceDigest {
				t.Errorf("%s: Go caps %s %s, Python %+v", s.Scenario, caps.Result, digest, py)
			}
		}
		outcomes[s.Scenario] = d
		ledgers[s.Config] = append(ledgers[s.Config], RecordFor(a, Decision{Outcome: s.Outcome}))
	}
	for name, want := range map[string]struct {
		caps, field string
		fellBack    bool
		projected   int64
	}{
		"authorised-above-capture":      {"pass", "spend_authorized_minor", false, 454},
		"authorised-below-capture":      {"pass", "spend_minor", true, 454},
		"authorised-absent":             {"pass", "spend_minor", true, 454},
		"authorised-over-limit":         {"fail", "spend_authorized_minor", false, 454},
		"booking-over-limit":            {"fail", "spend_minor", true, 55880},
		"window-authorised-under-total": {"pass", "spend_authorized_minor", false, 9954},
		"v4-purchase-over-window":       {"fail", "spend_minor", true, 10100},
		"authorised-954-limit-500":      {"fail", "spend_authorized_minor", false, 454},
		"absent-limit-500":              {"pass", "spend_minor", true, 454},
	} {
		caps, _ := outcomes[name].Constraint("caps")
		f, ok := caps.Caps()
		if string(caps.Result) != want.caps || !ok || f.Basis == nil || f.Basis.Field != want.field || f.Basis.FellBack != want.fellBack || f.ProjectedMinor != want.projected {
			t.Errorf("%s: caps %s, basis %+v, projected %d; want %+v", name, caps.Result, f.Basis, f.ProjectedMinor, want)
		}
	}
}
