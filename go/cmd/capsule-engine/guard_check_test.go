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
	tamperedHistory, err := os.ReadFile(filepath.Join(emitDir, "pay-history-tampered.json"))
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
		{"tampered record", tampered, nil, "cannot be decoded"},
		{"tampered history", tamperedHistory, nil, "cannot be decoded"},
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
			// No digest or id of the input reaches stderr.
			for _, hex := range regexp.MustCompile(`[0-9a-f]{16,}|7{8,}`).FindAllString(string(c.in), -1) {
				if strings.Contains(stderr, hex) {
					t.Fatalf("stderr repeats %q from the input", hex)
				}
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

// TestGuardCheckRoles: the party_role chooses the pack when nothing else
// does; a digest-only rules_pin naming both packs keeps the acting role's
// own pack; a pin that contradicts itself is refused; received_cards change
// nothing in the result.
func TestGuardCheckRoles(t *testing.T) {
	good, err := os.ReadFile(filepath.Join(emitDir, "pay-no-history.json"))
	if err != nil {
		t.Fatal(err)
	}
	with := func(members map[string]string) []byte {
		var env map[string]json.RawMessage
		if err := json.Unmarshal(good, &env); err != nil {
			t.Fatal(err)
		}
		for k, v := range members {
			env[k] = json.RawMessage(v)
		}
		b, _ := json.Marshal(env)
		return b
	}
	digest := func(id string) string {
		t.Helper()
		code, out, stderr := runGuardCheck(t, good, "--pack", id)
		if code != 0 {
			t.Fatalf("%s: exit %d %s", id, code, stderr)
		}
		var r struct {
			D string `json:"definition_digest"`
		}
		_ = json.Unmarshal([]byte(out), &r)
		return r.D
	}
	idOf := func(stdin []byte) string {
		t.Helper()
		code, out, stderr := runGuardCheck(t, stdin)
		if code != 0 {
			t.Fatalf("exit %d: %s", code, stderr)
		}
		var r struct {
			ID string `json:"ruleset_id"`
		}
		_ = json.Unmarshal([]byte(out), &r)
		return r.ID
	}
	if got := idOf(with(map[string]string{"party_role": `"seller"`})); got != "asg/seller/0.1.5" {
		t.Fatalf("seller default: %s", got)
	}
	both := `{"definition_digests":["` + digest("asg/everyday/0.3.8") + `","` + digest("asg/seller/0.1.5") + `"]}`
	for role, want := range map[string]string{"buyer": "asg/everyday/0.3.8", "seller": "asg/seller/0.1.5"} {
		if got := idOf(with(map[string]string{"party_role": `"` + role + `"`, "rules_pin": both})); got != want {
			t.Fatalf("%s with a flat pin of both packs: %s, want %s", role, got, want)
		}
	}
	// A pack and a digest that do not belong together name no pack.
	bad := `{"pack":"asg/everyday/0.3.8","definition_digests":["` + digest("asg/seller/0.1.5") + `"]}`
	if code, out, _ := runGuardCheck(t, with(map[string]string{"rules_pin": bad})); code != 2 || out != "" {
		t.Fatalf("contradicting pin: exit %d, stdout %q", code, out)
	}
	// received_cards are for a notice only: the result is the golden's.
	want, err := os.ReadFile(filepath.Join(emitDir, "golden", "pay-no-history.out.json"))
	if err != nil {
		t.Fatal(err)
	}
	code, out, stderr := runGuardCheck(t, with(map[string]string{"received_cards": `[{}]`}))
	if code != 0 || out != string(want) {
		t.Fatalf("received_cards changed the result (exit %d, stderr %q):\n%s", code, stderr, out)
	}
}
