package main

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"regexp"
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

// TestAdmit runs the command with --admit over the shared admit vectors:
// the body it prints is the vector's, apart from the nonce it draws, which
// is fresh 64 lowercase hex on every run; admits_deal is the vector's; and
// without --admit neither member is printed.
func TestAdmit(t *testing.T) {
	read := func(name string, v any) {
		raw, err := os.ReadFile(filepath.Join(vectorDir, name))
		if err != nil {
			t.Fatal(err)
		}
		if err := json.Unmarshal(raw, v); err != nil {
			t.Fatal(err)
		}
	}
	var cases struct {
		Cases []struct {
			Case  string          `json:"case"`
			Pack  string          `json:"pack"`
			Input json.RawMessage `json:"input"`
		} `json:"cases"`
	}
	read("cases.json", &cases)
	var admits struct {
		Cases []struct {
			Case       string   `json:"case"`
			Asks       []string `json:"asks"`
			Answers    []string `json:"answers"`
			CardDigest string   `json:"card_digest"`
			Admit      *string  `json:"admit"`
			AdmitsDeal bool     `json:"admits_deal"`
		} `json:"cases"`
	}
	read("admit.json", &admits)
	nonceRE := regexp.MustCompile(`"nonce":"([0-9a-f]{64})"`)
	nonces := map[string]bool{}
	for _, a := range admits.Cases {
		var pack string
		var input json.RawMessage
		for _, c := range cases.Cases {
			if c.Case == a.Case {
				pack, input = filepath.Join(vectorDir, filepath.FromSlash(c.Pack)), c.Input
			}
		}
		refs, err := json.Marshal(map[string]any{"asks": a.Asks, "answers": a.Answers, "card_digest": a.CardDigest})
		if err != nil {
			t.Fatal(err)
		}
		refsPath := filepath.Join(t.TempDir(), "refs.json")
		if err := os.WriteFile(refsPath, refs, 0o600); err != nil {
			t.Fatal(err)
		}
		var out, errb bytes.Buffer
		if code := run([]string{"admission", "evaluate", "--pack", pack, "--admit", refsPath}, bytes.NewReader(input), &out, &errb); code != 0 {
			t.Fatalf("%s: exit %d: %s", a.Case, code, errb.String())
		}
		var o struct {
			Admit      json.RawMessage `json:"admit"`
			AdmitsDeal *bool           `json:"admits_deal"`
		}
		if err := json.Unmarshal(out.Bytes(), &o); err != nil {
			t.Fatal(err)
		}
		if o.AdmitsDeal == nil || *o.AdmitsDeal != a.AdmitsDeal {
			t.Errorf("%s: admits_deal %v, want %v", a.Case, o.AdmitsDeal, a.AdmitsDeal)
		}
		if a.Admit == nil {
			if string(o.Admit) != "null" {
				t.Errorf("%s: admit %s, want null", a.Case, o.Admit)
			}
			continue
		}
		m := nonceRE.FindSubmatch(o.Admit)
		if m == nil {
			t.Fatalf("%s: no nonce in %s", a.Case, o.Admit)
		}
		if nonces[string(m[1])] {
			t.Errorf("%s: nonce drawn twice", a.Case)
		}
		nonces[string(m[1])] = true
		want := nonceRE.ReplaceAllString(*a.Admit, `"nonce":"`+string(m[1])+`"`)
		if string(o.Admit) != want {
			t.Errorf("%s: admit\n got %s\nwant %s", a.Case, o.Admit, want)
		}
	}

	pack := filepath.Join(vectorDir, "admission-baseline-1.0.0", "pack-definition.json")
	var out, errb bytes.Buffer
	if code := run([]string{"admission", "evaluate", "--pack", pack}, strings.NewReader(`{"now":"2026-01-01T00:00:00Z"}`), &out, &errb); code != 0 {
		t.Fatalf("exit %d: %s", code, errb.String())
	}
	if strings.Contains(out.String(), `"admit"`) || strings.Contains(out.String(), `"admits_deal"`) {
		t.Errorf("without --admit: %s", out.String())
	}

	// A refs file the command cannot use is a usage error, exit 2, nothing on stdout.
	dir := t.TempDir()
	for name, body := range map[string]string{
		"an unknown member":      `{"asks":[],"answers":[],"card_digest":"` + strings.Repeat("c", 64) + `","nonce":"` + strings.Repeat("e", 64) + `"}`,
		"no card_digest":         `{"asks":[],"answers":[]}`,
		"no asks":                `{"answers":[],"card_digest":"` + strings.Repeat("c", 64) + `"}`,
		"an ask that is not hex": `{"asks":["x"],"answers":[],"card_digest":"` + strings.Repeat("c", 64) + `"}`,
		"two JSON values":        `{"asks":[],"answers":[],"card_digest":"` + strings.Repeat("c", 64) + `"} {}`,
		"not JSON":               `asks`,
	} {
		path := filepath.Join(dir, "refs.json")
		if err := os.WriteFile(path, []byte(body), 0o600); err != nil {
			t.Fatal(err)
		}
		in := `{"now":"2026-01-01T00:00:00Z","amount":"1","currency":"USD"}`
		var out, errb bytes.Buffer
		code := run([]string{"admission", "evaluate", "--pack", pack, "--admit", path}, strings.NewReader(in), &out, &errb)
		if code != exitUnusable || out.Len() != 0 {
			t.Errorf("%s: exit %d, stdout %q", name, code, out.String())
		}
	}
	var out2, errb2 bytes.Buffer
	if code := run([]string{"admission", "evaluate", "--pack", pack, "--admit", filepath.Join(dir, "absent.json")}, strings.NewReader(`{}`), &out2, &errb2); code != exitUnusable {
		t.Errorf("a refs file that does not exist: exit %d", code)
	}
}
