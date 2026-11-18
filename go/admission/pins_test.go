package admission

import (
	"os"
	"path/filepath"
	"testing"
	"time"
)

// The judgements of a stapled history card (capsule-engine#304): the shared
// vectors pin each decision; these read the rows, which the verdict hides.

func pinsPack(t *testing.T) *Pack {
	t.Helper()
	src, err := os.ReadFile(filepath.Join(vectorDir, "packs", "pins.json"))
	if err != nil {
		t.Fatal(err)
	}
	p, err := Load(src)
	if err != nil {
		t.Fatal(err)
	}
	return p
}

func yes() *bool { b := true; return &b }

func pinsInputs(card Card) Inputs {
	card.State = CardVerified
	return Inputs{
		Now: time.Date(2026, 10, 30, 12, 0, 0, 0, time.UTC), Amount: "50", Currency: "USD", Card: card,
		Pin:     &Pin{LogSize: 19, Digest: "a"},
		Streams: &Streams{Pinned: 2},
	}
}

func rowOf(t *testing.T, d Decision, id string) RowResult {
	t.Helper()
	for _, r := range d.Rows {
		if r.Row == id {
			return r
		}
	}
	t.Fatalf("no row %s", id)
	return RowResult{}
}

func TestWitnessLabel(t *testing.T) {
	p := pinsPack(t)
	for _, c := range []struct {
		name  string
		card  Card
		label string
	}{
		{"a counted receipt under a configured witness", Card{Witness: "witness-a"}, LabelWitnessed},
		{"a counted receipt on an earlier checkpoint only", Card{Receipts: &Receipts{Counted: 1, Presented: 1, Checkpoints: 2}}, LabelWitnessed},
		{"no receipt", Card{}, LabelUnwitnessed},
		{"no receipt presented, receipts counted none", Card{Receipts: &Receipts{Checkpoints: 2}}, LabelUnwitnessed},
		{"receipts presented and none counted", Card{Receipts: &Receipts{Presented: 2, Checkpoints: 2}}, LabelUnknownWitness},
		{"a witness the pack does not configure", Card{Witness: "witness-x"}, LabelUnknownWitness},
	} {
		d := Evaluate(p, pinsInputs(c.card))
		if got := rowOf(t, d, "label").Label; got != c.label {
			t.Errorf("%s: label %q, want %q", c.name, got, c.label)
		}
	}
	// A card not verified reads no label, and the block carries none.
	in := pinsInputs(Card{})
	in.Card.State = CardUnverifiable
	if r := rowOf(t, Evaluate(p, in), "label"); r.Result != NotRead || r.Label != "" {
		t.Errorf("unverified card: %+v", r)
	}
}

func TestBehindPin(t *testing.T) {
	p := pinsPack(t)
	cp := func(n int64, d string) []Checkpoint { return []Checkpoint{{LogSize: n, Digest: d}} }
	for _, c := range []struct {
		name string
		cps  []Checkpoint
		want string
	}{
		{"ends at the pin", cp(19, "a"), Pass},
		{"past the pin", []Checkpoint{{LogSize: 19, Digest: "a"}, {LogSize: 25, Digest: "b"}}, Pass},
		{"past the pin, pin size not shown", cp(25, "b"), Pass},
		{"earlier than the pin", cp(14, "c"), Fail},
		{"another digest at the pin's size", cp(19, "c"), Fail},
		{"shows no checkpoint", nil, NotRead},
	} {
		in := pinsInputs(Card{Witness: "witness-a", LogKeyAccepted: yes(), Checkpoints: c.cps})
		if got := rowOf(t, Evaluate(p, in), "pin").Result; got != c.want {
			t.Errorf("%s: %s, want %s", c.name, got, c.want)
		}
	}
	in := pinsInputs(Card{Checkpoints: cp(14, "c")})
	in.Pin = nil
	if got := rowOf(t, Evaluate(p, in), "pin").Result; got != NotRead {
		t.Errorf("no pin: %s, want not_read", got)
	}
}

func TestDroppedStreams(t *testing.T) {
	p := pinsPack(t)
	in := pinsInputs(Card{})
	in.Streams = &Streams{Pinned: 2, Dropped: 1}
	r := rowOf(t, Evaluate(p, in), "streams")
	if r.Result != Fail || *r.Count != 1 || *r.Denominator != 2 {
		t.Errorf("one of two pinned streams dropped: %+v", r)
	}
	in.Streams = nil
	if r := rowOf(t, Evaluate(p, in), "streams"); r.Result != NotRead {
		t.Errorf("no commitment read: %+v", r)
	}
}

func TestLogKeyAccepted(t *testing.T) {
	p := pinsPack(t)
	no := false
	for _, c := range []struct {
		name string
		v    *bool
		want string
	}{{"accepted", yes(), Pass}, {"refused", &no, Fail}, {"undecided", nil, NotRead}} {
		got := rowOf(t, Evaluate(p, pinsInputs(Card{LogKeyAccepted: c.v})), "log-key").Result
		if got != c.want {
			t.Errorf("%s: %s, want %s", c.name, got, c.want)
		}
	}
}
