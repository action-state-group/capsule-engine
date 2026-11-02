package main

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

const vectorDir = "../../../conformance-vectors/admission/v0"

// TestVectors runs the command over every shared admission vector and
// requires the verdict, source, tier and block SHA-256 the vectors pin, so
// the command decides what go/admission and the Rust port decide.
func TestVectors(t *testing.T) {
	raw, err := os.ReadFile(filepath.Join(vectorDir, "cases.json"))
	if err != nil {
		t.Fatal(err)
	}
	var set struct {
		Cases []struct {
			Case     string          `json:"case"`
			Pack     string          `json:"pack"`
			Input    json.RawMessage `json:"input"`
			Expected struct {
				Verdict     string  `json:"verdict"`
				Source      string  `json:"source"`
				Tier        *string `json:"tier"`
				BlockSHA256 string  `json:"block_sha256"`
			} `json:"expected"`
		} `json:"cases"`
	}
	if err := json.Unmarshal(raw, &set); err != nil {
		t.Fatal(err)
	}
	if len(set.Cases) == 0 {
		t.Fatal("no admission vectors")
	}
	for _, c := range set.Cases {
		var out, errb bytes.Buffer
		code := run([]string{"admission", "evaluate", "--pack", filepath.Join(vectorDir, filepath.FromSlash(c.Pack))},
			bytes.NewReader(c.Input), &out, &errb)
		if code != 0 {
			t.Errorf("%s: exit %d: %s", c.Case, code, errb.String())
			continue
		}
		var got struct {
			Verdict     string  `json:"verdict"`
			Source      string  `json:"source"`
			Tier        *string `json:"tier"`
			BlockSHA256 string  `json:"block_sha256"`
		}
		if err := json.Unmarshal(out.Bytes(), &got); err != nil {
			t.Errorf("%s: %v", c.Case, err)
			continue
		}
		if got.Verdict != c.Expected.Verdict || got.Source != c.Expected.Source ||
			(got.Tier == nil) != (c.Expected.Tier == nil) || (got.Tier != nil && *got.Tier != *c.Expected.Tier) ||
			got.BlockSHA256 != c.Expected.BlockSHA256 {
			t.Errorf("%s: got %+v, want %+v", c.Case, got, c.Expected)
		}
	}
}

func TestUnusable(t *testing.T) {
	pack := filepath.Join(vectorDir, "admission-baseline-1.0.0", "pack-definition.json")
	for name, tc := range map[string]struct {
		args  []string
		stdin string
	}{
		"no verb":        {nil, ""},
		"no pack":        {[]string{"admission", "evaluate"}, "{}"},
		"missing pack":   {[]string{"admission", "evaluate", "--pack", "nope.json"}, "{}"},
		"half a pin":     {[]string{"admission", "evaluate", "--pack", pack, "--pack-id", "x"}, "{}"},
		"wrong pin":      {[]string{"admission", "evaluate", "--pack", pack, "--pack-id", "x", "--digest", "y"}, "{}"},
		"unknown member": {[]string{"admission", "evaluate", "--pack", pack}, `{"nwo":"2026-01-01T00:00:00Z"}`},
		"bad time":       {[]string{"admission", "evaluate", "--pack", pack}, `{"now":"yesterday"}`},
		"trailing data":  {[]string{"admission", "evaluate", "--pack", pack}, `{} {}`},
		"stray brace":    {[]string{"admission", "evaluate", "--pack", pack}, `{} }`},
		"stray bracket":  {[]string{"admission", "evaluate", "--pack", pack}, `{} ]`},
		"null input":     {[]string{"admission", "evaluate", "--pack", pack}, `null`},
		"stray argument": {[]string{"admission", "evaluate", "--pack", pack, "stray"}, `{}`},
		"unknown flag":   {[]string{"admission", "evaluate", "--bogus"}, `{}`},
		"not json":       {[]string{"admission", "evaluate", "--pack", pack}, `nope`},
	} {
		var out, errb bytes.Buffer
		if code := run(tc.args, strings.NewReader(tc.stdin), &out, &errb); code != exitUnusable {
			t.Errorf("%s: exit %d, want %d", name, code, exitUnusable)
		}
		if out.Len() != 0 || errb.Len() == 0 || strings.Count(strings.TrimSpace(errb.String()), "\n") != 0 {
			t.Errorf("%s: stdout %q, stderr %q", name, out.String(), errb.String())
		}
	}
}

func TestPinnedAndInputFile(t *testing.T) {
	pack := filepath.Join(vectorDir, "admission-baseline-1.0.0", "pack-definition.json")
	var first struct {
		Pack struct{ ID, Digest string } `json:"pack"`
	}
	var out, errb bytes.Buffer
	if code := run([]string{"admission", "evaluate", "--pack", pack}, strings.NewReader(`{"now":"2026-01-01T00:00:00Z"}`), &out, &errb); code != 0 {
		t.Fatalf("exit %d: %s", code, errb.String())
	}
	if err := json.Unmarshal(out.Bytes(), &first); err != nil {
		t.Fatal(err)
	}
	in := filepath.Join(t.TempDir(), "in.json")
	if err := os.WriteFile(in, []byte(`{"now":"2026-01-01T00:00:00Z"}`), 0o600); err != nil {
		t.Fatal(err)
	}
	var out2 bytes.Buffer
	if code := run([]string{"admission", "evaluate", "--pack", pack, "--pack-id", first.Pack.ID, "--digest", first.Pack.Digest, "--input", in}, strings.NewReader(""), &out2, &errb); code != 0 {
		t.Fatalf("exit %d: %s", code, errb.String())
	}
	if !bytes.Equal(out.Bytes(), out2.Bytes()) {
		t.Errorf("pinned file run differs from stdin run")
	}
}
