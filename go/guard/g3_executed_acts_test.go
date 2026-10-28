package guard

import (
	"encoding/json"
	"testing"

	"github.com/action-state-group/capsule-engine/go/canon"
	"github.com/action-state-group/capsule-engine/go/pack"
)

// AN EXECUTED ACT COUNTS AT ITS SEALED SPEND (the G3 ruling, 2026-10-09,
// which supersedes ruling (b) for such acts): an act in history that carries no
// recorded decision but does carry a spend (spend_minor) is counted in the
// 7-day total at that spend and does not leave the window unevaluated. Money
// in counts 0 (#130). An act whose spend cannot be read (none, or one in the
// wrong shape) still leaves the window unevaluated. An act the guard
// accepted is counted once, never twice; unchecked acts still count.

// executedAct is a checked act capsulectl sealed fyi (no disposition): a
// purchase commit of amount at spend, in the window.
func executedAct(id string, spend *int64) Record {
	return Record{CapsuleID: id, ActionID: "deal-" + id + "/5", ActionType: "fyi", Operator: "household-a", Developer: "d",
		Timestamp: "2026-10-02T10:00:00Z", AmountMinor: new(int64(500)), SpendMinor: spend, ActionClass: new("money.purchase"),
		Direction: new("out")}
}

func capsWindow(t *testing.T, e *Engine, acts []Record) (Constraint, CapsFacts) {
	t.Helper()
	d, err := e.CheckWithHistory(purchaseAction(t, 3, 200), InputHistory{Days: 31, Complete: true, Acts: acts})
	if err != nil {
		t.Fatal(err)
	}
	c := capsOf(t, d)
	f, _ := c.Caps()
	return c, f
}

func TestAnExecutedActCountsAtItsSealedSpend(t *testing.T) {
	e := refundVectorEngine(t, 1000)
	accept := "accept"
	purchase := Record{CapsuleID: "1", ActionID: "deal-1/6", Operator: "household-a", Developer: "d", Timestamp: "2026-10-01T10:00:00Z",
		Decision: &accept, AmountMinor: new(int64(300)), ActionClass: new("money.purchase")}
	for name, tc := range map[string]struct {
		acts   []Record
		weekly int64
		result Result
	}{
		"spend 500, under":       {[]Record{purchase, executedAct("2", new(int64(500)))}, 800, Pass},
		"spend 0":                {[]Record{purchase, executedAct("2", new(int64(0)))}, 300, Pass},
		"spend 600, over":        {[]Record{purchase, executedAct("2", new(int64(600)))}, 900, Fail},
		"spend, not amount":      {[]Record{executedAct("2", new(int64(450)))}, 450, Pass},
		"two executed":           {[]Record{executedAct("2", new(int64(300))), executedAct("3", new(int64(300)))}, 600, Pass},
		"executed, no direction": {[]Record{func() Record { r := executedAct("2", new(int64(500))); r.Direction = nil; return r }()}, 500, Pass},
	} {
		c, f := capsWindow(t, e, tc.acts)
		if c.Result != tc.result || c.Pending() != "" || f.WeeklySpendMinor != tc.weekly || f.ProjectedMinor != tc.weekly+200 {
			t.Errorf("%s: caps %s %q weekly %d projected %d; want %s, evaluated, %d", name, c.Result, c.Pending(),
				f.WeeklySpendMinor, f.ProjectedMinor, tc.result, tc.weekly)
		}
		if f.ExcludedFromWindow != nil || f.UncheckedInWindow != nil {
			t.Errorf("%s: excluded %+v unchecked %+v, want none", name, f.ExcludedFromWindow, f.UncheckedInWindow)
		}
		if x := f.ExecutedInWindow; x == nil || x.Minor != tc.weekly-sumAccepted(tc.acts) {
			t.Errorf("%s: executed in window %+v", name, x)
		}
	}
}

func sumAccepted(acts []Record) int64 {
	var n int64
	for _, r := range acts {
		if r.accepted() {
			n += *r.AmountMinor
		}
	}
	return n
}

// Outside the window, an executed act is not counted, as the fold counts
// no accepted act outside it.
func TestAnExecutedActOutsideTheWindowIsNotCounted(t *testing.T) {
	e := refundVectorEngine(t, 1000)
	old := executedAct("2", new(int64(900)))
	old.Timestamp = "2026-09-20T10:00:00Z"
	c, f := capsWindow(t, e, []Record{old})
	if c.Result != Pass || c.Pending() != "" || f.WeeklySpendMinor != 0 || f.ExecutedInWindow != nil {
		t.Errorf("caps %s %q weekly %d executed %+v; want pass at 0, none counted", c.Result, c.Pending(), f.WeeklySpendMinor, f.ExecutedInWindow)
	}
}

// An executed act whose spend cannot be read still leaves the window
// unevaluated, with the reason that says only what is known. Under the
// shipped spend.weekly/3.1.0 the reason and the sealed n/a are caps.py's
// (WindowUnreadable, naming spend_minor).
func TestAnExecutedActWithNoReadableSpendStillBlocksTheWindow(t *testing.T) {
	noAmount := executedAct("2", nil)
	noAmount.AmountMinor = nil
	negative := executedAct("2", new(int64(-1)))
	for _, v := range unreadableWindowPacks(t) {
		for name, r := range map[string]Record{"no spend_minor": executedAct("2", nil), "no amount at all": noAmount, "negative spend": negative} {
			c, _ := capsWindow(t, v.engine, []Record{r})
			digest, _, _ := c.EvidenceDigest()
			if c.Result != NotApplicable || c.Pending() != v.reason || digest != v.evidence {
				t.Errorf("%s %s: caps %s %q %s; want not evaluable, %q %s", v.fold, name, c.Result, c.Pending(), digest, v.reason, v.evidence)
			}
		}
	}
}

// unreadableWindowPacks are the two everyday packs' engines with the
// reason and sealed evidence each gives a window an unreadable act holds:
// the frozen 0.3.5's own (spend.weekly/3.0.0), and the shipped 0.3.8's (0.3.6's caps),
// which are caps.py _window_unreadable's.
func unreadableWindowPacks(t *testing.T) []struct {
	engine                 *Engine
	fold, reason, evidence string
} {
	t.Helper()
	digest := func(field string) string {
		d, err := canon.Digest(notApplicableEvidence("caps", true, field))
		if err != nil {
			t.Fatal(err)
		}
		return d
	}
	return []struct {
		engine                 *Engine
		fold, reason, evidence string
	}{
		{refundVectorEngine(t, 1000), "spend.weekly/3.0.0",
			"1 earlier act(s) carry no recorded decision; the 7d total as the engine computes it counts accepted acts only, so it cannot be computed", digest("")},
		{refundVectorEngineOf(t, 1000, pack.LoadEveryday038, "spend.weekly/3.1.0"), "spend.weekly/3.1.0", WindowUnreadable, digest("spend_minor")},
	}
}

// Rule 5 is window-only (the ruling on the G3 vectors, 2026-10-09): an
// act whose spend cannot be read holds the window only when it is one the
// window would count, in [check - 7d, check] (both ends included) under the
// check's fold key. One outside cannot change the 7-day total, so it does not
// hold it: the window no longer goes dark for the 31 days of history.
func TestAnUnreadableActHoldsTheWindowOnlyInsideIt(t *testing.T) {
	for _, v := range unreadableWindowPacks(t) {
		testUnreadableActHoldsTheWindowOnlyInsideIt(t, v.engine, v.reason)
	}
}

func testUnreadableActHoldsTheWindowOnlyInsideIt(t *testing.T, e *Engine, want string) {
	at := func(ts, operator string) Record {
		r := executedAct("2", nil)
		r.Timestamp, r.Operator = ts, operator
		return r
	}
	for name, tc := range map[string]struct {
		act  Record
		held bool
	}{
		"inside":                   {at("2026-10-02T10:00:00Z", "household-a"), true},
		"at the window's start":    {at("2026-09-26T10:00:00Z", "household-a"), true},
		"at the check":             {at("2026-10-03T10:00:00Z", "household-a"), true},
		"a second before it":       {at("2026-09-26T09:59:59Z", "household-a"), false},
		"20 days before":           {at("2026-09-13T10:00:00Z", "household-a"), false},
		"after the check":          {at("2026-10-03T10:00:01Z", "household-a"), false},
		"inside, another operator": {at("2026-10-02T10:00:00Z", "household-b"), false},
	} {
		c, f := capsWindow(t, e, []Record{tc.act})
		switch {
		case tc.held && (c.Result != NotApplicable || c.Pending() != want):
			t.Errorf("%s: caps %s %q; want not evaluable, %q", name, c.Result, c.Pending(), want)
		case !tc.held && (c.Result != Pass || c.Pending() != "" || f.WeeklySpendMinor != 0):
			t.Errorf("%s: caps %s %q weekly %d; want pass at 0, not held", name, c.Result, c.Pending(), f.WeeklySpendMinor)
		}
	}
}

// Money in counts 0 and is named excluded, as before (#130).
func TestAnExecutedMoneyInActStillCountsNothing(t *testing.T) {
	e := refundVectorEngine(t, 1000)
	in := executedAct("2", new(int64(700)))
	in.Direction = new("in")
	c, f := capsWindow(t, e, []Record{in, executedAct("3", new(int64(100)))})
	if c.Result != Pass || c.Pending() != "" || f.WeeklySpendMinor != 100 {
		t.Errorf("caps %s %q weekly %d; want pass at 100", c.Result, c.Pending(), f.WeeklySpendMinor)
	}
	if x := f.ExcludedFromWindow; x == nil || x.Count != 1 {
		t.Errorf("excluded %+v, want one money-in act", x)
	}
}

// An act the guard accepted is counted once: an fyi record of the same act
// (the same capsule, or the same action_id) beside the accepted one adds
// nothing, nor does the same executed or unchecked capsule given twice.
func TestAnAcceptedActIsCountedOnce(t *testing.T) {
	e := refundVectorEngine(t, 10000)
	accept := "accept"
	paid := Record{CapsuleID: "A", ActionID: "deal-a/6", ActionType: "decide", Operator: "household-a", Developer: "d",
		Timestamp: "2026-10-01T10:00:00Z", Decision: &accept, AmountMinor: new(int64(2000)), SpendMinor: new(int64(2000)),
		ActionClass: new("money.purchase"), Direction: new("out")}
	sameAction := executedAct("B", new(int64(2000)))
	sameAction.ActionID = paid.ActionID
	sameCapsule := executedAct("A", new(int64(2000)))
	sameCapsule.ActionID = ""
	unchecked := uncheckedAct(1000, "payee/one")
	unchecked.Timestamp = "2026-10-02T10:00:00Z"
	uncheckedTwin := unchecked
	uncheckedTwin.ActionID = paid.ActionID
	for name, tc := range map[string]struct {
		acts   []Record
		weekly int64
	}{
		"accepted + fyi, same action_id":  {[]Record{paid, sameAction}, 2000},
		"fyi first, then accepted":        {[]Record{sameAction, paid}, 2000},
		"accepted + fyi, same capsule":    {[]Record{paid, sameCapsule}, 2000},
		"one executed, given twice":       {[]Record{executedAct("C", new(int64(700))), executedAct("C", new(int64(700)))}, 700},
		"one unchecked, given twice":      {[]Record{unchecked, unchecked}, 1000},
		"accepted + unchecked, same act":  {[]Record{paid, uncheckedTwin}, 2000},
		"accepted + a different executed": {[]Record{paid, executedAct("D", new(int64(700)))}, 2700},
	} {
		c, f := capsWindow(t, e, tc.acts)
		if c.Pending() != "" || f.WeeklySpendMinor != tc.weekly {
			t.Errorf("%s: caps %s %q weekly %d; want %d", name, c.Result, c.Pending(), f.WeeklySpendMinor, tc.weekly)
		}
	}
}

// A real checked fyi act (capsulectl rc14, purchase commit) decodes with
// its sealed spend, and one whose spend_minor is in the wrong shape decodes
// with none, so it holds the window rather than refusing the history.
func TestAnExecutedActsSpendIsReadOrMarkedUnreadable(t *testing.T) {
	raw := realFyiAct(t)
	r, err := DecodeHistoryAct(raw)
	if err != nil {
		t.Fatal(err)
	}
	if r.Decision != nil || r.Unchecked || r.SpendMinor == nil || *r.SpendMinor != 2000 {
		t.Fatalf("decision %v unchecked %v spend %v; want an undecided act of 2000", r.Decision, r.Unchecked, r.SpendMinor)
	}
	for name, body := range map[string]bodyEdits{
		"spend_minor a string":  {"spend_minor": json.RawMessage(`"2000"`)},
		"spend_minor an object": {"spend_minor": json.RawMessage(`{"minor":2000}`)},
		"no amount_minor":       {"amount_minor": nil},
		"amount_minor a string": {"amount_minor": json.RawMessage(`"2000"`)},
	} {
		got, err := DecodeHistoryAct(withBody(t, raw, body))
		if err != nil {
			t.Errorf("%s: refused (%v); want decoded and marked unreadable", name, err)
			continue
		}
		if _, ok := got.executedSpend(); ok && name != "no amount_minor" && name != "amount_minor a string" {
			t.Errorf("%s: spend read as %v", name, got.SpendMinor)
		}
	}
	// A decided act with no amount is still refused: the fold would skip it.
	if _, err := DecodeHistoryAct(withMember(t, withBody(t, raw, bodyEdits{"amount_minor": nil}), "disposition",
		json.RawMessage(`{"decision":"accept"}`))); err == nil {
		t.Error("a decided act with no amount_minor decoded")
	}
}

// realFyiAct is the checked purchase commit capsulectl rc14 sealed fyi, as
// it sent it in history (testdata/real-fyi-act).
func realFyiAct(t *testing.T) json.RawMessage {
	t.Helper()
	in := decodeJSON[struct {
		InputHistory []json.RawMessage `json:"history"`
	}](t, readFile(t, "testdata", "real-fyi-act", "purchase-commit-repeat.input.json"))
	for _, raw := range in.InputHistory {
		var h struct {
			ActionType string `json:"action_type"`
		}
		if json.Unmarshal(raw, &h) == nil && h.ActionType == "fyi" {
			return raw
		}
	}
	t.Fatal("no fyi act in the fixture's history")
	return nil
}

// bodyEdits are deal body members to set, by name, each as its sealed JSON;
// a nil value removes the member.
type bodyEdits map[string]json.RawMessage

// withBody is raw with its agent_input body members set (bodyEdits),
// resealed: the header's agent_input_digest is recomputed over the new body,
// so the decoder reads it as sealed.
func withBody(t *testing.T, raw json.RawMessage, set bodyEdits) json.RawMessage {
	t.Helper()
	var header, agentInput, body map[string]json.RawMessage
	if err := json.Unmarshal(raw, &header); err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(header["agent_input"], &agentInput); err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(agentInput["body"], &body); err != nil {
		t.Fatal(err)
	}
	for k, v := range set {
		if v == nil {
			delete(body, k)
			continue
		}
		body[k] = v
	}
	agentInput["body"] = marshalled(t, body)
	ai := marshalled(t, agentInput)
	header["agent_input"] = ai
	v, err := canon.FromJSON(ai)
	if err != nil {
		t.Fatal(err)
	}
	d, err := canon.Digest(v)
	if err != nil {
		t.Fatal(err)
	}
	header["model_attestation"] = json.RawMessage(`{"compute_attestation":{"agent_input_digest":"` + d + `"}}`)
	return marshalled(t, header)
}

// marshalled is m as JSON.
func marshalled(t *testing.T, m map[string]json.RawMessage) json.RawMessage {
	t.Helper()
	out, err := json.Marshal(m)
	if err != nil {
		t.Fatal(err)
	}
	return out
}

// The spend counted is the act's own sealed record's (point 2 of the ruling on the
// G3 vectors): history[i].agent_input.body.spend_minor of the action record,
// never its check's, and not its amount_minor.
func TestAnExecutedActsSpendIsItsOwnSealedSpend(t *testing.T) {
	r, err := DecodeHistoryAct(withBody(t, realFyiAct(t), bodyEdits{"spend_minor": json.RawMessage(`1700`), "amount_minor": json.RawMessage(`2000`)}))
	if err != nil {
		t.Fatal(err)
	}
	if spend, ok := r.executedSpend(); !ok || spend != 1700 {
		t.Errorf("spend %d (%v); want the act's sealed 1700", spend, ok)
	}
}

// Under the shipped spend.weekly/3.1.0 an executed act that moved money in
// (here a purchase), and one classed as a cancel or a refund, add nothing to
// the total (the
// engine's record of it is 0, or a reversal citing no charge: unlinked), so
// neither is reported among the executed acts the window counted; an
// ordinary executed act still is.
func TestUnder031NoSpendActsAreNotCountedExecutedActs(t *testing.T) {
	e := refundVectorEngineOf(t, 1000, pack.LoadEveryday038, "spend.weekly/3.1.0")
	in := executedAct("2", new(int64(0)))
	in.Direction = new(moneyIn)
	cancel := executedAct("3", new(int64(700)))
	cancel.ActionClass = new("booking.cancel")
	c, f := capsWindow(t, e, []Record{in, cancel})
	if c.Result != Pass || f.WeeklySpendMinor != 0 || f.ExecutedInWindow != nil {
		t.Errorf("caps %s weekly %d executed %+v; want pass at 0, none counted", c.Result, f.WeeklySpendMinor, f.ExecutedInWindow)
	}
	c, f = capsWindow(t, e, []Record{in, cancel, executedAct("4", new(int64(300)))})
	if c.Result != Pass || f.WeeklySpendMinor != 300 || f.ExecutedInWindow == nil || *f.ExecutedInWindow != (ActsInWindow{Count: 1, Minor: 300}) {
		t.Errorf("caps %s weekly %d executed %+v; want pass at 300, one counted", c.Result, f.WeeklySpendMinor, f.ExecutedInWindow)
	}
}
