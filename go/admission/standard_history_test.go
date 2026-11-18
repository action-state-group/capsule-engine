package admission

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// The shared standard_history/1 mapping vectors
// (conformance-vectors/admission/v0/standard-history.json), which the Rust
// port reads too.
func TestStandardHistoryVectors(t *testing.T) {
	runStandardHistoryVectors(t, "standard-history.json", StandardHistoryCounts)
}

// The standard_history/3 vectors (standard-history-3.json), read the same way.
func TestStandardHistory3Vectors(t *testing.T) {
	runStandardHistoryVectors(t, "standard-history-3.json", StandardHistory3Counts)
}

// A /1 reader given a /3 card, and a /3 reader given a /1 card, report the
// derivation as unknown (evidencebook#461 §4).
func TestStandardHistoryReadersRefuseTheOtherToken(t *testing.T) {
	for _, f := range []struct {
		file string
		read func([]byte) ([]BandCount, *Disputes, error)
	}{{"standard-history.json", StandardHistory3Counts}, {"standard-history-3.json", StandardHistoryCounts}} {
		raw, err := os.ReadFile(filepath.Join(vectorDir, f.file))
		if err != nil {
			t.Fatal(err)
		}
		var set struct {
			Cases []struct {
				Case     string    `json:"case"`
				Artifact string    `json:"artifact"`
				Expected *struct{} `json:"expected"`
			} `json:"cases"`
		}
		if err := json.Unmarshal(raw, &set); err != nil {
			t.Fatal(err)
		}
		n := 0
		for _, c := range set.Cases {
			if c.Expected == nil {
				continue
			}
			n++
			if _, _, err := f.read([]byte(c.Artifact)); err == nil || !strings.Contains(err.Error(), "derivation is") {
				t.Errorf("%s %s: got %v, want a derivation refusal", f.file, c.Case, err)
			}
		}
		if n == 0 {
			t.Fatalf("%s: no passing cards", f.file)
		}
	}
}

func runStandardHistoryVectors(t *testing.T, file string, read func([]byte) ([]BandCount, *Disputes, error)) {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join(vectorDir, file))
	if err != nil {
		t.Fatal(err)
	}
	var set struct {
		Cases []struct {
			Case     string `json:"case"`
			Artifact string `json:"artifact"`
			Want     string `json:"want"`
			Expected *struct {
				Bands    []BandCount `json:"bands"`
				Disputes Disputes    `json:"disputes"`
			} `json:"expected"`
		} `json:"cases"`
	}
	if err := json.Unmarshal(raw, &set); err != nil {
		t.Fatal(err)
	}
	if len(set.Cases) == 0 {
		t.Fatal("no vectors in " + file)
	}
	for _, c := range set.Cases {
		bands, disputes, err := read([]byte(c.Artifact))
		if c.Expected == nil {
			if err == nil || !strings.Contains(err.Error(), c.Want) {
				t.Errorf("%s: got error %v, want one containing %q", c.Case, err, c.Want)
			}
			continue
		}
		if err != nil {
			t.Errorf("%s: %v", c.Case, err)
			continue
		}
		got, _ := json.Marshal(struct {
			B []BandCount
			D Disputes
		}{bands, *disputes})
		want, _ := json.Marshal(struct {
			B []BandCount
			D Disputes
		}{c.Expected.Bands, c.Expected.Disputes})
		if string(got) != string(want) {
			t.Errorf("%s: got %s, want %s", c.Case, got, want)
		}
	}
}
