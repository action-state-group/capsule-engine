package guard

import (
	"encoding/json"
	"path/filepath"
	"slices"
	"testing"

	"gopkg.in/yaml.v3"

	"github.com/action-state-group/capsule-engine/go/pack"
)

// actionClassGate12Vectors are action_class_gate/1.2.0's differential
// vectors (PROVENANCE.json): capsulectl rc20's share and commit with each
// telling-memory input, decided by capsule-engine 0c93a58's own
// CONFIGURED_CHECKS["action_class_gate"] under the 1.2.0 definition.
var actionClassGate12Vectors = filepath.Join("testdata", "action-class-gate-12-vectors")

const actionClassGate12 = "action_class_gate/1.2.0"

type gateVector struct {
	releaseVector
	RepeatDisclosures   []string `json:"repeat_disclosures"`
	RecipientToldBefore *bool    `json:"recipient_told_before"`
}

// CROSS-IMPLEMENTATION: every vector, the record read as a live check
// input, with NO known difference from the engine: the telling-memory
// inputs read, and the gate's result, its n/a reason and its evidence. A
// fail's reason is the engine's own report line, which Go builds from the
// matched selectors; the evidence names them.
func TestActionClassGate12AgreesWithTheEngine(t *testing.T) {
	v := decodeJSON[struct {
		Definition       string       `json:"definition"`
		DefinitionDigest string       `json:"definition_digest"`
		Vectors          []gateVector `json:"vectors"`
	}](t, readFile(t, actionClassGate12Vectors, "expected.json"))
	if v.Definition != actionClassGate12 || v.DefinitionDigest != portedDefinitions[actionClassGate12] {
		t.Fatalf("vectors under %s %s, ported %s", v.Definition, v.DefinitionDigest, portedDefinitions[actionClassGate12])
	}
	e, _ := seller014Engine(t)
	p, err := pack.LoadEveryday038()
	if err != nil {
		t.Fatal(err)
	}
	i := slices.IndexFunc(p.Wickets, func(w pack.Wicket) bool { return w.ID == actionClassGate12 })
	if i < 0 {
		t.Fatalf("%s cites no %s", p.ID, actionClassGate12)
	}
	gc, err := decodeConfig[gateConfig](p.Wickets[i])
	if err != nil {
		t.Fatal(err)
	}
	results := map[string]int{}
	for _, want := range v.Vectors {
		a, _, err := DecodeCheckInput(readFile(t, actionClassGate12Vectors, want.Input))
		if err != nil {
			t.Fatalf("%s: %v", want.Input, err)
		}
		if (a.RepeatDisclosures == nil) != (want.RepeatDisclosures == nil) || !slices.Equal(a.RepeatDisclosures, want.RepeatDisclosures) {
			t.Errorf("%s: repeat_disclosures %v, engine %v", want.Input, a.RepeatDisclosures, want.RepeatDisclosures)
		}
		if (a.RecipientToldBefore == nil) != (want.RecipientToldBefore == nil) ||
			a.RecipientToldBefore != nil && *a.RecipientToldBefore != *want.RecipientToldBefore {
			t.Errorf("%s: recipient_told_before %v, engine %v", want.Input, a.RecipientToldBefore, want.RecipientToldBefore)
		}
		got, err := checkActionClassGate(a, gc.Selectors, e.tax)
		if err != nil {
			t.Fatal(err)
		}
		engine := releaseOutcomeOf(t, want.Result, want.Reason, want.Evidence)
		if want.Result != "n/a" || want.Reason == "no selector names this action class" {
			engine.Reason = ""
		}
		if cell := releaseCell(t, got); cell != engine {
			t.Errorf("%s: Go %+v, engine %+v", want.Input, cell, engine)
		}
		results[want.Result]++
	}
	if len(v.Vectors) != 39 || results["fail"] == 0 || results["n/a"] != 9 {
		t.Errorf("%d vectors, %v", len(v.Vectors), results)
	}
}

// A 1.2.0 except key is true when set, as action_class_gate.py requires.
func TestAGateExceptFlagMustBeTrue(t *testing.T) {
	e, _ := seller014Engine(t)
	for _, flag := range []string{"except_repeat_disclosures", "except_recipient_told_before"} {
		for _, value := range []string{"false", "1", `"true"`} {
			node := yamlNode(t, "s:\n  trigger_classes: [DISCLOSE]\n  "+flag+": "+value+"\n  on_match: fail\n")
			if _, err := parseSelectors(node, e.tax); err == nil {
				t.Errorf("%s: %s loaded", flag, value)
			}
		}
		if _, err := parseSelectors(yamlNode(t, "s:\n  trigger_classes: [DISCLOSE]\n  "+flag+": true\n  on_match: fail\n"), e.tax); err != nil {
			t.Errorf("%s: true: %v", flag, err)
		}
	}
}

// yamlNode is src's top mapping node.
func yamlNode(t *testing.T, src string) yaml.Node {
	t.Helper()
	var doc yaml.Node
	if err := yaml.Unmarshal([]byte(src), &doc); err != nil || len(doc.Content) != 1 {
		t.Fatalf("yaml %q: %v", src, err)
	}
	return *doc.Content[0]
}

// A selector's exceptions apply in order (the role, then a repeat, then a
// recipient told before) and read only what they reach; when selectors
// leave an action out for different reasons, the reason names each, the
// role by its value, sorted.
func TestGateExceptionsOrderReadsAndMixedReason(t *testing.T) {
	e, _ := seller014Engine(t)
	node := yamlNode(t, "by_role:\n  trigger_classes: [DISCLOSE]\n  except_recipient_roles: [platform]\n  on_match: fail\n"+
		"by_memory:\n  trigger_classes: [DISCLOSE]\n  except_repeat_disclosures: True\n  except_recipient_told_before: TRUE\n  on_match: fail\n")
	yes := true
	share := func(role string, repeat []string, told *bool) Action {
		class := "disclosure.personal"
		return Action{ActionClass: &class, RecipientRole: &role, DisclosedClasses: []string{"phone"}, RepeatDisclosures: repeat,
			RecipientToldBefore: told}
	}
	for _, x := range []struct {
		name   string
		a      Action
		result Result
		note   string
		reads  []string
	}{
		{"a platform told before", share("platform", []string{"phone"}, &yes), NotApplicable,
			"the selectors naming action class 'disclosure.personal' leave this action out: every class it discloses was told to this recipient before; recipient_role 'platform'",
			[]string{"action_class", "disclosed_classes", "recipient_role", "repeat_disclosures"}},
		{"a buyer told the class and before", share("buyer", []string{"phone"}, &yes), Fail, "",
			[]string{"action_class", "disclosed_classes", "recipient_role", "repeat_disclosures"}},
		{"a buyer told before, a new class", share("buyer", []string{"email"}, &yes), Fail, "",
			[]string{"action_class", "disclosed_classes", "recipient_role", "recipient_told_before", "repeat_disclosures"}},
	} {
		got, err := checkActionClassGate(x.a, node, e.tax)
		if err != nil {
			t.Fatal(err)
		}
		if got.Result != x.result || got.scopeNote != x.note || !slices.Equal(got.reads, x.reads) {
			t.Errorf("%s: %s %q reads %v", x.name, got.Result, got.scopeNote, got.reads)
		}
	}
	// Repeat before told: a selector setting both stops at the repeat.
	memory := yamlNode(t, "m:\n  trigger_classes: [DISCLOSE]\n  except_repeat_disclosures: true\n  except_recipient_told_before: true\n  on_match: fail\n")
	got, err := checkActionClassGate(share("buyer", []string{"phone"}, &yes), memory, e.tax)
	if err != nil {
		t.Fatal(err)
	}
	if got.scopeNote != "the selectors naming action class 'disclosure.personal' leave this action out: every class it discloses was told to this recipient before" ||
		slices.Contains(got.reads, "recipient_told_before") {
		t.Errorf("repeat then told: %q reads %v", got.scopeNote, got.reads)
	}
}

// On --action, repeat_disclosures must be disclosure classes sorted and
// each named once ([] allowed), and recipient_told_before a boolean, as
// the engine's Action requires.
func TestTellingMemoryActionShapes(t *testing.T) {
	for field, cases := range map[string]map[string]bool{
		"repeat_disclosures": {`[]`: true, `["phone"]`: true, `["email","phone"]`: true, `["phone","email"]`: false,
			`["phone","phone"]`: false, `["bogus"]`: false, `[""]`: false, `"phone"`: false},
		"recipient_told_before": {`true`: true, `false`: true, `"true"`: false, `1`: false},
	} {
		for value, ok := range cases {
			body := json.RawMessage(`{"verb":"share","operator":"o","developer":"d","timestamp":"2026-10-10T12:00:00Z","` + field + `":` + value + `}`)
			a, err := decodeAction(body, true)
			if (err == nil) != ok {
				t.Errorf("%s %s: %v", field, value, err)
			}
			if ok && field == "repeat_disclosures" && a.RepeatDisclosures == nil {
				t.Errorf("%s %s: dropped", field, value)
			}
		}
	}
}
