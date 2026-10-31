package admission

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func ladderSource(t *testing.T) string {
	t.Helper()
	src, err := os.ReadFile(filepath.Join(vectorDir, "packs", "ladder.yaml"))
	if err != nil {
		t.Fatal(err)
	}
	return string(src)
}

func TestLoadPinned(t *testing.T) {
	src := ladderSource(t)
	p, err := Load([]byte(src))
	if err != nil {
		t.Fatal(err)
	}
	if _, err := LoadPinned([]byte(src), p.ID, p.Digest); err != nil {
		t.Errorf("the pinned bytes: %v", err)
	}
	if _, err := LoadPinned([]byte(src+"\n"), p.ID, p.Digest); err == nil {
		t.Error("changed bytes under the same pin load")
	}
	if _, err := LoadPinned([]byte(src), "asg/other/0.1.0", p.Digest); err == nil {
		t.Error("another pack id loads under this pin")
	}
}

// TestLoadRefuses is the admission design rules (capsule-engine#265) as the
// loader holds them: each edit of the ladder pack must be refused, naming
// what broke the rule.
func TestLoadRefuses(t *testing.T) {
	cases := []struct {
		name, old, new, want string
	}{
		{"a card row decides NO",
			"    check: card.verified\n    from: T1\n    on_fail: LIMIT", "    check: card.verified\n    from: T1\n    on_fail: \"NO\"",
			"a card row may not decide NO"},
		{"a vouch row decides NO",
			"    from: T2\n    on_fail: LIMIT", "    from: T2\n    on_fail: \"NO\"", "a vouch row may not decide NO"},
		{"a judged row decides NO",
			"    on_fail: {cap: T1}", "    on_fail: \"NO\"", "a judged row's on_fail must be {cap: <tier>}"},
		{"a missing input defaults to GO", "missing: LIMIT", "missing: GO", "missing must not be GO"},
		{"a score field", "budget:\n", "score_weights: {settled: 3}\nbudget:\n", "score_weights"},
		{"a judged row in a pack without LIMIT", `outcomes: ["GO", "ASK", "LIMIT", "NO"]`, `outcomes: ["GO", "ASK", "NO"]`,
			"a judged row caps a tier, so LIMIT must be in outcomes"},
		{"a cap that lowers nothing", "    on_fail: {cap: T1}", "    on_fail: {cap: T2}", "cap T2 must be a tier below from T2"},
		{"an ask on a NO row",
			"    check: platform.attestation_not_invalid\n    from: T0\n    on_fail: \"NO\"",
			"    check: platform.attestation_not_invalid\n    from: T0\n    on_fail: \"NO\"\n    ask: {derivation: history_card/1}",
			"only a LIMIT row may carry an ask"},
		{"a ceiling in a unit accept does not name", "max_decade: {USD: 2}", "max_decade: {USD: 2, Sat: 9}", "max_decade.Sat is not a band unit in accept"},
		{"a ceiling that falls across a skipped tier", "max_decade: {USD: 2}", "max_decade: {USD: 2, sat: 3}", "max_decade.sat 3 does not rise above the tier below (4)"},
		{"a cap with another key", "    on_fail: {cap: T1}", "    on_fail: {cap: T1, weight: 2}", "a judged row's on_fail must be {cap: <tier>}"},
		{"a unit no tier names", "  USD: {band: USD, scale: 0}\n", "  USD: {band: USD, scale: 0}\n  EUR: {band: EUR, scale: 0}\n", "band unit EUR is named by no tier"},
		{"a negative threshold", "params: {min: 1}\n    from: T1", "params: {min: -1}\n    from: T1", "params.min -1 must be from 0"},
		{"a threshold whose duration wraps", "params: {max_age_s: 604800}", "params: {max_age_s: 99999999999}", "params.max_age_s 99999999999 must be from 0"},
		{"a float", "scale: -3}", "scale: -3.5}", "quote \"-3.5\""},
		{"a hex integer", "max_asks: 2", "max_asks: 0x2", "quote \"0x2\""},
		{"a boolean id", "  - id: attestation\n", "  - id: True\n", "quote \"True\""},
		{"an exponent with no dot", "  - id: attestation\n", "  - id: 1e3\n", "quote \"1e3\""},
		{"an octal 0o", "  - id: attestation\n", "  - id: 0o10\n", "quote \"0o10\""},
		{"a one-digit date", "  - id: attestation\n", "  - id: 2026-1-2\n", "quote \"2026-1-2\""},
		{"a base-60 number", "  - id: attestation\n", "  - id: 1:30\n", "quote \"1:30\""},
		{"a tag on a quoted scalar", "max_asks: 2", "max_asks: !!int \"2\"", "tags are not read"},
		{"a merge key", "  - id: T0\n", "  - <<: {x: 1}\n    id: T0\n", "quote \"<<\""},
		{"an anchor", "  - id: T0\n", "  - &t0 id: T0\n", "anchors and aliases"},
		{"no ask deadline", "  deadline_s: 86400\n", "", "budget.deadline_s must be a positive integer"},
		{"a row weight", "    check: own.settled\n", "    check: own.settled\n    weight: 2\n", "weight"},
		{"LIMIT from the lowest tier",
			"    params: {min: 1}\n    from: T0\n    on_fail: record", "    params: {min: 1}\n    from: T0\n    on_fail: LIMIT",
			"cannot LIMIT"},
		{"an unknown predicate", "check: own.settled", "check: own.reputation", "not an admission predicate"},
		{"a predicate under the wrong source", "    source: own_history\n    check: own.settled", "    source: card\n    check: own.settled", "reads own_history"},
		{"a missing threshold", "    params: {min: 1}\n    from: T1", "    from: T1", "needs params.min"},
		{"decades that do not rise", "max_decade: {sat: 4, USD: 0}", "max_decade: {sat: 3, USD: 0}", "does not rise"},
		{"an outcome the pack does not yield", `outcomes: ["GO", "ASK", "LIMIT", "NO"]`, `outcomes: ["GO", "LIMIT", "NO"]`, "ASK is not in outcomes"},
		{"an unquoted NO", `    from: T0
    on_fail: "NO"`, `    from: T0
    on_fail: NO`, "is a boolean in YAML 1.1"},
		{"another scope kind", "kind: admission", "kind: live", "scope.kind must be admission"},
		{"a version that is not the id's", "pack_version: 0.1.0", "pack_version: 0.2.0", "does not end in pack_version"},
	}
	src := ladderSource(t)
	for _, c := range cases {
		if !strings.Contains(src, c.old) {
			t.Fatalf("%s: the ladder pack has no %q", c.name, c.old)
		}
		_, err := Load([]byte(strings.Replace(src, c.old, c.new, 1)))
		if err == nil || !strings.Contains(err.Error(), c.want) {
			t.Errorf("%s: got %v; want an error naming %q", c.name, err, c.want)
		}
	}
}
