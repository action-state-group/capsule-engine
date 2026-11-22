package main

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

const emitDir = "../../guard/testdata/emit"

func runGuardCheck(t *testing.T, stdin []byte, args ...string) (int, string, string) {
	t.Helper()
	var out, errb bytes.Buffer
	code := run(append([]string{"guard", "check"}, args...), bytes.NewReader(stdin), &out, &errb)
	return code, out.String(), errb.String()
}

// TestGuardCheckGoldens requires the command to print, byte for byte, the
// external-check-result/v0 each golden holds for its input. The goldens are
// the bytes the product plugin that first carried this mapping asserts for
// the same inputs (golden/PROVENANCE.json), so a change to the mapping that
// moves one sealed finding id or reason fails here.
func TestGuardCheckGoldens(t *testing.T) {
	goldens, err := filepath.Glob(filepath.Join(emitDir, "golden", "*.out.json"))
	if err != nil || len(goldens) == 0 {
		t.Fatalf("no goldens: %v", err)
	}
	for _, g := range goldens {
		name := strings.TrimSuffix(filepath.Base(g), ".out.json")
		t.Run(name, func(t *testing.T) {
			in, err := os.ReadFile(filepath.Join(emitDir, name+".json"))
			if err != nil {
				t.Fatal(err)
			}
			want, err := os.ReadFile(g)
			if err != nil {
				t.Fatal(err)
			}
			code, out, stderr := runGuardCheck(t, in)
			if code != 0 || stderr != "" {
				t.Fatalf("exit %d, stderr %q", code, stderr)
			}
			if out != string(want) {
				t.Fatalf("output differs from the golden:\n got  %s\n want %s", out, want)
			}
		})
	}
}

// TestGuardCheckRefusals: every refusal exits 2 with nothing on stdout and
// one line on stderr that repeats no value of the input.
func TestGuardCheckRefusals(t *testing.T) {
	good, err := os.ReadFile(filepath.Join(emitDir, "pay-no-history.json"))
	if err != nil {
		t.Fatal(err)
	}
	var withPin map[string]json.RawMessage
	if err := json.Unmarshal(good, &withPin); err != nil {
		t.Fatal(err)
	}
	pin := func(v string) []byte {
		withPin["rules_pin"] = json.RawMessage(v)
		b, _ := json.Marshal(withPin)
		return b
	}
	tampered, err := os.ReadFile(filepath.Join(emitDir, "pay-tampered.json"))
	if err != nil {
		t.Fatal(err)
	}
	cases := []struct {
		name   string
		in     []byte
		args   []string
		stderr string
	}{
		{"not JSON", []byte(`{"x":`), nil, "not JSON"},
		{"not an envelope", []byte(`{"x":1}`), nil, "does not validate"},
		{"empty stdin", nil, nil, "not JSON"},
		{"tampered record", tampered, nil, "capsule seals"},
		{"unknown pack id", good, []string{"--pack", "asg/nothing/9"}, "no embedded pack"},
		{"stray argument", good, []string{"extra"}, "usage"},
		{"pin names a pack not carried", pin(`{"pack":"asg/nothing/9"}`), nil, "does not carry"},
		{"pin names a digest not carried", pin(`{"definition_digests":["` + strings.Repeat("0", 64) + `"]}`), nil, "does not carry"},
		{"pin names nothing", pin(`{}`), nil, ""},
		{"pin with a null member", pin(`{"pack":null}`), nil, ""},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			code, out, stderr := runGuardCheck(t, c.in, c.args...)
			if code != 2 || out != "" {
				t.Fatalf("exit %d, stdout %q, stderr %q", code, out, stderr)
			}
			if strings.Count(strings.TrimSpace(stderr), "\n") != 0 || !strings.Contains(stderr, c.stderr) {
				t.Fatalf("stderr %q", stderr)
			}
		})
	}
}

// TestGuardCheckPins: a rules_pin names the pack that runs, and --pack
// overrides the party_role default.
func TestGuardCheckPins(t *testing.T) {
	good, err := os.ReadFile(filepath.Join(emitDir, "pay-no-history.json"))
	if err != nil {
		t.Fatal(err)
	}
	id := func(stdin []byte, args ...string) string {
		t.Helper()
		code, out, stderr := runGuardCheck(t, stdin, args...)
		if code != 0 {
			t.Fatalf("exit %d: %s", code, stderr)
		}
		var r struct {
			RulesetID string `json:"ruleset_id"`
		}
		if err := json.Unmarshal([]byte(out), &r); err != nil {
			t.Fatal(err)
		}
		return r.RulesetID
	}
	if got := id(good); got != "asg/everyday/0.3.8" {
		t.Fatalf("default pack %s", got)
	}
	var env map[string]json.RawMessage
	_ = json.Unmarshal(good, &env)
	env["rules_pin"] = json.RawMessage(`{"pack":"asg/everyday/0.3.7"}`)
	pinned, _ := json.Marshal(env)
	if got := id(pinned); got != "asg/everyday/0.3.7" {
		t.Fatalf("pinned pack %s", got)
	}
	if got := id(good, "--pack", "asg/everyday/0.3.7"); got != "asg/everyday/0.3.7" {
		t.Fatalf("--pack %s", got)
	}
}
