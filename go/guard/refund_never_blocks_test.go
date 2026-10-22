package guard

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"strings"
	"testing"

	"github.com/action-state-group/capsule-engine/go/pack"
)

// refundVectors is the engine side's python-decisions.json for
// a refund never blocks the spend window (capsule-engine #130, 62d7a92).
type refundVectors struct {
	MoneyInDirection       string `json:"money_in_direction"`
	RefundStatedSpendMinor int64  `json:"refund_stated_spend_minor"`
	Bridge                 map[string]struct {
		AmountMinor int64           `json:"amount_minor"`
		Body        json.RawMessage `json:"body"`
	} `json:"bridge"`
	Sequences map[string]struct {
		WindowMinor int64 `json:"window_minor"`
		Steps       []struct {
			Act          string                     `json:"act"`
			AmountMinor  int64                      `json:"amount_minor"`
			CapsResult   string                     `json:"caps_result"`
			CapsEvidence map[string]json.RawMessage `json:"caps_evidence"`
		} `json:"steps"`
	} `json:"sequences"`
}

func refundVectorsOf(t *testing.T) refundVectors {
	t.Helper()
	return decodeJSON[refundVectors](t, readFile(t, "testdata", "refund-never-blocks-vectors", "python-decisions.json"))
}

// moneyInCheck is a deal check whose body is body, sealed as the engine's
// test seals it (_money_in): a check record in a deal, its agent_input bound
// by the header's digest, taxonomy 6 and USD.
func moneyInCheck(t *testing.T, body json.RawMessage) Action {
	t.Helper()
	body = withMember(t, body, "taxonomy_version", json.RawMessage(`"6"`))
	body = withMember(t, body, "currency", json.RawMessage(`"USD"`))
	ai := json.RawMessage(`{"body":` + string(body) + `,"x-deal-v0":{"record_type":"check","deal_id":"deal-0000000000000000","seq":7}}`)
	raw := fmt.Sprintf(`{"capsule_id":%q,"action_id":"deal-0000000000000000/7","action_type":"fyi","operator":"household-a",`+
		`"developer":"capsulectl-deal","timestamp":"2026-10-02T10:00:00Z","agent_input":%s,`+
		`"model_attestation":{"compute_attestation":{"agent_input_digest":%q}}}`, strings.Repeat("0", 64), ai, jcsDigest(t, ai))
	a, err := DecodeRecord([]byte(raw), "")
	if err != nil {
		t.Fatal(err)
	}
	return a
}

// purchaseAction is the engine test's _purchase(n, amount).
func purchaseAction(t *testing.T, n int, amount int64) Action {
	t.Helper()
	a, err := DecodeAction(strings.NewReader(fmt.Sprintf(`{"verb":"buy","operator":"household-a","developer":"shopping-assistant@v1",`+
		`"action_class":"money.purchase","action_id":"buy/%d","amount_minor":%d,"currency":"USD","target":"shop/%d",`+
		`"timestamp":"2026-10-0%dT10:00:00Z"}`, n, amount, n, n)))
	if err != nil {
		t.Fatal(err)
	}
	return a
}

// refundVectorEngine is the engine the vectors ran (_engine): spend.weekly
// 3.0.0, one window limit on money.purchase, money.refund and
// external_commitment.other, no per-action limit and no limit sources.
func refundVectorEngine(t *testing.T, window int64) *Engine {
	t.Helper()
	return refundVectorEngineOf(t, window, pack.LoadEveryday035, "spend.weekly/3.0.0")
}

// refundVectorEngineOf is refundVectorEngine over the pack load gives,
// whose caps fold must be fold: the frozen asg/everyday/0.3.5 over
// spend.weekly/3.0.0, or the shipped 0.3.7 (0.3.6's caps) over spend.weekly/3.1.0.
func refundVectorEngineOf(t *testing.T, window int64, load func() (pack.Pack, error), fold string) *Engine {
	t.Helper()
	p, err := load()
	if err != nil {
		t.Fatal(err)
	}
	e, err := NewEngine(p)
	if err != nil {
		t.Fatal(err)
	}
	if e.capsFold.id != fold {
		t.Fatalf("the everyday pack's caps fold is %s", e.capsFold.id)
	}
	e.capsMinor = map[string]int64{"money.purchase": window, "money.refund": window, "external_commitment.other": window}
	e.perActionMinor, e.perActionReads, e.limitSources = map[string]int64{}, "", false
	return e
}

func capsOf(t *testing.T, d Decision) Constraint {
	t.Helper()
	c, ok := d.Constraint("caps")
	if !ok {
		t.Fatal("no caps constraint")
	}
	return c
}

// The bridge rows: a deal check stating direction "in" is a spend of 0,
// whatever its spend_minor says (MONEY_IN).
func TestAMoneyInCheckIsBridgedAsTheEngineBridgesIt(t *testing.T) {
	v := refundVectorsOf(t)
	if v.MoneyInDirection != "in" || len(v.Bridge) != 2 {
		t.Fatalf("vectors: direction %q, %d bridge rows", v.MoneyInDirection, len(v.Bridge))
	}
	for kind, row := range v.Bridge {
		got, reads := moneyInCheck(t, row.Body).engineAmount()
		if got == nil || *got != row.AmountMinor || strings.Join(reads, ",") != "direction" {
			t.Errorf("%s: amount %s read from %v, want %d from the direction", kind, shown(got), reads, row.AmountMinor)
		}
	}
}

// The acceptance for that rule: every sequence
// (purchase 300, a money-in check stating spend 500, purchase 200; under and
// over the window limit) gives the engine's caps result at every step. The
// first two steps are checked over the earlier steps as accepted acts and
// their evidence is the engine's byte for byte. The third is checked as
// check --emit checks it: over a complete 31-day history holding the
// purchase, accepted, and the money-in act WITHOUT a recorded decision, as
// capsulectl 831afeeb seals a refund. That act is never spend, so it does
// not leave the window unevaluated: the third step's caps is the engine's
// pass or fail with the total at 300, never not_evaluable, and the window
// names the one money-in act it did not count.
func TestAMoneyInActNeverBlocksTheWindowAsTheEngineDecides(t *testing.T) {
	v := refundVectorsOf(t)
	if len(v.Sequences) != 4 {
		t.Fatalf("%d sequences", len(v.Sequences))
	}
	for name, seq := range v.Sequences {
		kind, _, _ := strings.Cut(name, "/")
		e := refundVectorEngine(t, seq.WindowMinor)
		acts := []Action{purchaseAction(t, 1, 300), moneyInCheck(t, v.Bridge[kind].Body), purchaseAction(t, 3, 200)}
		accept := "accept"
		decided := []Record{{CapsuleID: "1", Operator: "household-a", Developer: "shopping-assistant@v1", Timestamp: "2026-10-01T10:00:00Z",
			Decision: &accept, AmountMinor: new(int64(300)), ActionClass: new("money.purchase")}}
		for i, step := range seq.Steps {
			if step.Act != []string{"purchase", kind, "purchase"}[i] {
				t.Fatalf("%s #%d is %s", name, i+1, step.Act)
			}
			var d Decision
			var err error
			if i < 2 {
				d, err = e.Check(acts[i], decided[:i])
			} else {
				in := "in"
				history := append(decided[:1:1], Record{CapsuleID: "2", Operator: "household-a", Developer: "capsulectl-deal",
					Timestamp: "2026-10-02T10:00:00Z", AmountMinor: new(v.RefundStatedSpendMinor), SpendMinor: new(v.RefundStatedSpendMinor),
					ActionClass:     new(map[string]string{"refund": "money.refund", "partial_cancel": "external_commitment.other"}[kind]),
					TaxonomyVersion: new("6"), Direction: &in})
				d, err = e.CheckWithHistory(acts[i], History{Days: 31, Complete: true, Acts: history})
			}
			if err != nil {
				t.Fatalf("%s #%d: %v", name, i+1, err)
			}
			c := capsOf(t, d)
			// The caps result, not the outcome: the engine's test engine
			// runs caps alone, this one the everyday pack's other checks
			// too (a first-time merchant asks).
			if string(c.Result) != step.CapsResult {
				t.Errorf("%s #%d: caps %s (%q); the engine: %s", name, i+1, c.Result, c.Pending(), step.CapsResult)
				continue
			}
			want := map[string]json.RawMessage{}
			for k, raw := range step.CapsEvidence {
				want[k] = raw
			}
			if i == 2 {
				// The engine decided the money-in check and its fold reads
				// it as an unlinked reversal; here the act carries no
				// decision, so the fold never reads it and there is no
				// reversal to count. Every other member is the engine's.
				want["reversals"] = json.RawMessage(`{"linked_count":0,"linked_minor":0,"unlinked_count":0}`)
				f, _ := c.Caps()
				if x := f.ExcludedFromWindow; x == nil || x.Direction != "in" || x.Count != 1 {
					t.Errorf("%s #3: excluded_from_window %+v, want direction in, count 1", name, x)
				}
			}
			wantRaw, err := json.Marshal(want)
			if err != nil {
				t.Fatal(err)
			}
			if got, ok, err := c.EvidenceDigest(); err != nil || !ok || got != jcsDigest(t, wantRaw) {
				t.Errorf("%s #%d: caps evidence digest %s (%v %v), the engine's %s", name, i+1, got, ok, err, wantRaw)
			}
			if i == 1 {
				decided = append(decided, Record{CapsuleID: "2", Operator: "household-a", Developer: "capsulectl-deal",
					Timestamp: "2026-10-02T10:00:00Z", Decision: &accept, AmountMinor: new(int64(0)), ActionClass: acts[1].ActionClass})
			}
		}
	}
}

// An act with no recorded decision that does not state money moved in and
// whose spend cannot be read still leaves the window unevaluated, an out act
// included, and so does one stating no direction or another (the money-in
// rule keys on the direction, as the bridge does, never on the spend). One
// whose spend can be read, 0 included, is counted at it (the G3 ruling,
// which supersedes ruling (b) here; g3_executed_acts_test.go). Nothing is
// named excluded then.
func TestAnUndecidedActWithNoSpendThatIsNotMoneyInStillBlocksTheWindow(t *testing.T) {
	e := refundVectorEngine(t, 1000)
	accept, out, in := "accept", "out", "in"
	purchase := Record{CapsuleID: "1", Operator: "household-a", Developer: "d", Timestamp: "2026-10-01T10:00:00Z",
		Decision: &accept, AmountMinor: new(int64(300)), ActionClass: new("money.purchase")}
	undecided := func(direction *string, spend *int64) Record {
		return Record{CapsuleID: "2", Operator: "household-a", Developer: "d", Timestamp: "2026-10-02T10:00:00Z",
			AmountMinor: new(int64(500)), SpendMinor: spend, ActionClass: new("money.purchase"), Direction: direction}
	}
	for name, r := range map[string]Record{
		"out, no spend":          undecided(&out, nil),
		"out, a negative spend":  undecided(&out, new(int64(-1))),
		"no direction, no spend": undecided(nil, nil),
		"another direction":      undecided(new("sideways"), nil),
		"in, and another, out":   undecided(&out, nil),
	} {
		acts := []Record{purchase, r}
		if name == "in, and another, out" {
			acts = append(acts, undecided(&in, new(int64(0))))
		}
		d, err := e.CheckWithHistory(purchaseAction(t, 3, 200), History{Days: 31, Complete: true, Acts: acts})
		if err != nil {
			t.Fatal(err)
		}
		c := capsOf(t, d)
		f, _ := c.Caps()
		// The reason says the act has no recorded decision, never why: a
		// checked act capsulectl seals fyi carries none either.
		const want = "1 earlier act(s) carry no recorded decision; the 7d total as the engine computes it counts accepted acts only, so it cannot be computed"
		if c.Result != NotApplicable || c.Pending() != want {
			t.Errorf("%s: caps %s %q; want not evaluable, one undecided act", name, c.Result, c.Pending())
		}
		if name != "in, and another, out" && f.ExcludedFromWindow != nil {
			t.Errorf("%s: excluded_from_window %+v, want none", name, f.ExcludedFromWindow)
		}
	}
}

// With no money-in act skipped, the caps facts name nothing excluded. With
// one skipped, the name stays internal: external-check-result/v0 has no
// member for it (a finding is id, check, verdict, reason, limit, value, and
// no other), the reason is shown only on a fail or not_evaluable, and the
// machine report (CheckReport) gains no member for it either.
func TestTheExcludedMoneyInActStaysInternal(t *testing.T) {
	e := refundVectorEngine(t, 1000)
	accept, in := "accept", "in"
	purchase := Record{CapsuleID: "1", Operator: "household-a", Developer: "d", Timestamp: "2026-10-01T10:00:00Z", Decision: &accept,
		AmountMinor: new(int64(300)), ActionClass: new("money.purchase")}
	refund := Record{CapsuleID: "2", Operator: "household-a", Developer: "d", Timestamp: "2026-10-02T10:00:00Z", AmountMinor: new(int64(300)),
		SpendMinor: new(int64(0)), ActionClass: new("money.refund"), Direction: &in}
	for name, c := range map[string]struct {
		acts     []Record
		excluded *ExcludedFromWindow
	}{
		"no money-in act": {[]Record{purchase}, nil},
		"one refund":      {[]Record{purchase, refund}, &ExcludedFromWindow{Direction: "in", Count: 1}},
		"two refunds":     {[]Record{purchase, refund, refund}, &ExcludedFromWindow{Direction: "in", Count: 2}},
	} {
		a := purchaseAction(t, 3, 200)
		d, err := e.CheckWithHistory(a, History{Days: 31, Complete: true, Acts: c.acts})
		if err != nil {
			t.Fatal(err)
		}
		caps := capsOf(t, d)
		f, _ := caps.Caps()
		if caps.Result != Pass || (f.ExcludedFromWindow == nil) != (c.excluded == nil) ||
			(c.excluded != nil && *f.ExcludedFromWindow != *c.excluded) {
			t.Errorf("%s: caps %s, excluded %+v; want pass, %+v", name, caps.Result, f.ExcludedFromWindow, c.excluded)
		}
		rep, err := json.Marshal(e.Report(a, d))
		if err != nil {
			t.Fatal(err)
		}
		if strings.Contains(string(rep), "excluded") || strings.Contains(string(rep), "money-in") {
			t.Errorf("%s: the report names the exclusion: %s", name, rep)
		}
	}
}

// On capsulectl 831afeeb's own envelopes the refund act (fyi, direction
// "in", no disposition) is read with its direction and left out of the
// window, by name, once; the window is the three accepted purchases.
func TestARealRefundActIsReadAsMoneyIn(t *testing.T) {
	p, err := pack.LoadEveryday037()
	if err != nil {
		t.Fatal(err)
	}
	e, err := NewEngine(p)
	if err != nil {
		t.Fatal(err)
	}
	for name, weekly := range map[string]int64{"acc-probe-purchase-after-a-refund": 7000, "acc-refund-repeat-same-reversed-act": 5000} {
		env := decodeJSON[struct {
			Record  json.RawMessage   `json:"record"`
			History []json.RawMessage `json:"history"`
		}](t, readFile(t, "testdata", "refund-never-blocks-vectors", "real", name+".json"))
		a, err := DecodeCheckInputRecord(env.Record)
		if err != nil {
			t.Fatal(err)
		}
		h := History{Days: 31, Complete: true}
		moneyIn := 0
		for _, raw := range env.History {
			r, err := DecodeHistoryAct(raw)
			if err != nil {
				t.Fatalf("%s: %v", name, err)
			}
			if r.Decision == nil && r.Direction != nil && *r.Direction == "in" {
				moneyIn++
			}
			h.Acts = append(h.Acts, r)
		}
		d, err := e.CheckWithHistory(a, h)
		if err != nil {
			t.Fatal(err)
		}
		c := capsOf(t, d)
		f, _ := c.Caps()
		if moneyIn != 1 || c.Result != Pass || f.WeeklySpendMinor != weekly || f.ExcludedFromWindow == nil || *f.ExcludedFromWindow != (ExcludedFromWindow{"in", 1}) {
			t.Errorf("%s: %d money-in act(s); caps %s %q, weekly %d, excluded %+v", name, moneyIn, c.Result, c.Pending(), f.WeeklySpendMinor, f.ExcludedFromWindow)
		}
	}
}

// The vectors and the real envelopes are the engine side's and capsulectl's bytes, as
// PROVENANCE.json pins them.
func TestRefundVectorsAreVendoredByteForByte(t *testing.T) {
	type files map[string]struct {
		SHA256 string `json:"sha256"`
	}
	prov := decodeJSON[struct {
		EngineCommit string `json:"engine_commit"`
		Files        files  `json:"files"`
		Real         struct {
			Files files `json:"files"`
		} `json:"real"`
	}](t, readFile(t, "testdata", "refund-never-blocks-vectors", "PROVENANCE.json"))
	if prov.EngineCommit != "62d7a92726366f1c39d324854256fc80fd1a51e8" || len(prov.Files) != 4 || len(prov.Real.Files) != 2 {
		t.Fatalf("provenance: %+v", prov)
	}
	for _, fs := range []files{prov.Files, prov.Real.Files} {
		for name, f := range fs {
			if got := sha256.Sum256(readFile(t, "testdata", "refund-never-blocks-vectors", name)); hex.EncodeToString(got[:]) != f.SHA256 {
				t.Errorf("%s: sha256 %x, pinned %s", name, got, f.SHA256)
			}
		}
	}
}

// sealedHistoryAct is a history act whose deal body is body, its agent_input
// bound by the header's digest, accepted when decided.
func sealedHistoryAct(t *testing.T, id string, body json.RawMessage, decided bool) []byte {
	t.Helper()
	ai := json.RawMessage(`{"body":` + string(body) + `,"x-deal-v0":{"record_type":"action","deal_id":"deal-0000000000000000","seq":8}}`)
	disposition := ""
	if decided {
		disposition = `"disposition":{"approver":"policy","decision":"accept","human_disposed":false,"verdict_class":"executed"},`
	}
	return []byte(fmt.Sprintf(`{"capsule_id":%q,"action_id":"deal-0000000000000000/8","action_type":"fyi","operator":"household-a",`+
		`"developer":"capsulectl-deal","timestamp":"2026-10-02T10:00:00Z",%s"agent_input":%s,`+
		`"model_attestation":{"compute_attestation":{"agent_input_digest":%q}}}`, id, disposition, ai, jcsDigest(t, ai)))
}

// The history decode bridges a money-in act as the engine's deal bridge
// does (report/replay.py MONEY_IN): its amount and its spend are 0, whatever
// the body states, so a partial cancel stating no amount_minor is read, not
// refused, and an accepted refund adds nothing to the window. The engine's
// fold reads its own decision on such a check at amount 0: decided, the
// third step's caps evidence is the engine's byte for byte, reversals
// included; undecided, the act is left out and named.
func TestAMoneyInHistoryActIsBridgedAsTheEngineBridgesIt(t *testing.T) {
	v := refundVectorsOf(t)
	for name, seq := range v.Sequences {
		kind, _, _ := strings.Cut(name, "/")
		body := withMember(t, v.Bridge[kind].Body, "taxonomy_version", json.RawMessage(`"6"`))
		body = withMember(t, body, "currency", json.RawMessage(`"USD"`))
		for _, decided := range []bool{true, false} {
			r, err := DecodeHistoryAct(sealedHistoryAct(t, strings.Repeat("2", 64), body, decided))
			if err != nil {
				t.Errorf("%s decided=%v: %v", name, decided, err)
				continue
			}
			if r.AmountMinor == nil || *r.AmountMinor != 0 || r.SpendMinor == nil || *r.SpendMinor != 0 {
				t.Errorf("%s decided=%v: amount %s spend %s, want 0 and 0", name, decided, shown(r.AmountMinor), shown(r.SpendMinor))
			}
			e := refundVectorEngine(t, seq.WindowMinor)
			accept := "accept"
			purchase := Record{CapsuleID: "1", Operator: "household-a", Developer: "shopping-assistant@v1", Timestamp: "2026-10-01T10:00:00Z",
				Decision: &accept, AmountMinor: new(int64(300)), ActionClass: new("money.purchase")}
			d, err := e.CheckWithHistory(purchaseAction(t, 3, 200), History{Days: 31, Complete: true, Acts: []Record{purchase, r}})
			if err != nil {
				t.Fatal(err)
			}
			step := seq.Steps[2]
			c := capsOf(t, d)
			if string(c.Result) != step.CapsResult {
				t.Errorf("%s decided=%v: caps %s (%q); the engine: %s", name, decided, c.Result, c.Pending(), step.CapsResult)
				continue
			}
			if !decided {
				continue
			}
			want, err := json.Marshal(step.CapsEvidence)
			if err != nil {
				t.Fatal(err)
			}
			if got, ok, err := c.EvidenceDigest(); err != nil || !ok || got != jcsDigest(t, want) {
				t.Errorf("%s decided: caps evidence digest %s (%v %v), the engine's %s", name, got, ok, err, want)
			}
		}
	}
}

// An act that does not state the money moved in is still refused without an
// amount_minor (the fold would silently leave it out), and read with one.
func TestAnOutActWithNoAmountIsStillRefused(t *testing.T) {
	for _, body := range []string{
		`{"action":"pay","action_class":"money.purchase","direction":"out","spend_minor":0,"taxonomy_version":"6","currency":"USD"}`,
		`{"action":"pay","action_class":"money.purchase","spend_minor":0,"taxonomy_version":"6","currency":"USD"}`,
	} {
		if _, err := DecodeHistoryAct(sealedHistoryAct(t, strings.Repeat("3", 64), json.RawMessage(body), true)); err == nil {
			t.Errorf("%s: read, want refused", body)
		}
		with := withMember(t, json.RawMessage(body), "amount_minor", json.RawMessage(`0`))
		if r, err := DecodeHistoryAct(sealedHistoryAct(t, strings.Repeat("3", 64), with, true)); err != nil || r.AmountMinor == nil || *r.AmountMinor != 0 {
			t.Errorf("%s with amount_minor 0: %v", body, err)
		}
	}
}
