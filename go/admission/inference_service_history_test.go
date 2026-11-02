package admission

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
	"time"
)

// vectorPeriod is a served period as the shared vectors write it.
type vectorPeriod struct {
	From       string `json:"from"`
	To         string `json:"to"`
	Total      *int64 `json:"total"`
	Suppressed string `json:"suppressed,omitempty"`
}

type vectorServed struct {
	LengthS int64          `json:"length_s"`
	Floor   int64          `json:"floor"`
	Periods []vectorPeriod `json:"periods"`
	// Verdicts is absent when the answer counts no referee verdicts.
	Verdicts     *[]vectorPeriod `json:"verdicts,omitempty"`
	VerdictsOnly bool            `json:"verdicts_only,omitempty"`
}

func toVectorServed(s *Served) *vectorServed {
	if s == nil {
		return nil
	}
	out := &vectorServed{LengthS: s.LengthS, Floor: s.Floor, Periods: []vectorPeriod{}}
	for _, p := range s.Periods {
		out.Periods = append(out.Periods, vectorPeriod{From: p.From.UTC().Format(time.RFC3339),
			To: p.To.UTC().Format(time.RFC3339), Total: p.Total, Suppressed: p.Suppressed})
	}
	if s.Verdicts != nil {
		vs := []vectorPeriod{}
		for _, p := range s.Verdicts {
			vs = append(vs, vectorPeriod{From: p.From.UTC().Format(time.RFC3339),
				To: p.To.UTC().Format(time.RFC3339), Total: p.Total, Suppressed: p.Suppressed})
		}
		out.Verdicts = &vs
	}
	out.VerdictsOnly = s.VerdictsOnly
	return out
}

// The shared inference-service-history/0 vectors
// (conformance-vectors/admission/v0/inference-service-history.json), which
// the Rust port reads too.
func TestInferenceServiceHistoryVectors(t *testing.T) {
	raw, err := os.ReadFile(filepath.Join(vectorDir, "inference-service-history.json"))
	if err != nil {
		t.Fatal(err)
	}
	var set struct {
		Cases []struct {
			Case       string `json:"case"`
			Definition string `json:"definition"`
			Artifact   string `json:"artifact"`
			Card       struct {
				Checkpoints []struct {
					At      string `json:"at"`
					Records int64  `json:"records"`
				} `json:"checkpoints"`
			} `json:"card"`
			Pin      string          `json:"pin"`
			Want     string          `json:"want"`
			Property string          `json:"property"`
			Expected json.RawMessage `json:"expected"`
		} `json:"cases"`
	}
	if err := json.Unmarshal(raw, &set); err != nil {
		t.Fatal(err)
	}
	if len(set.Cases) == 0 {
		t.Fatal("no inference-service-history/0 vectors")
	}
	for _, c := range set.Cases {
		var card CardCheckpoints
		for _, cp := range c.Card.Checkpoints {
			card.Checkpoints = append(card.Checkpoints, CardCheckpoint{At: parseTime(t, cp.At), Records: cp.Records})
		}
		served, err := InferenceServiceHistoryCounts([]byte(c.Definition), []byte(c.Artifact), card, c.Pin)
		if c.Want != "" {
			var ie *InferenceServiceHistoryError
			if !errors.As(err, &ie) || !strings.Contains(err.Error(), c.Want) || ie.Property != c.Property ||
				!slices.Contains(InferenceServiceHistoryProperties, ie.Property) {
				t.Errorf("%s: got error %v, want one containing %q naming %s", c.Case, err, c.Want, c.Property)
			}
			continue
		}
		if err != nil {
			t.Errorf("%s: %v", c.Case, err)
			continue
		}
		var want *vectorServed
		if err := json.Unmarshal(c.Expected, &want); err != nil {
			t.Fatal(err)
		}
		got, _ := json.Marshal(toVectorServed(served))
		wantJSON, _ := json.Marshal(want)
		if string(got) != string(wantJSON) {
			t.Errorf("%s: got %s, want %s", c.Case, got, wantJSON)
		}
	}
}

func TestServedPeriodsRow(t *testing.T) {
	n := func(v int64) *int64 { return &v }
	card := Card{State: CardVerified, Served: &Served{LengthS: 604800, Floor: 5, Periods: []ServedPeriod{
		{Total: n(12)}, {Suppressed: SuppressedBelowFloor}, {Total: n(18)}, {Suppressed: SuppressedComplementary},
	}}}
	row := func(window, need int64) Row {
		return Row{Check: "card.self_reported_served_periods", Params: map[string]int64{"window": window, "min_periods": need}}
	}
	for _, tc := range []struct {
		name         string
		window, need int64
		card         Card
		result       string
		count        int64
		nc           NotCounted
	}{
		{"all four weeks, two counted", 4, 2, card, Pass, 2, NotCounted{BelowFloor: 1, Complementary: 1}},
		{"three needed", 4, 3, card, Fail, 2, NotCounted{BelowFloor: 1, Complementary: 1}},
		{"the last two only", 2, 1, card, Pass, 1, NotCounted{Complementary: 1}},
		{"a window longer than the stream", 6, 2, card, Pass, 2, NotCounted{BelowFloor: 1, Complementary: 1, NotListed: 2}},
	} {
		res := servedPeriods(row(tc.window, tc.need), tc.card)
		if res.Result != tc.result || *res.Count != tc.count || *res.Denominator != tc.window || *res.NotCounted != tc.nc {
			t.Errorf("%s: got %s %d/%d %+v", tc.name, res.Result, *res.Count, *res.Denominator, *res.NotCounted)
		}
	}
	for _, c := range []Card{{State: CardVerified}, {State: CardUnanswered, Served: card.Served}} {
		if res := servedPeriods(row(4, 1), c); res.Result != NotRead || res.NotCounted != nil {
			t.Errorf("%+v: got %s, want not_read", c, res.Result)
		}
	}
}

func parseTime(t *testing.T, s string) time.Time {
	t.Helper()
	if s == "" {
		return time.Time{}
	}
	at, err := time.Parse(time.RFC3339, s)
	if err != nil {
		t.Fatal(err)
	}
	return at
}
