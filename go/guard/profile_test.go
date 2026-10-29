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
		`{"rate_cap":{"max_input_msat_per_million":"2000000"}}`,
		`{"rate_cap":{"max_input_msat_per_million":true}}`,
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

// DecodeProfile refuses what profile.py parse_profile refuses beyond a value's
// range: a pack entry with no parameters mapping, an entry key its kind does
// not have (an "fp_alg": null on a target entry is one), and data after the
// document.
func TestDecodeProfileRefusesWhatParseProfileRefuses(t *testing.T) {
	list := func(entry string) string {
		return `{"format":"policy-profile/v0","packs":[{"pack":"asg/inference","parameters":{"counterparty_list":
			{"mode":"deny","entries":[` + entry + `]}}}]}`
	}
	for name, raw := range map[string]string{
		"no parameters":        `{"format":"policy-profile/v0","packs":[{"pack":"asg/inference"}]}`,
		"null parameters":      `{"format":"policy-profile/v0","packs":[{"pack":"asg/inference","parameters":null}]}`,
		"target with fp_alg":   list(`{"kind":"target","value":"node-b","fp_alg":null}`),
		"target extra key":     list(`{"kind":"target","value":"node-b","note":"x"}`),
		"payee without fp_alg": list(`{"kind":"payee","value":"` + strings.Repeat("b", 64) + `"}`),
		"value not a string":   list(`{"kind":"target","value":1}`),
		"trailing data":        capsProfile + ` {}`,
	} {
		if _, err := DecodeProfile([]byte(raw)); err == nil {
			t.Errorf("%s: accepted", name)
		}
	}
	if _, err := DecodeProfile([]byte(list(`{"kind":"payee","fp_alg":"hmac-sha256-deal-key","value":"` + strings.Repeat("b", 64) + `"}`))); err != nil {
		t.Errorf("a payee entry with its fp_alg: %v", err)
	}
}

// A second WithProfile replaces the first whole: what it omits is the
// wicket's default again (resolve.py lays a profile over the wicket's own
// config), and a refused profile leaves the engine as it was.
func TestWithProfileReplacesAnEarlierProfileWhole(t *testing.T) {
	first := `{"format":"policy-profile/v0","packs":[{"pack":"asg/inference","parameters":{
		"rate_cap":{"max_input_msat_per_million":1},
		"counterparty_list":{"mode":"allow","entries":[{"kind":"target","value":"node-x"}],"disposition":"ask"}}}]}`
	second := `{"format":"policy-profile/v0","packs":[{"pack":"asg/inference","parameters":{
		"counterparty_list":{"mode":"deny","entries":[{"kind":"target","value":"node-x"}]}}}]}`
	e := inferenceEngine(t, first)
	p, err := DecodeProfile([]byte(second))
	if err != nil {
		t.Fatal(err)
	}
	if e, err = e.WithProfile(p); err != nil {
		t.Fatal(err)
	}
	if e.rateCap.MaxInputMsatPerMillion != maxSafeInt || e.list.Disposition != "deny" || e.list.Mode != "deny" {
		t.Errorf("after the second profile: rate_cap %+v, list %+v; want the wicket's cap and disposition", e.rateCap, e.list)
	}

	before := inferenceEngine(t, first)
	rateCap, list := before.rateCap, before.list
	bad, err := DecodeProfile([]byte(`{"format":"policy-profile/v0","packs":[{"pack":"asg/inference","parameters":{
		"rate_cap":{"max_input_msat_per_million":5},"caps":{"per_action_minor":{"money.purchase":1}}}}]}`))
	if err != nil {
		t.Fatal(err)
	}
	if _, err := before.WithProfile(bad); err == nil {
		t.Fatal("a profile setting caps limits was accepted")
	}
	if before.rateCap.MaxInputMsatPerMillion != rateCap.MaxInputMsatPerMillion || before.list.Disposition != list.Disposition {
		t.Errorf("a refused profile changed the engine: rate_cap %+v, list %+v", before.rateCap, before.list)
	}
}
