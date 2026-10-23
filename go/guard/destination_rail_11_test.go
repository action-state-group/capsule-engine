package guard

import (
	"encoding/json"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"github.com/action-state-group/capsule-engine/go/pack"
)

// destinationRail11Vectors are seller.destination_rail/1.1.0's differential
// vectors (PROVENANCE.json): capsulectl rc20's commit with each rail, alias,
// handover and class, decided by capsule-engine 0c93a58's own
// CONFIGURED_CHECKS["destination_rail"] under the 1.1.0 definition.
var destinationRail11Vectors = filepath.Join("testdata", "destination-rail-11-vectors")

const destinationRail11 = "seller.destination_rail/1.1.0"

type railVector struct {
	releaseVector
	Handover *string `json:"handover"`
}

func destinationRail11Config(t *testing.T) railConfig {
	t.Helper()
	p, err := pack.LoadSeller015()
	if err != nil {
		t.Fatal(err)
	}
	i := slices.IndexFunc(p.Wickets, func(w pack.Wicket) bool { return w.ID == destinationRail11 })
	if i < 0 {
		t.Fatalf("%s cites no %s", p.ID, destinationRail11)
	}
	c, err := decodeConfig[railConfig](p.Wickets[i])
	if err != nil {
		t.Fatal(err)
	}
	return c
}

// museRailWording is the engine's reason as Muse words it: rails and the
// handover without Python's repr quotes, and a list of rails as "a, b", as
// Muse has worded 1.0.0's allowed-rails reason since its port.
func museRailWording(reason string) string {
	reason = strings.ReplaceAll(reason, "'", "")
	return strings.NewReplacer("[", "", "]", "").Replace(reason)
}

// CROSS-IMPLEMENTATION: every vector, the commit read as a live check
// input, with NO known difference from the engine: the handover read, and
// destination_rail's result, reason (in Muse's wording) and evidence.
func TestDestinationRail11AgreesWithTheEngine(t *testing.T) {
	v := decodeJSON[struct {
		Definition       string       `json:"definition"`
		DefinitionDigest string       `json:"definition_digest"`
		Vectors          []railVector `json:"vectors"`
	}](t, readFile(t, destinationRail11Vectors, "expected.json"))
	if v.Definition != destinationRail11 || v.DefinitionDigest != portedDefinitions[destinationRail11] {
		t.Fatalf("vectors under %s %s, ported %s", v.Definition, v.DefinitionDigest, portedDefinitions[destinationRail11])
	}
	c := destinationRail11Config(t)
	results := map[string]int{}
	for _, want := range v.Vectors {
		a, _, err := DecodeCheckInput(readFile(t, destinationRail11Vectors, want.Input))
		if err != nil {
			t.Fatalf("%s: %v", want.Input, err)
		}
		if handoverText(a.Handover) != handoverText(want.Handover) {
			t.Errorf("%s: handover %s, engine %s", want.Input, handoverText(a.Handover), handoverText(want.Handover))
		}
		got := checkDestinationRail(a, c)
		engine := releaseOutcomeOf(t, want.Result, museRailWording(want.Reason), want.Evidence)
		if want.Result != "fail" {
			engine.Reason = ""
		}
		if cell := releaseCell(t, got); cell != engine {
			t.Errorf("%s: Go %+v, engine %+v", want.Input, cell, engine)
		}
		results[want.Result]++
	}
	if len(v.Vectors) != 40 || results["pass"] == 0 || results["fail"] == 0 || results["n/a"] == 0 {
		t.Errorf("%d vectors, %v", len(v.Vectors), results)
	}
}

// 1.0.0 decides and records as before: no alias, no handover, no failure
// for a missing rail, and its evidence has no counted_as.
func TestDestinationRail100IsUnchanged(t *testing.T) {
	c := destinationRail11Config(t)
	c.InPersonRails, c.RailAliases, c.MissingRail = nil, nil, nil
	c.ActionClasses = []string{"marketplace.sale", "agreement.accept"}
	for _, x := range []struct {
		input  string
		result Result
		note   string
	}{
		{"agreement.accept--venmo--no-handover.json", Fail, "rail venmo is not one of the allowed rails card, p2p"},
		{"agreement.accept--cash--in_person.json", Fail, "rail cash is not one of the allowed rails card, p2p"},
		{"agreement.accept--card--no-handover.json", Pass, ""},
		{"agreement.accept--none--no-handover.json", NotApplicable, ""},
	} {
		a, _, err := DecodeCheckInput(readFile(t, destinationRail11Vectors, "inputs", x.input))
		if err != nil {
			t.Fatal(err)
		}
		got := checkDestinationRail(a, c)
		if got.Result != x.result || got.failNote != x.note || slices.Contains(got.reads, "handover") {
			t.Errorf("%s: %s %q reads %v", x.input, got.Result, got.failNote, got.reads)
		}
	}
}

// handoverText is a handover for a message: "<none>" when there is none.
func handoverText(h *string) string {
	if h == nil {
		return "<none>"
	}
	return *h
}

// railRecord is a typed seller commit paying in cash whose body's terms are
// terms, bound to its header as capsulectl binds it.
func railRecord(t *testing.T, terms json.RawMessage) []byte {
	t.Helper()
	agentInput := encodeJSON(t, map[string]json.RawMessage{
		"type": json.RawMessage(`"proposed-action/v0"`), "chain_id": json.RawMessage(`"deal-0000000000000001"`),
		"body": encodeJSON(t, map[string]json.RawMessage{"action": json.RawMessage(`"commit"`),
			"action_class": json.RawMessage(`"agreement.accept"`), "rail": json.RawMessage(`"cash"`), "terms": terms}),
	})
	digest, ok := rawDigest(agentInput)
	if !ok {
		t.Fatal("the agent_input does not digest")
	}
	return encodeJSON(t, map[string]json.RawMessage{
		"operator": json.RawMessage(`"seller"`), "developer": json.RawMessage(`"capsulectl-deal"`),
		"timestamp": json.RawMessage(`"2026-10-10T12:00:00Z"`), "agent_input": agentInput,
		"model_attestation": encodeJSON(t, map[string]json.RawMessage{"compute_attestation": encodeJSON(t,
			map[string]json.RawMessage{"agent_input_digest": encodeJSON(t, digest)})}),
	})
}

// The handover is read only as in_person or shipped (action.py
// handover_of): on a record, from terms.handover, and anything else there
// (another token, a non-string, terms that are not an object) is no
// handover; on --action, another token is no handover, and a non-string is
// refused as every typed --action field is. Cash then fails with the
// handover not stated, its evidence recording none.
func TestTheHandoverIsReadOnlyAsATokenOfTheClosedSet(t *testing.T) {
	c := destinationRail11Config(t)
	for _, x := range []struct {
		name  string
		terms string
		want  string
	}{
		{"in person", `{"handover":"in_person"}`, "in_person"},
		{"shipped", `{"handover":"shipped"}`, "shipped"},
		{"another token", `{"handover":"drone"}`, ""},
		{"not a string", `{"handover":42}`, ""},
		{"an object", `{"handover":{"how":"in_person"}}`, ""},
		{"terms not an object", `"in_person"`, ""},
		{"no terms member", `{}`, ""},
	} {
		a, err := DecodeRecord(railRecord(t, json.RawMessage(x.terms)), "")
		if err != nil {
			t.Fatalf("%s: %v", x.name, err)
		}
		if handoverText(a.Handover) != handoverText(&x.want) && !(a.Handover == nil && x.want == "") {
			t.Errorf("%s: handover %s, want %q", x.name, handoverText(a.Handover), x.want)
		}
		got := checkDestinationRail(a, c)
		if wantPass := x.want == "in_person"; (got.Result == Pass) != wantPass {
			t.Errorf("%s: cash %s %q", x.name, got.Result, got.failNote)
		}
		if x.want == "" && got.failNote != "rail cash is allowed only when the item is handed over in person; the handover is not stated" {
			t.Errorf("%s: %q", x.name, got.failNote)
		}
	}
	action := func(handover string) ([]byte, error) {
		b := []byte(`{"verb":"commit","operator":"seller","developer":"capsulectl-deal","timestamp":"2026-10-10T12:00:00Z",` +
			`"action_class":"agreement.accept","rail":"cash","handover":` + handover + `}`)
		return b, nil
	}
	for _, x := range []struct {
		handover, want string
		refused        bool
	}{{`"in_person"`, "in_person", false}, {`"drone"`, "", false}, {`42`, "", true}} {
		body, _ := action(x.handover)
		a, err := decodeAction(body, true)
		if x.refused {
			if err == nil {
				t.Errorf("--action handover %s: decoded", x.handover)
			}
			continue
		}
		if err != nil {
			t.Fatalf("--action handover %s: %v", x.handover, err)
		}
		if (a.Handover == nil) != (x.want == "") || a.Handover != nil && *a.Handover != x.want {
			t.Errorf("--action handover %s: %s", x.handover, handoverText(a.Handover))
		}
	}
}
