package admission

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

// admission-baseline@1 and its fixtures (evidencebook-skills#51), copied
// byte for byte into conformance-vectors/admission/v0/admission-baseline-1.0.0
// with the commit they came from in PROVENANCE.json. The fixtures' verdict,
// source and tier are written by hand in evidencebook-skills; this evaluator
// and the Rust one decide each the same.

const baselineDir = vectorDir + "/admission-baseline-1.0.0"

func TestBaselineFixtures(t *testing.T) {
	var prov struct {
		Commit string            `json:"commit"`
		Files  map[string]string `json:"files"`
	}
	raw, err := os.ReadFile(filepath.Join(baselineDir, "PROVENANCE.json"))
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(raw, &prov); err != nil {
		t.Fatal(err)
	}
	read := func(name string) []byte {
		b, err := os.ReadFile(filepath.Join(baselineDir, name))
		if err != nil {
			t.Fatal(err)
		}
		if sum := sha256.Sum256(b); hex.EncodeToString(sum[:]) != prov.Files[name] {
			t.Fatalf("%s is not the copy PROVENANCE.json pins (evidencebook-skills %s)", name, prov.Commit)
		}
		return b
	}
	p, err := Load(read("pack-definition.json"))
	if err != nil {
		t.Fatal(err)
	}
	var set struct {
		Pack  string       `json:"pack"`
		Cases []vectorCase `json:"cases"`
	}
	if err := json.Unmarshal(read("cases.json"), &set); err != nil {
		t.Fatal(err)
	}
	if set.Pack != p.ID || len(set.Cases) == 0 {
		t.Fatalf("the fixtures name %s with %d cases; the pack is %s", set.Pack, len(set.Cases), p.ID)
	}
	for _, c := range set.Cases {
		d := Evaluate(p, decodeInput(t, c.Input))
		var tier *string
		if d.Tier != "" {
			tier = &d.Tier
		}
		if d.Verdict != c.Expected.Verdict || d.Source != c.Expected.Source || !samePtr(tier, c.Expected.Tier) {
			t.Errorf("%s: got %s from %s at %s; want %s from %s at %s", c.Case, d.Verdict, d.Source, deref(tier),
				c.Expected.Verdict, c.Expected.Source, deref(c.Expected.Tier))
		}
	}
}
