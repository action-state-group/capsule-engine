package guard

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"strings"
	"testing"
)

// AN UNCHECKED ACT IS UNAUTHORIZED BUT REAL (ruling (a), 2026-10-09):
// capsulectl seals an act the agent takes after a check paused with no
// sealed approval as unchecked (rule no_sealed_approval): an x-deal-v0
// outcome record whose body.unchecked is the act, or in typed records an
// action-outcome/v0 whose body.attempted is. It moved money, so it counts as
// spend in the window, never skipped; it was never authorized, so it is
// never accepted, never makes its merchant seen, and is never an earlier act
// for dedupe. Held against real capsulectl records (testdata/real-unchecked-act,
// capsule-cli 53b702f7).

const uncheckedDir = "real-unchecked-act"

// uncheckedHistory is the history capsulectl gave its rules checker at the
// named check of the fixture.
func uncheckedHistory(t *testing.T, name string) []json.RawMessage {
	t.Helper()
	return decodeJSON[struct {
		History []json.RawMessage `json:"history"`
	}](t, readFile(t, "testdata", uncheckedDir, name+".input.json")).History
}

func TestARealUncheckedActIsReadAsUncheckedSpend(t *testing.T) {
	acts := uncheckedHistory(t, "typed-check-2")
	if len(acts) != 2 {
		t.Fatalf("%d history acts, want the two deals' unchecked acts", len(acts))
	}
	shapes := map[string]bool{}
	for _, raw := range acts {
		r, err := DecodeHistoryAct(raw)
		if err != nil {
			t.Fatal(err)
		}
		if !r.Unchecked || r.Decision != nil || r.AmountMinor == nil || *r.AmountMinor != 2000 || r.Target != nil {
			t.Errorf("%s: unchecked %v decision %v amount %v target %v; want unchecked, no decision, 2000, no target",
				r.CapsuleID, r.Unchecked, r.Decision, r.AmountMinor, r.Target)
		}
		var ai struct {
			Type  string `json:"type"`
			XDeal *struct {
				RecordType string `json:"record_type"`
			} `json:"x-deal-v0"`
		}
		var h struct {
			AgentInput json.RawMessage `json:"agent_input"`
		}
		_ = json.Unmarshal(raw, &h)
		_ = json.Unmarshal(h.AgentInput, &ai)
		if ai.XDeal != nil {
			shapes["x-deal-v0 "+ai.XDeal.RecordType] = true
		} else {
			shapes[ai.Type] = true
		}
	}
	if !shapes["x-deal-v0 outcome"] || !shapes["action-outcome/v0"] {
		t.Errorf("shapes %v: want one x-deal-v0 outcome and one action-outcome/v0", shapes)
	}
}

// A sealed disposition on an unchecked act is never read: capsulectl seals
// none, and an act taken without authority is never an accepted one.
func TestAnUncheckedActIsNeverAccepted(t *testing.T) {
	raw := uncheckedHistory(t, "x-deal-v0-check-2")[0]
	r, err := DecodeHistoryAct(withMember(t, raw, "disposition", json.RawMessage(`{"decision":"accept"}`)))
	if err != nil {
		t.Fatal(err)
	}
	if r.Decision != nil || r.accepted() {
		t.Errorf("an unchecked act carrying an accept disposition reads as decision %v", *r.Decision)
	}
}

// uncheckedAct is an unchecked act of amount at the payee the next purchase
// pays, in the window, keyed as a deal check's act is.
func uncheckedAct(amount int64, target string) Record {
	return Record{CapsuleID: "unchecked-" + target, ActionID: "deal-1/5", ActionType: "fyi", Operator: "household-a",
		Developer: "capsulectl-deal", Timestamp: "2026-10-08T10:00:00Z", AmountMinor: &amount, Target: &target,
		ActionClass: strp("money.purchase"), TaxonomyVersion: strp("6"), Direction: strp("out"), Unchecked: true}
}

func uncheckedPurchase(t *testing.T, amount int64, target string) Action {
	t.Helper()
	a, err := DecodeAction(strings.NewReader(`{"verb":"pay","operator":"household-a","developer":"capsulectl-deal",` +
		`"action_class":"money.purchase","taxonomy_version":"6","action_type":"fyi","amount_minor":` + itoa(amount) +
		`,"currency":"USD","target":"` + target + `","timestamp":"2026-10-09T10:00:00Z"}`))
	if err != nil {
		t.Fatal(err)
	}
	return a
}

func itoa(n int64) string { b, _ := json.Marshal(n); return string(b) }

// It is spend: the window total counts it, so a purchase it pushes over the
// limit fails, and the evidence names it. It is never seen and never a
// dedupe match, even at the same payee for the same amount.
func TestAnUncheckedActIsSpendNeverSeenNeverDeduped(t *testing.T) {
	e, _ := everydayEngine(t)
	cap := e.capsMinor[e.tax.canonical("money.purchase")]
	a := uncheckedPurchase(t, 2000, "payee/one")
	for _, tc := range []struct {
		name    string
		earlier int64
		result  Result
	}{{"under", 2000, Pass}, {"over", cap - 1999, Fail}} {
		h := History{Days: 31, Complete: true, Acts: []Record{uncheckedAct(tc.earlier, "payee/one")}}
		d, err := e.CheckWithHistory(a, h)
		if err != nil {
			t.Fatal(err)
		}
		caps, _ := d.Constraint("caps")
		f, _ := caps.Caps()
		var window CapsPart
		for _, p := range f.Parts {
			if p.Limit == "window" {
				window = p
			}
		}
		if window.Result != tc.result || f.WeeklySpendMinor != tc.earlier || f.ProjectedMinor != tc.earlier+2000 || window.Pending != "" {
			t.Errorf("%s: window %s (%q), weekly %d projected %d; want %s, %d, %d", tc.name, window.Result, window.Pending,
				f.WeeklySpendMinor, f.ProjectedMinor, tc.result, tc.earlier, tc.earlier+2000)
		}
		if u := f.UncheckedInWindow; u == nil || u.Count != 1 || u.Minor != tc.earlier {
			t.Errorf("%s: unchecked in window %+v, want 1 act of %d", tc.name, u, tc.earlier)
		}
		if dd, _ := d.Constraint("dedupe"); dd.Result != Pass {
			t.Errorf("%s: dedupe %s against an unchecked act, want pass", tc.name, dd.Result)
		}
		if seen, _ := d.Constraint("counterparty_seen_before"); seen.Result == Pass {
			t.Errorf("%s: the unchecked act made its payee seen", tc.name)
		}
	}
	// The control: the same act accepted is a dedupe match and seen.
	accepted := uncheckedAct(2000, "payee/one")
	accepted.Unchecked, accepted.Decision = false, strp("accept")
	d, err := e.CheckWithHistory(a, History{Days: 31, Complete: true, Acts: []Record{accepted}})
	if err != nil {
		t.Fatal(err)
	}
	if dd, _ := d.Constraint("dedupe"); dd.Result != Fail {
		t.Errorf("control: dedupe %s against the accepted act, want fail", dd.Result)
	}
	// Money in stays 0 and leaves the window evaluated (#130).
	in := uncheckedAct(0, "payee/one")
	in.Direction = strp(moneyIn)
	d, err = e.CheckWithHistory(a, History{Days: 31, Complete: true, Acts: []Record{in}})
	if err != nil {
		t.Fatal(err)
	}
	caps, _ := d.Constraint("caps")
	if f, _ := caps.Caps(); caps.Result != Pass || f.WeeklySpendMinor != 0 {
		t.Errorf("money in: caps %s weekly %d, want pass 0", caps.Result, f.WeeklySpendMinor)
	}
}

// The #94 known-difference pin is retired: capsule-engine #138 (a710636c) counts the unchecked act's spend.
// The engine's replay of the two real bundles (generate_engine_replay.py at
// a710636c, asg/everyday/0.3.6) and this port, on the envelope capsulectl
// sent at the same second check, both count the unchecked act's 2000.
func TestTheEngineReplayCountsTheUncheckedSpendAsThisPortDoes(t *testing.T) {
	v := decodeJSON[struct {
		Commit  string `json:"capsule_engine_commit"`
		Bundles map[string]struct {
			Decisions []struct {
				CapsuleID    string `json:"capsule_id"`
				CapsEvidence struct {
					WeeklySpendMinor *int64 `json:"weekly_spend_minor"`
				} `json:"caps_evidence"`
			} `json:"decisions"`
			Undecided []string `json:"undecided"`
		} `json:"bundles"`
	}](t, readFile(t, "testdata", uncheckedDir, "engine-replay.json"))
	e, _ := everydayEngine(t)
	for _, set := range []string{"x-deal-v0", "typed"} {
		b := v.Bundles[set+".bundle.json"]
		act := decodeJSON[struct {
			CapsuleID string `json:"capsule_id"`
		}](t, readFile(t, "testdata", uncheckedDir, set+"-act.json")).CapsuleID
		if len(b.Decisions) != 2 || !contains(b.Undecided, act) {
			t.Fatalf("%s: %d decisions; the unchecked act %s undecided: %v", set, len(b.Decisions), act, contains(b.Undecided, act))
		}
		if got := b.Decisions[1].CapsEvidence.WeeklySpendMinor; got == nil || *got != 2000 {
			t.Errorf("%s: the engine (%s) counts %s of unchecked spend before the second check, want 2000", set, v.Commit, shownMinor(got))
		}
	}
	// This port, on the envelope capsulectl sent at the same second check
	// (the x-deal-v0 deal: its own unchecked act is the only history),
	// counts the same 2000.
	in := decodeJSON[struct {
		Record       json.RawMessage `json:"record"`
		HistoryScope struct {
			Days     int  `json:"days"`
			Complete bool `json:"complete"`
		} `json:"history_scope"`
	}](t, readFile(t, "testdata", uncheckedDir, "x-deal-v0-check-2.input.json"))
	a, err := DecodeCheckInputRecord(in.Record)
	if err != nil {
		t.Fatal(err)
	}
	var id struct {
		CapsuleID string `json:"capsule_id"`
	}
	_ = json.Unmarshal(in.Record, &id)
	if id.CapsuleID != v.Bundles["x-deal-v0.bundle.json"].Decisions[1].CapsuleID {
		t.Fatalf("the envelope's record %s is not the engine's second check", id.CapsuleID)
	}
	h := History{Days: in.HistoryScope.Days, Complete: in.HistoryScope.Complete}
	for _, raw := range uncheckedHistory(t, "x-deal-v0-check-2") {
		r, err := DecodeHistoryAct(raw)
		if err != nil {
			t.Fatal(err)
		}
		h.Acts = append(h.Acts, r)
	}
	d, err := e.CheckWithHistory(a, h)
	if err != nil {
		t.Fatal(err)
	}
	caps, _ := d.Constraint("caps")
	if f, _ := caps.Caps(); f.WeeklySpendMinor != 2000 {
		t.Errorf("this port counts %d before the second check, want the unchecked act's 2000", f.WeeklySpendMinor)
	}
}

func shownMinor(p *int64) string {
	if p == nil {
		return "none"
	}
	return fmt.Sprint(*p)
}

func contains(xs []string, x string) bool {
	for _, y := range xs {
		if y == x {
			return true
		}
	}
	return false
}

// PROVENANCE.json pins every fixture file by its sha256 (the set has no
// MANIFEST), and the commits that made them.
func TestRealUncheckedActFixtureIsThePinnedBytes(t *testing.T) {
	prov := decodeJSON[struct {
		Producer struct {
			Commit string `json:"commit"`
		} `json:"producer"`
		EngineReplay struct {
			Commit string `json:"capsule_engine_commit"`
		} `json:"engine_replay"`
		Files map[string]struct {
			SHA256 string `json:"sha256"`
		} `json:"files"`
	}](t, readFile(t, "testdata", uncheckedDir, "PROVENANCE.json"))
	replayed := decodeJSON[struct {
		Commit string `json:"capsule_engine_commit"`
	}](t, readFile(t, "testdata", uncheckedDir, "engine-replay.json"))
	if prov.Producer.Commit != "53b702f7d673207f1f016dae27cca285a35d51ac" || prov.EngineReplay.Commit != replayed.Commit || len(prov.Files) < 12 {
		t.Fatalf("provenance: producer %s, replay %s (file says %s), %d files", prov.Producer.Commit, prov.EngineReplay.Commit,
			replayed.Commit, len(prov.Files))
	}
	for name, f := range prov.Files {
		sum := sha256.Sum256(readFile(t, "testdata", uncheckedDir, name))
		if hex.EncodeToString(sum[:]) != f.SHA256 {
			t.Errorf("%s: sha256 %x, pinned %s", name, sum, f.SHA256)
		}
	}
}

// The window reads an unchecked act as the fold reads any record: under the
// fold's key (spend.weekly/3.0.0: the operator) and within its 7 days,
// at spend_minor when sealed (a cancel's), else amount_minor.
func TestAnUncheckedActIsCountedUnderTheFoldsKeyAndWindow(t *testing.T) {
	e, _ := everydayEngine(t)
	a := uncheckedPurchase(t, 2000, "payee/one")
	old := uncheckedAct(3000, "payee/one")
	old.Timestamp = "2026-10-01T09:59:59Z" // 8 days before the check
	other := uncheckedAct(3000, "payee/one")
	other.Operator = "household-b"
	sealedSpend := uncheckedAct(3000, "payee/one")
	sealedSpend.SpendMinor = i64p(500)
	for _, tc := range []struct {
		name   string
		act    Record
		weekly int64
	}{{"outside the window", old, 0}, {"another operator", other, 0}, {"spend_minor sealed", sealedSpend, 500}} {
		d, err := e.CheckWithHistory(a, History{Days: 31, Complete: true, Acts: []Record{tc.act}})
		if err != nil {
			t.Fatal(err)
		}
		caps, _ := d.Constraint("caps")
		if f, _ := caps.Caps(); f.WeeklySpendMinor != tc.weekly {
			t.Errorf("%s: weekly %d, want %d", tc.name, f.WeeklySpendMinor, tc.weekly)
		}
	}
}
