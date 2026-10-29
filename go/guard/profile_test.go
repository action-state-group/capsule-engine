package guard

import (
	"strings"
	"testing"

	"github.com/action-state-group/capsule-engine/go/pack"
)

// inferenceEngine is an engine over the embedded asg/inference/0.1.0 pack
// (capsule-engine#263), with profile laid over it when given.
func inferenceEngine(t *testing.T, profile string) *Engine {
	t.Helper()
	p, err := pack.LoadInference010()
	if err != nil {
		t.Fatal(err)
	}
	e, err := NewEngine(p)
	if err != nil {
		t.Fatal(err)
	}
	if profile == "" {
		return e
	}
	prof, err := DecodeProfile([]byte(profile))
	if err != nil {
		t.Fatal(err)
	}
	if e, err = e.WithProfile(prof); err != nil {
		t.Fatal(err)
	}
	return e
}

func inferenceAction(t *testing.T, rates string) Action {
	t.Helper()
	a, err := DecodeAction(strings.NewReader(`{"operator":"node-a","developer":"mesh-node@v1","verb":"buy",
		"action_class":"money.purchase","taxonomy_version":"6","action_id":"inference/t1",
		"timestamp":"2026-10-29T12:00:00Z","target":"node-b"` + rates + `}`))
	if err != nil {
		t.Fatal(err)
	}
	return a
}

const capsProfile = `{"format":"policy-profile/v0","packs":[{"pack":"asg/inference","parameters":
	{"rate_cap":{"max_input_msat_per_million":2000000,"max_output_msat_per_million":8000000}}}]}`

func TestRateCapDecidesOnTheProfileCaps(t *testing.T) {
	e := inferenceEngine(t, capsProfile)
	for rates, want := range map[string]string{
		`,"input_msat_per_million":2000000,"output_msat_per_million":8000000`: Allow,
		`,"input_msat_per_million":2000001,"output_msat_per_million":0`:       Deny,
		`,"input_msat_per_million":0,"output_msat_per_million":8000001`:       Deny,
		`,"input_msat_per_million":0`:                                         Deny,
		``:                                                                    Deny,
	} {
		d, err := e.Check(inferenceAction(t, rates), nil)
		if err != nil {
			t.Fatal(err)
		}
		if d.Outcome != want {
			t.Errorf("rates %q: outcome %s, want %s", rates, d.Outcome, want)
		}
	}
}

func TestTheInferencePackShipsNoRateBound(t *testing.T) {
	d, err := inferenceEngine(t, "").Check(inferenceAction(t, `,"input_msat_per_million":9000000000000000,"output_msat_per_million":1`), nil)
	if err != nil {
		t.Fatal(err)
	}
	if d.Outcome != Allow {
		t.Errorf("outcome %s, want allow", d.Outcome)
	}
}

func TestWithProfileRefusesWhatResolveRefuses(t *testing.T) {
	cases := map[string]string{
		"another pack": `{"format":"policy-profile/v0","packs":[{"pack":"asg/everyday","parameters":{}}]}`,
		"caps limits": `{"format":"policy-profile/v0","packs":[{"pack":"asg/inference","parameters":
			{"caps":{"per_action_minor":{"money.purchase":1}}}}]}`,
	}
	for name, raw := range cases {
		p, err := DecodeProfile([]byte(raw))
		if err != nil {
			t.Fatalf("%s: %v", name, err)
		}
		e := inferenceEngine(t, "")
		if _, err := e.WithProfile(p); err == nil {
			t.Errorf("%s: WithProfile accepted it", name)
		}
	}
	p, err := DecodeProfile([]byte(capsProfile))
	if err != nil {
		t.Fatal(err)
	}
	everyday, err := pack.LoadEveryday039()
	if err != nil {
		t.Fatal(err)
	}
	e, err := NewEngine(everyday)
	if err != nil {
		t.Fatal(err)
	}
	p.Packs = map[string]ProfilePack{"asg/everyday": p.Packs["asg/inference"]}
	if _, err := e.WithProfile(p); err == nil || !strings.Contains(err.Error(), "rate_cap") {
		t.Errorf("rate_cap on a pack without the wicket: %v, want a refusal naming rate_cap", err)
	}
}

func TestDecodeProfileRefusesAMalformedRateCap(t *testing.T) {
	for _, params := range []string{
		`{"rate_cap":{}}`,
		`{"rate_cap":{"max_input_msat_per_million":-1}}`,
		`{"rate_cap":{"max_input_msat_per_million":1.5}}`,
		`{"rate_cap":{"max_input_msat_per_million":9007199254740992}}`,
		`{"rate_cap":{"max_total_msat":1}}`,
		`{"rate_limit":{}}`,
	} {
		raw := `{"format":"policy-profile/v0","packs":[{"pack":"asg/inference","parameters":` + params + `}]}`
		if _, err := DecodeProfile([]byte(raw)); err == nil {
			t.Errorf("%s: accepted", params)
		}
	}
}

func TestTheProfileDigestIgnoresEntryOrder(t *testing.T) {
	list := func(a, b string) string {
		return `{"format":"policy-profile/v0","packs":[{"pack":"asg/inference","parameters":{"counterparty_list":
			{"mode":"deny","entries":[{"kind":"target","value":"` + a + `"},{"kind":"target","value":"` + b + `"}]}}}]}`
	}
	p1, err := DecodeProfile([]byte(list("node-x", "node-y")))
	if err != nil {
		t.Fatal(err)
	}
	p2, err := DecodeProfile([]byte(list("node-y", "node-x")))
	if err != nil {
		t.Fatal(err)
	}
	if p1.Digest != p2.Digest {
		t.Errorf("digests differ: %s, %s", p1.Digest, p2.Digest)
	}
}
