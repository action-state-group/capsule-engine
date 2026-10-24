package guard

import (
	"testing"
	"time"

	"github.com/action-state-group/capsule-engine/go/pack"
)

// The edge cases of historyFreed (capsule-engine#234) the shared vectors do
// not reach; python/tests/test_history_reversal.py holds the same cases.
func TestHistoryFreed(t *testing.T) {
	f := fold{
		filters:  []foldFilter{{field: "disposition.decision", op: "in", values: []value{{kind: str, s: "accept"}, {kind: str, s: executedDecision}}}},
		reversal: &pack.FoldReversal{Classes: []string{"booking.cancel", "money.refund"}, Relation: "supersedes"},
	}
	anchor := time.Date(2026, 10, 7, 21, 40, 0, 0, time.UTC)
	const charged = "c"
	accept, class, parent, relation := "accept", "money.purchase", charged, "supersedes"
	refundClass, cancelClass, in := "money.refund", "booking.cancel", moneyIn
	n := func(v int64) *int64 { return &v }
	charge := func(amount int64, spend *int64, decision *string) Record {
		return Record{CapsuleID: charged, ActionID: "a/c", Operator: "deal", Developer: "d", Timestamp: "2026-10-06T09:00:00Z",
			Decision: decision, AmountMinor: n(amount), SpendMinor: spend, ActionClass: &class, live: liveDisposition}
	}
	reversal := func(id string, returned *int64, mut func(*Record)) Record {
		r := Record{CapsuleID: id, ActionID: "a/" + id, Operator: "deal", Developer: "d", Timestamp: "2026-10-06T12:00:00Z",
			Decision: &accept, AmountMinor: n(0), SpendMinor: n(0), ActionClass: &refundClass, Direction: &in,
			ReturnedMinor: returned, ChainParent: &parent, ChainRelation: &relation, live: liveDisposition}
		if mut != nil {
			mut(&r)
		}
		return r
	}
	tests := []struct {
		name    string
		records []Record
		want    int64
	}{
		{"one refund", []Record{charge(9000, n(3000), &accept), reversal("r1", n(2000), nil)}, 2000},
		{"refunds add up and stop at the spend", []Record{charge(9000, n(3000), &accept), reversal("r1", n(4000), nil),
			reversal("r2", n(4000), func(r *Record) { r.Timestamp = "2026-10-06T13:00:00Z" })}, 6000},
		{"the same refund twice", []Record{charge(9000, n(3000), &accept), reversal("r1", n(2000), nil), reversal("r1", n(2000), nil)}, 2000},
		{"a charge that spent its amount", []Record{charge(9000, n(9000), &accept), reversal("r1", n(9000), nil)}, 0},
		{"a charge with no spend", []Record{charge(9000, nil, &accept), reversal("r1", n(9000), nil)}, 0},
		{"an unaccepted charge", []Record{charge(9000, n(3000), nil), reversal("r1", n(9000), nil)}, 0},
		{"another operator", []Record{charge(9000, n(3000), &accept), reversal("r1", n(2000), func(r *Record) { r.Operator = "other" })}, 0},
		{"after the check", []Record{charge(9000, n(3000), &accept),
			reversal("r1", n(2000), func(r *Record) { r.Timestamp = "2026-10-08T00:00:00Z" })}, 0},
		{"before the charge", []Record{charge(9000, n(3000), &accept),
			reversal("r1", n(2000), func(r *Record) { r.Timestamp = "2026-10-05T00:00:00Z" })}, 0},
		{"a cancel", []Record{charge(9000, n(3000), &accept), reversal("r1", n(1500), func(r *Record) { r.ActionClass = &cancelClass })}, 1500},
		{"no returned amount", []Record{charge(9000, n(3000), &accept), reversal("r1", nil, nil)}, 0},
		{"the same action id", []Record{charge(9000, n(3000), &accept), reversal("r1", n(2000), nil),
			reversal("r2", n(2000), func(r *Record) { r.ActionID = "a/r1" })}, 2000},
		{"another developer", []Record{charge(9000, n(3000), &accept), reversal("r1", n(2000), func(r *Record) { r.Developer = "other" })}, 0},
		{"sealed at the charge's instant", []Record{charge(9000, n(3000), &accept),
			reversal("r1", n(2000), func(r *Record) { r.Timestamp = "2026-10-06T09:00:00Z" })}, 2000},
		{"sealed at the check's instant", []Record{charge(9000, n(3000), &accept),
			reversal("r1", n(2000), func(r *Record) { r.Timestamp = "2026-10-07T21:40:00Z" })}, 2000},
		{"listed before a charge of the same instant", []Record{
			reversal("r1", n(2000), func(r *Record) { r.Timestamp = "2026-10-06T09:00:00Z" }), charge(9000, n(3000), &accept)}, 2000},
		{"a claim is not a reversal", []Record{charge(9000, n(3000), &accept),
			reversal("r1", n(2000), func(r *Record) { r.Stated = &Statement{} })}, 0},
		{"only the first copy of a capsule id is the charge", []Record{charge(500, n(500), &accept), charge(9000, n(3000), &accept),
			reversal("r1", n(2000), nil)}, 0},
		{"no chain", []Record{charge(9000, n(3000), &accept), reversal("r1", n(2000), func(r *Record) { r.ChainParent, r.ChainRelation = nil, nil })}, 0},
		{"money not moved in", []Record{charge(9000, n(3000), &accept), reversal("r1", n(2000), func(r *Record) { r.Direction = nil })}, 0},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			var got int64
			for _, v := range f.historyFreed(tc.records, anchor) {
				got += v
			}
			if got != tc.want {
				t.Errorf("freed %d, want %d", got, tc.want)
			}
		})
	}
	if got := (fold{reversal: f.reversal}).historyFreed([]Record{charge(9000, n(3000), &accept), reversal("r1", n(2000), nil)}, anchor); len(got) != 0 {
		t.Errorf("a fold that does not count executed acts frees %v, want nothing", got)
	}
}
