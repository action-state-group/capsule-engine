package guard

import (
	"errors"
	"io/fs"
	"path"
	"strings"
	"testing"

	"gopkg.in/yaml.v3"

	"github.com/action-state-group/capsule-engine/go/packs"
)

// vendoredGate is the selectors node of the vendored action_class_gate/1.0.0
// definition.
func vendoredGate(t *testing.T) yaml.Node {
	t.Helper()
	raw, err := fs.ReadFile(packs.Everyday037, path.Join(packs.Everyday037Dir, "wickets", "action_class_gate.yaml"))
	if err != nil {
		t.Fatal(err)
	}
	var def struct {
		WicketID string     `yaml:"wicket_id"`
		Config   gateConfig `yaml:"config"`
	}
	if err := yaml.Unmarshal(raw, &def); err != nil {
		t.Fatal(err)
	}
	if def.WicketID != "action_class_gate/1.0.0" {
		t.Fatalf("vendored %s", def.WicketID)
	}
	return def.Config.Selectors
}

func selectorsYAML(t *testing.T, src string) yaml.Node {
	t.Helper()
	var doc yaml.Node
	if err := yaml.Unmarshal([]byte(src), &doc); err != nil {
		t.Fatal(err)
	}
	return *doc.Content[0]
}

func gateOf(t *testing.T, class *string, node yaml.Node) Constraint {
	t.Helper()
	tax, err := engineTaxonomy()
	if err != nil {
		t.Fatal(err)
	}
	c, err := checkActionClassGate(Action{Verb: "v", Operator: "o", Developer: "d", ActionClass: class}, node, tax)
	if err != nil {
		t.Fatal(err)
	}
	return c
}

func strp(s string) *string { return &s }

// (iv) The six shipped selectors, exactly: what each one matches and what a
// match produces.
func TestGateShipsTheSixSelectors(t *testing.T) {
	tax, err := engineTaxonomy()
	if err != nil {
		t.Fatal(err)
	}
	sel, err := parseSelectors(vendoredGate(t), tax)
	if err != nil {
		t.Fatal(err)
	}
	f := false
	want := map[string]gateSelector{
		"non_consequential":    {consequential: &f, onMatch: "pass"},
		"public_posting":       {actionClasses: []string{"communication.publish"}, onMatch: "fail"},
		"booking_create":       {actionClasses: []string{"booking.create"}, onMatch: "fail"},
		"booking_cancel":       {actionClasses: []string{"booking.cancel"}, onMatch: "fail"},
		"destructive_mutation": {triggerClasses: []string{"MUTATE"}, onMatch: "fail"},
		"personal_disclosure":  {triggerClasses: []string{"DISCLOSE"}, onMatch: "fail"},
	}
	if len(sel) != len(want) {
		t.Fatalf("%d selectors, want %d", len(sel), len(want))
	}
	for id, w := range want {
		g, ok := sel[id]
		if !ok || g.onMatch != w.onMatch || strings.Join(g.actionClasses, ",") != strings.Join(w.actionClasses, ",") ||
			strings.Join(g.triggerClasses, ",") != strings.Join(w.triggerClasses, ",") || (g.consequential == nil) != (w.consequential == nil) ||
			(g.consequential != nil && *g.consequential != *w.consequential) {
			t.Errorf("selector %s = %+v, want %+v", id, g, w)
		}
	}
	for class, result := range map[string]Result{
		"info.query": Pass, "communication.publish": Fail, "booking.create": Fail, "booking.cancel": Fail,
		"data.delete": Fail, "disclosure.personal": Fail, "money.purchase": NotApplicable, "communication.send": NotApplicable,
	} {
		if c := gateOf(t, strp(class), vendoredGate(t)); c.Result != result {
			t.Errorf("%s: %s, want %s", class, c.Result, result)
		}
	}
}

// (i) No declared class, or a class with no row, fails closed.
func TestGateFailsClosedOffTheTable(t *testing.T) {
	for name, class := range map[string]*string{"no class": nil, "unknown class": strp("money.teleport")} {
		c := gateOf(t, class, vendoredGate(t))
		if c.Result != Fail {
			t.Errorf("%s: %s, want fail", name, c.Result)
		}
		if d, ok, err := c.EvidenceDigest(); !ok || err != nil || d == "" {
			t.Errorf("%s: no evidence digest", name)
		}
	}
}

// (ii) A selector the table cannot resolve is an error from the decision
// that reads it, never a load failure and never a quiet non-match.
func TestGateBadSelectorFailsTheDecisionNotTheLoad(t *testing.T) {
	e, _ := everydayEngine(t)
	for name, src := range map[string]string{
		"unknown class":   "x: {action_classes: [money.teleport], on_match: fail}",
		"CHANGE trigger":  "x: {trigger_classes: [CHANGE], on_match: fail}",
		"no on_match":     "x: {action_classes: [booking.create]}",
		"selects nothing": "x: {on_match: fail}",
		"unknown key":     "x: {action_class: [booking.create], on_match: fail}",
	} {
		t.Run(name, func(t *testing.T) {
			e.order = []string{"action_class_gate"}
			e.gate = selectorsYAML(t, src)
			a := Action{Verb: "v", Operator: "o", Developer: "d", ActionID: "v/1", ActionType: "decide", Timestamp: "2026-10-07T00:00:00Z", ActionClass: strp("booking.create")}
			if _, err := e.Check(a, nil); !errors.Is(err, errBadSelectors) {
				t.Fatalf("Check: %v, want a selectors error", err)
			}
		})
	}
}

// (iii) A legacy alias matches as its canonical class. The selector passes
// its class, so only resolving the alias passes it: an unresolved name
// would fail closed.
func TestGateResolvesLegacyAliases(t *testing.T) {
	node := selectorsYAML(t, "send: {action_classes: [communication.send], on_match: pass}")
	if c := gateOf(t, strp("comms.external"), node); c.Result != Pass {
		t.Fatalf("comms.external: %s, want pass as communication.send", c.Result)
	}
}
