package admission

import (
	"testing"
	"time"
)

var testAccept = map[string]Unit{
	"msat": {Band: "sat", Scale: -3},
	"sat":  {Band: "sat", Scale: 0},
	"USDC": {Band: "USD", Scale: 0},
	"USD":  {Band: "USD", Scale: 0},
}

func TestValueBand(t *testing.T) {
	cases := []struct {
		amount, currency string
		want             Band
	}{
		{"5", "sat", Band{"sat", 0}},
		{"9", "sat", Band{"sat", 0}},
		{"10", "sat", Band{"sat", 1}},
		{"1000", "sat", Band{"sat", 3}},
		{"9999", "sat", Band{"sat", 3}},
		{"1000000", "msat", Band{"sat", 3}},
		{"999", "msat", Band{"sat", -1}},
		{"1", "msat", Band{"sat", -3}},
		{"0.25", "USDC", Band{"USD", -1}},
		{"0.0099", "USD", Band{"USD", -3}},
		{"1.0", "USD", Band{"USD", 0}},
		{"12.50", "USD", Band{"USD", 1}},
		{"100", "USD", Band{"USD", 2}},
	}
	for _, c := range cases {
		got, ok, err := ValueBand(c.amount, c.currency, testAccept)
		if err != nil || !ok || got != c.want {
			t.Errorf("ValueBand(%q, %q) = %v, %v, %v; want %v", c.amount, c.currency, got, ok, err, c.want)
		}
	}
}

func TestValueBandZeroHasNoBand(t *testing.T) {
	for _, a := range []string{"0", "0.0", "0.000"} {
		_, ok, err := ValueBand(a, "USD", testAccept)
		if ok || err != nil {
			t.Errorf("ValueBand(%q) = ok %v, err %v; want no band, no error", a, ok, err)
		}
	}
}

func TestValueBandRefuses(t *testing.T) {
	cases := []struct{ amount, currency string }{
		{"10", "EUR"},  // not in the accept table
		{"-1", "USD"},  // signed
		{"1e3", "USD"}, // exponent
		{"01", "USD"},  // redundant leading zero
		{".5", "USD"},  // no integer part
		{"5.", "USD"},  // empty fraction
		{"", "USD"},
		{" 5", "USD"},
	}
	for _, c := range cases {
		if _, _, err := ValueBand(c.amount, c.currency, testAccept); err == nil {
			t.Errorf("ValueBand(%q, %q): want an error", c.amount, c.currency)
		}
	}
}

func TestFresh(t *testing.T) {
	now := time.Date(2026, 10, 30, 12, 0, 0, 0, time.UTC)
	day := 24 * time.Hour
	if !Fresh(now.Add(-day), now, day) {
		t.Error("a checkpoint exactly maxAge old is fresh")
	}
	if Fresh(now.Add(-day-time.Second), now, day) {
		t.Error("a checkpoint past maxAge is stale")
	}
	if Fresh(now.Add(time.Second), now, day) {
		t.Error("a checkpoint stated after now is not fresh")
	}
}
