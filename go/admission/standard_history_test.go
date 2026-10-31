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
	raw, err := os.ReadFile(filepath.Join(vectorDir, "standard-history.json"))
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
		t.Fatal("no standard_history/1 vectors")
	}
	for _, c := range set.Cases {
		bands, disputes, err := StandardHistoryCounts([]byte(c.Artifact))
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
