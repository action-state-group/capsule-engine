package admission

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"flag"
	"os"
	"path/filepath"
	"testing"

	"github.com/action-state-group/capsule-engine/go/canon"
)

// The shared admission vectors (conformance-vectors/admission/v0): each
// case's verdict, source and tier are written by hand; block_sha256, the
// SHA-256 of the decision block's JCS bytes, is written by this test with
// -update and checked by the Rust port byte for byte.

var update = flag.Bool("update", false, "rewrite block_sha256 in the admission vectors")

const vectorDir = "../../conformance-vectors/admission/v0"

type vectorSet struct {
	Source string       `json:"source"`
	Cases  []vectorCase `json:"cases"`
}

type vectorCase struct {
	Case     string          `json:"case"`
	Pack     string          `json:"pack"`
	Input    json.RawMessage `json:"input"`
	Expected vectorExpected  `json:"expected"`
}

type vectorExpected struct {
	Verdict     string  `json:"verdict"`
	Source      string  `json:"source"`
	Tier        *string `json:"tier"`
	BlockSHA256 string  `json:"block_sha256,omitempty"`
}

func decodeInput(t *testing.T, raw json.RawMessage) Inputs {
	t.Helper()
	in, err := DecodeInputs(raw)
	if err != nil {
		t.Fatal(err)
	}
	return in
}

func TestConformanceVectors(t *testing.T) {
	path := filepath.Join(vectorDir, "cases.json")
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var set vectorSet
	if err := json.Unmarshal(raw, &set); err != nil {
		t.Fatal(err)
	}
	if len(set.Cases) == 0 {
		t.Fatal("no admission vectors")
	}
	packs := map[string]*Pack{}
	for i := range set.Cases {
		c := &set.Cases[i]
		p, ok := packs[c.Pack]
		if !ok {
			src, err := os.ReadFile(filepath.Join(vectorDir, filepath.FromSlash(c.Pack)))
			if err != nil {
				t.Fatal(err)
			}
			if p, err = Load(src); err != nil {
				t.Fatalf("%s: %v", c.Pack, err)
			}
			packs[c.Pack] = p
		}
		d := Evaluate(p, decodeInput(t, c.Input))
		jcs, err := canon.JCS(d.Block())
		if err != nil {
			t.Fatal(err)
		}
		sum := sha256.Sum256([]byte(jcs))
		got := hex.EncodeToString(sum[:])
		var tier *string
		if d.Tier != "" {
			tier = &d.Tier
		}
		if d.Verdict != c.Expected.Verdict || d.Source != c.Expected.Source || !samePtr(tier, c.Expected.Tier) {
			t.Errorf("%s: got %s from %s at %v; want %s from %s at %v\n%s", c.Case, d.Verdict, d.Source, deref(tier),
				c.Expected.Verdict, c.Expected.Source, deref(c.Expected.Tier), jcs)
			continue
		}
		if *update {
			c.Expected.BlockSHA256 = got
		} else if got != c.Expected.BlockSHA256 {
			t.Errorf("%s: block_sha256 %s, want %s\n%s", c.Case, got, c.Expected.BlockSHA256, jcs)
		}
	}
	if *update && !t.Failed() {
		out, err := json.MarshalIndent(set, "", " ")
		if err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, append(out, '\n'), 0o644); err != nil {
			t.Fatal(err)
		}
	}
}

func samePtr(a, b *string) bool {
	if a == nil || b == nil {
		return a == b
	}
	return *a == *b
}

func deref(s *string) string {
	if s == nil {
		return "<none>"
	}
	return *s
}
