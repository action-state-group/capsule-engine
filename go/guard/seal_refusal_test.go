package guard

import (
	"os"
	"strings"
	"testing"
)

// capsuleCLIShown is what capsule-cli shows the user when a rules checker
// exits non-zero (plugins/internal/plugin/rules_checker.go runRulesChecker
// and firstLine): its prefix, then the checker's stderr cut to its first
// line and its first 300 bytes. The checker's stderr is its own line
// (actionstate-muse internal/plugin/plugin.go: "capsulectl actionstate: "
// and the error).
func capsuleCLIShown(err error) string {
	why := "capsulectl actionstate: " + err.Error()
	if i := strings.IndexByte(why, '\n'); i >= 0 {
		why = why[:i]
	}
	if len(why) > 300 {
		why = why[:300]
	}
	return "the rules checker refused the record: " + why
}

// When a record's or a history act's disclosed body is not the one its
// capsule seals, Go refuses it at decode, and what the user is shown must
// read as that refusal (capsule-engine #210, switch-over gate 2): the seal
// wording leads, within the first 300 bytes of what capsule-cli shows, with
// the act's id and the digests after it.
func TestASealRefusalKeepsItsWordingWhereTheUserSeesIt(t *testing.T) {
	for _, c := range []struct{ input, want string }{
		{"pay-tampered", "the record's body is not the one its capsule seals"},
		{"pay-history-tampered", "a history act's body is not the one its capsule seals (act "},
	} {
		raw, err := os.ReadFile("testdata/emit/" + c.input + ".json")
		if err != nil {
			t.Fatal(err)
		}
		_, _, err = DecodeCheckInput(raw)
		if err == nil {
			t.Fatalf("%s: decoded; want a refusal", c.input)
		}
		if !strings.HasPrefix(err.Error(), c.want) {
			t.Errorf("%s: %q does not lead with %q", c.input, err, c.want)
		}
		shown := capsuleCLIShown(err)
		if len(shown) > 300 {
			shown = shown[:300]
		}
		if !strings.Contains(shown, c.want) {
			t.Errorf("%s: the first 300 bytes shown lose the seal wording: %q", c.input, shown)
		}
		if !strings.Contains(err.Error(), "agent_input_digest") {
			t.Errorf("%s: %q no longer names the digests", c.input, err)
		}
	}
}

// Every other refusal of a history act leads with its reason and names the
// act after it, so the id never pushes the reason out of what is shown.
func TestAHistoryActRefusalLeadsWithItsReason(t *testing.T) {
	id := strings.Repeat("7", 64)
	for name, c := range map[string]struct{ raw, want string }{
		"no agent_input": {`{"capsule_id":"` + id + `"}`,
			"a history act's capsule carries no disclosed agent_input (act " + id + ")"},
		"no digest": {`{"capsule_id":"` + id + `","agent_input":{"body":{}}}`,
			"a history act's body is not bound to its capsule (act " + id + "): the capsule header has no"},
		"no capsule_id": {`{"agent_input":{}}`, "a history act has no capsule_id"},
	} {
		_, err := DecodeHistoryAct([]byte(c.raw))
		if err == nil || !strings.HasPrefix(err.Error(), c.want) {
			t.Errorf("%s: %v, want it to lead with %q", name, err, c.want)
		}
	}
}
