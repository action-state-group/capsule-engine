package guard

import (
	"encoding/json"
	"strings"
	"testing"
)

// saleRecord is a history act of operator "op" in sale item, of class
// class, with the state and sealed decision given.
func saleRecord(id, at, class, item, deal string, live liveState, sealed string) Record {
	r := Record{CapsuleID: id, Operator: "op", Timestamp: at, ActionClass: &class, ItemRef: &item, DealID: &deal, live: live}
	if sealed != "" {
		r.sealed = &sealed
	}
	return r
}

var (
	saleItem     = strings.Repeat("7c", 32)
	saleTaskAuth = strings.Repeat("a", 64)
)

// saleCommit is a commitment in sale saleItem, in deal.
func saleCommit(deal string) Action {
	class := "agreement.accept"
	return Action{Verb: "commit", Operator: "op", Developer: "dev", ActionClass: &class, ActionID: deal + "/9",
		ActionType: "fyi", Timestamp: "2026-10-10T00:50:00Z", TaskAuthorityRef: &saleTaskAuth, ItemRef: &saleItem, DealID: &deal}
}

// single_commitment reads a check input's history oldest first, as the
// engine's history ledger writes it (capsulectl sends it newest first): the
// sale's acceptance is the earliest accepted one. Its decision is the one
// its capsule seals (a history act naming two deals is read reject for seen
// and spend, but sealed accept is what the engine's builder writes). An
// unread act of the sale (one taken without authority) leaves the sale's
// acceptance unknown, and the check refuses.
func TestTheSalesAcceptanceIsReadAsTheHistoryLedgerWritesIt(t *testing.T) {
	e, _ := seller013Engine(t)
	live := checkInputs{history: true}
	older := saleRecord("older", "2026-10-10T00:41:00Z", "agreement.accept", saleItem, "deal-a", liveDisposition, "accept")
	newer := saleRecord("newer", "2026-10-10T00:45:00Z", "agreement.accept", saleItem, "deal-b", liveDisposition, "accept")
	reject := "reject"
	conflicted := older
	conflicted.Decision = &reject
	unchecked := saleRecord("unchecked", "2026-10-10T00:40:00Z", "agreement.accept", saleItem, "deal-c", liveUnread, "")
	unchecked.Unchecked = true
	for _, c := range []struct {
		name    string
		ledger  []Record
		deal    string
		result  Result
		missing string
	}{
		{"oldest first: deal-a's acceptance, followed in deal-a", []Record{newer, older}, "deal-a", Pass, ""},
		{"oldest first: deal-a's acceptance, not followed in deal-b", []Record{newer, older}, "deal-b", Fail, ""},
		{"the sealed accept counts, whatever is read for spend", []Record{conflicted}, "deal-z", Fail, ""},
		{"an unread act of the sale", []Record{unchecked, older}, "deal-a", NotApplicable, "history"},
	} {
		got, err := e.constraintFor("single_commitment", saleCommit(c.deal), c.ledger, live)
		if err != nil {
			t.Fatal(err)
		}
		if got.Result != c.result || got.missing != c.missing || got.failsClosed != (c.missing != "") {
			t.Errorf("%s: %s %q fails closed %v", c.name, got.Result, got.missing, got.failsClosed)
		}
	}
}

// A real unchecked act (capsulectl's typed action-outcome/v0) is unread in
// the history ledger: the engine reads only a typed act record as an act.
func TestARealUncheckedActIsUnreadForTheSale(t *testing.T) {
	for _, raw := range uncheckedHistory(t, "typed-check-2") {
		r, err := DecodeHistoryAct(raw)
		if err != nil {
			t.Fatal(err)
		}
		if r.live != liveUnread {
			t.Errorf("%s: %q", r.CapsuleID, r.live)
		}
	}
}

// replay.py _basis_task_authority: the one task_authority basis entry's
// digest; none, or two, name no task authority.
func TestABasisNamesOneTaskAuthority(t *testing.T) {
	ref := func(d string) string {
		return `{"type":"task_authority","ref":{"type":"deal-record","digest_alg":"SHA-256","digest":"` + d + `"}}`
	}
	other := `{"type":"approval","ref":{"type":"deal-record","digest_alg":"SHA-256","digest":"` + strings.Repeat("b", 64) + `"}}`
	for _, c := range []struct {
		basis string
		want  string
	}{
		{"[" + ref(saleTaskAuth) + "," + other + "]", saleTaskAuth},
		{"[" + ref(saleTaskAuth) + "," + ref(strings.Repeat("c", 64)) + "]", ""},
		{"[" + other + "]", ""},
		{"[" + ref("NOTHEX") + "]", ""},
	} {
		got := basisTaskAuthority(json.RawMessage(`{"body":{"authority_basis":` + c.basis + `}}`))
		if (got == nil) != (c.want == "") || got != nil && *got != c.want {
			t.Errorf("%s: %v", c.basis, got)
		}
	}
}

// A commit refused only because single_commitment fails closed is
// not_evaluable in the report, and refused (the engine seals a reject;
// check --emit says deny): a-commit-1900 with its thread's claim in the
// history passes every other rule, and here names no item.
func TestARefusalOnlyByAFailClosedCheckIsNotEvaluable(t *testing.T) {
	raw := readFile(t, seller013Vectors, "go-vectors", "check-inputs", "a-commit-1900.claims-in-history.json")
	var env map[string]json.RawMessage
	if err := json.Unmarshal(raw, &env); err != nil {
		t.Fatal(err)
	}
	delete(env, itemRefInput)
	raw, err := json.Marshal(env)
	if err != nil {
		t.Fatal(err)
	}
	a, h, err := DecodeCheckInput(raw)
	if err != nil {
		t.Fatal(err)
	}
	e, _ := seller013Engine(t)
	d, err := e.CheckWithHistory(a, h)
	if err != nil {
		t.Fatal(err)
	}
	rep := e.Report(a, d)
	if d.Outcome != Deny || d.anyFailed() || rep.Decision != VerdictNotEvaluable || !rep.Refused || rep.RefusedFor != RefusedNotEvaluated {
		t.Errorf("outcome %s, failed %v, report %s refused %v for %q", d.Outcome, d.anyFailed(), rep.Decision, rep.Refused, rep.RefusedFor)
	}
}

// release_on_acceptance reads the sale's acceptance as single_commitment
// does: one a live history leaves unknown releases nothing.
func TestAnUnknownAcceptanceReleasesNothing(t *testing.T) {
	e, _ := seller013Engine(t)
	str := func(s string) *string { return &s }
	acceptance := saleRecord(strings.Repeat("b", 64), "2026-10-10T00:41:00Z", "agreement.accept", saleItem, "deal-a", liveDisposition, "accept")
	acceptance.Target = str("cp/a")
	share := Action{Verb: "share_address", Operator: "op", Developer: "dev", ActionClass: str("disclosure.personal"), ActionID: "share/1",
		ActionType: "decide", Timestamp: "2026-10-10T00:50:00Z", Target: str("cp/a"), TaskAuthorityRef: &saleTaskAuth,
		ItemRef: &saleItem, RepresentationClass: str("address"), CitedMandateCapsuleID: str(strings.Repeat("b", 64))}
	for _, c := range []struct {
		in   checkInputs
		want Result
	}{{checkInputs{history: true}, NotApplicable}, {checkInputs{history: true, historyIncomplete: true}, Fail}} {
		got, err := e.constraintFor("release_on_acceptance", share, []Record{acceptance}, c.in)
		if err != nil {
			t.Fatal(err)
		}
		if got.Result != c.want {
			t.Errorf("incomplete %v: %s %q %q", c.in.historyIncomplete, got.Result, got.failNote, got.Pending())
		}
	}
}
